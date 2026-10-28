# Маршруты аутентификации (M3: регистрация, вход и проверка токена).
#
# Контракт входа — JSON {"email", "password"} → {"access_token", "token_type"}.
# Это НЕ OAuth2 password form: в Swagger нельзя нажать Authorize и войти
# автоматически. Порядок такой: вызвать /auth/login, скопировать
# access_token и вставить его в Authorize (схема HTTPBearer).

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import InvalidToken, create_access_token, decode_access_token
from app.database import SessionLocal, get_session
from app.models import User
from app.schemas.auth import TokenResponse, UserLogin, UserRead, UserRegister
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

SessionDep = Annotated[Session, Depends(get_session)]

# Схема безопасности для OpenAPI: поле для токена в Authorize.
# auto_error=False — чтобы отсутствие заголовка давало наш 401 с
# WWW-Authenticate, а не встроенный 403 без пояснений.
bearer_scheme = HTTPBearer(auto_error=False, description="Вставьте access_token из /auth/login")

# Один и тот же заголовок для всех отказов доступа.
WWW_AUTHENTICATE = {"WWW-Authenticate": "Bearer"}


def _unauthorized(detail: str = "Could not validate credentials") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers=WWW_AUTHENTICATE,
    )


def _forbidden() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Administrator privileges required",
    )


BearerCredentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


def _user_from_token(credentials: HTTPAuthorizationCredentials | None, session: Session) -> User:
    """Проверить Bearer-токен и прочитать его пользователя в переданной сессии.

    Общая часть get_current_user и зависимостей «только id» ниже: отличаются
    они лишь тем, чья это сессия и когда она закрывается.
    """
    if credentials is None or not credentials.credentials:
        raise _unauthorized("Not authenticated")
    try:
        user_id = decode_access_token(credentials.credentials)
    except InvalidToken as exc:
        raise _unauthorized() from exc

    user = auth_service.get_user_by_id(session, user_id)
    if user is None:
        raise _unauthorized()
    return user


def get_current_user(credentials: BearerCredentials, session: SessionDep) -> User:
    """Пользователь из access token.

    Шаги:

    1. Заголовок `Authorization: Bearer <token>` разбирает HTTPBearer с
       auto_error=False. Нет заголовка или он не Bearer → 401 от нас (а не
       встроенный 403), всегда с WWW-Authenticate: Bearer.
    2. Подпись, срок, iss, aud и формат sub проверяет
       core.security.decode_access_token. Декодированному payload мы не
       доверяем: из него берётся ТОЛЬКО id пользователя.
    3. Пользователь читается из БД по этому id. Возвращается настоящая
       строка User, а не то, что написано в токене: роль, email и факт
       существования берутся из базы и потому не могут быть подделаны
       содержимым токена. Нет строки → 401 (токен подписан нами, но
       пользователь удалён).

    Побочный эффект, который нам и нужен: изменение роли в БД действует
    на следующем же запросе с уже выданным токеном — перевыпуск не нужен.

    Сессия здесь — из Depends(get_session) и живёт до конца запроса. Это
    годится там, где той же сессией дальше работает сам маршрут (/auth/me,
    запись каталога). Для корзины и заказов — см. get_current_user_id.
    """
    return _user_from_token(credentials, session)


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_current_user_id(credentials: BearerCredentials) -> int:
    """Только id пользователя из проверенного токена (корзина и заказы).

    Наружу уходит не ORM-объект, а целое число: бизнес-операции корзины и
    checkout работают в СВОЕЙ свежей сессии (см. app/services/shop.py), и
    передавать туда объект из чужой сессии нельзя — чтение его атрибутов
    может пойти в старую транзакцию со старым снимком.

    Пользователь читается в КОРОТКОЙ сессии, которая закрывается до вызова
    сервиса: close() откатывает её неявную транзакцию и возвращает соединение
    в пул. Раньше здесь была сессия из Depends(get_session), и она держала
    второе соединение «idle in transaction» до конца запроса. Под параллельной
    нагрузкой запросы занимали весь пул первыми соединениями и падали 500 по
    pool_timeout, ожидая второе (дефект D1). Теперь в каждый момент запрос
    держит не больше одного соединения.

    Проверка токена при этом не ослабляется: id берётся из строки users,
    прочитанной из БД по sub токена, то есть удалённый пользователь получит
    401 ещё здесь. Блокировку строки владельца сервис берёт заново, в своей
    транзакции.
    """
    with SessionLocal() as session:
        return _user_from_token(credentials, session).id


def get_current_admin_id(credentials: BearerCredentials) -> int:
    """id администратора из проверенного токена; иначе 401/403.

    То же, что get_current_user_id, плюс проверка роли — для маршрутов, где
    дальше работает сервис в своей сессии (PATCH /orders/{id}/status).
    Сессия закрывается до вызова сервиса, а сервис перечитывает роль под
    users FOR SHARE в своей транзакции: проверка здесь — ранний отказ 403,
    решающая — там.
    """
    with SessionLocal() as session:
        user = _user_from_token(credentials, session)
        if user.is_admin is not True:
            raise _forbidden()
        return user.id


def require_admin(current_user: CurrentUser) -> User:
    """Пропустить только администратора (иначе 403).

    Роль берётся из строки пользователя, прочитанной из БД на этом же
    запросе, а не из claims токена: иначе достаточно было бы один раз
    получить админский токен или подделать claim, чтобы права остались
    навсегда. Здесь же снятие роли действует сразу.

    Проверяется строго `is True`: если колонка когда-нибудь окажется NULL
    (схема разошлась с ожиданием), доступ всё равно будет закрыт.
    """
    if current_user.is_admin is not True:
        raise _forbidden()
    return current_user


AdminUser = Annotated[User, Depends(require_admin)]


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Регистрация пользователя",
    responses={
        409: {"description": "Нормализованный email уже занят"},
        422: {"description": "Невалидные данные или лишние поля"},
    },
)
def register(payload: UserRegister, session: SessionDep) -> UserRead:
    """Зарегистрировать пользователя и вернуть его безопасные поля.

    Принимаются только `email` и `password`. Email нормализуется
    (strip + lower) до проверок и до записи. Пароль проверяется по длине
    в символах и в байтах UTF-8; NUL-символы запрещены. Пароль никогда не
    возвращается: ответ — `UserRead` (id, email, is_admin), а в ошибках
    валидации значение `password` замаскировано обработчиком в app.main.

    Занятый email → 409. Проверка опирается на UNIQUE(email) в БД, а не на
    предварительный SELECT: при гонке двух одновременных регистраций
    вторая получит отказ от базы, и он будет переведён в тот же 409.
    """
    try:
        return auth_service.register_user(session, payload)
    except auth_service.EmailAlreadyRegistered as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        ) from exc


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Вход по email и паролю",
    responses={
        401: {"description": "Неверная пара email/пароль"},
        422: {"description": "Невалидные данные или лишние поля"},
    },
)
def login(payload: UserLogin, session: SessionDep) -> TokenResponse:
    """Проверить пару email/пароль и выдать access token.

    Пароль сверяется с сохранённым хэшем через `verify_password` (соль
    внутри хэша, поэтому повторное вычисление и сравнение строк не
    используется). Если email не найден, выполняется такая же проверка
    против dummy_hash, и ответ тот же самый — 401 без подробностей: по
    ответу нельзя понять, существует ли такой адрес.

    Успех: 200 и JSON `{"access_token": "<jwt>", "token_type": "bearer",
    "user": {...}}`. Токен подписан HS256, живёт 15 минут, sub = id
    пользователя, iss = sneaker-practice, aud = sneaker-api. Пароля, хэша
    и признака роли в токене нет.

    Это не OAuth2 password flow: Swagger не умеет логиниться этой формой.
    Сначала вызовите /auth/login, затем вставьте access_token в Authorize.
    """
    try:
        user = auth_service.authenticate_user(session, payload)
    except auth_service.InvalidCredentials as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers=WWW_AUTHENTICATE,
        ) from exc

    return TokenResponse(
        access_token=create_access_token(user.id),
        token_type="bearer",
        user=UserRead.model_validate(user),
    )


@router.get(
    "/me",
    response_model=UserRead,
    summary="Текущий пользователь по access token",
    responses={401: {"description": "Токен отсутствует, просрочен или подделан"}},
)
def read_me(current_user: CurrentUser) -> UserRead:
    """Вернуть пользователя, которому принадлежит предъявленный токен.

    Ответ — UserRead: id, email, is_admin. Пароля и хэша здесь нет ни в
    каком виде.

    Маршрут существует как рабочая проверка защищённого доступа: 200 только
    с валидным токеном, 401 во всех остальных случаях (нет заголовка,
    битая подпись, истёк срок, чужой aud, удалённый пользователь). Он же
    демонстрирует схему HTTPBearer в OpenAPI.
    """
    return current_user


@router.get(
    "/admin-check",
    response_model=UserRead,
    summary="Проверка require_admin (только администратор)",
    responses={
        401: {"description": "Токен отсутствует или недействителен"},
        403: {"description": "Пользователь не администратор"},
    },
)
def admin_check(admin: AdminUser) -> UserRead:
    """Эндпоинт-проверка зависимости require_admin.

    Нужен, чтобы приёмка M3 могла убедиться: обычный пользователь получает
    403, администратор — 200, а смена роли в БД действует на следующем
    запросе с уже выданным токеном. Это только проверка доступа — никаких
    изменений каталога здесь нет (маршруты записи появятся отдельно).
    """
    return admin
