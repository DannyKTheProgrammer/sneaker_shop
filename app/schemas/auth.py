# Схемы аутентификации. Пароль только во входных схемах, в ответе его нет.

from typing import Any, Annotated

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
)

from app.core.security import PASSWORD_MAX_BYTES, PASSWORD_MIN_LENGTH, validate_password


def _normalize_email(value: Any) -> Any:
    """Нормализовать email (strip + lower), чтобы UNIQUE(email) не зависел от регистра."""
    return value.strip().lower() if isinstance(value, str) else value


def _validate_password(value: Any) -> Any:
    """Отклонить непригодный пароль до хэширования (логика в security.validate_password).

    NUL нужно отсечь здесь: иначе passlib поднимет ошибку и будет 500 вместо 422.
    """
    if not isinstance(value, str):
        return value
    return validate_password(value)


PasswordField = Annotated[
    str,
    Field(min_length=PASSWORD_MIN_LENGTH, max_length=72),
    BeforeValidator(_validate_password),
]
EmailField = Annotated[EmailStr, BeforeValidator(_normalize_email)]


class UserRead(BaseModel):
    """Пользователь в ответе API; hashed_password намеренно отсутствует."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    is_admin: bool


class UserRegister(BaseModel):
    """Вход POST /auth/register; лишние поля (например, is_admin) дают 422."""

    model_config = ConfigDict(extra="forbid")

    email: EmailField
    password: PasswordField


class UserLogin(BaseModel):
    """Вход POST /auth/login; email нормализуется так же, как при регистрации."""

    model_config = ConfigDict(extra="forbid")

    email: EmailField
    password: PasswordField


class TokenResponse(BaseModel):
    """Ответ входа: access token и данные пользователя (в самом JWT их нет)."""

    access_token: str
    token_type: str
    user: UserRead
