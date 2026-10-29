# Маршруты аутентификации (M3: регистрация, вход и проверка токена).
#
# Контракт входа — JSON {"email", "password"} → {"access_token", "token_type"}.
# Это НЕ OAuth2 password form: в Swagger нельзя нажать Authorize и войти
# автоматически. Порядок такой: вызвать /auth/login, скопировать
# access_token и вставить его в Authorize (схема HTTPBearer).

from fastapi import APIRouter, HTTPException, status

from app.core.security import create_access_token
from app.dependencies import WWW_AUTHENTICATE, AdminUser, CurrentUser, SessionDep
from app.schemas.auth import TokenResponse, UserLogin, UserRead, UserRegister
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


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
    валидации значение `password` замаскировано обработчиком в app/app_factory.py.

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
