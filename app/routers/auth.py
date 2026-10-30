# Регистрация, вход и проверка токена.
# Вход — JSON, не OAuth2 password form: токен из /auth/login вставляется в Authorize вручную.

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
    """Зарегистрировать пользователя.

    Занятый email даёт 409; проверка опирается на UNIQUE(email), поэтому гонка тоже даёт 409.
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
    """Проверить email/пароль и выдать access token.

    Для неизвестного email и неверного пароля ответ одинаков: 401.
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
    """Вернуть владельца токена; без валидного токена 401."""
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
    """Проверить доступ администратора: 403 для обычного пользователя."""
    return admin
