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
from app.database import get_session
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


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: SessionDep,
) -> User:
    """Пользователь из access token.

    Проверяется подпись, срок действия, издатель и аудитория — всё в
    decode_access_token. Затем пользователь перечитывается из БД: если его
    удалили, токен перестаёт работать, хотя подпись ещё валидна.
    """
    if credentials is None or not credentials.credentials:
        raise _unauthorized("Not authenticated")
    try:
        user_id = decode_access_token(credentials.credentials)
    except InvalidToken as exc:
        raise _unauthorized() from exc

    user = auth_service.get_user_by_id(session, user_id)
    if user is None:
        # Токен подписан нами, но пользователя больше нет.
        raise _unauthorized()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


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

    Маршрут существует как рабочая проверка защищённого доступа: он
    отвечает 200 только с валидным токеном и 401 во всех остальных
    случаях (нет заголовка, битая подпись, истёк срок, удалённый
    пользователь). Он же демонстрирует схему HTTPBearer в OpenAPI.
    """
    return current_user
