# Схемы аутентификации (M3).
#
# Отдельный модуль от каталога: здесь только то, что относится к регистрации
# и пользователю. Пароль — поле ВХОДА и никогда не поле ответа: наружу
# уходит UserRead, в котором хэша нет вообще.

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
    """Нормализация email: strip + lower — выбранная политика проекта.

    Нужна, чтобы один и тот же адрес в разном регистре не создавал двух
    пользователей. В БД попадает уже нормализованное значение, поэтому
    UNIQUE(email) защищает от дублей с точностью до регистра.
    """
    return value.strip().lower() if isinstance(value, str) else value


def _validate_password(value: Any) -> Any:
    """Отклонить непригодный пароль ДО хэширования.

    Сама логика — в app/core/security.validate_password, чтобы правило было
    одно на весь проект: длина в символах, длина в байтах UTF-8 (предел
    bcrypt) и запрет NUL. Здесь только адаптация под валидатор Pydantic.

    Про NUL: он не «просто некрасивый символ» — строка обрывается на нём в
    C-библиотеках (bcrypt в их числе), а passlib на таком пароле поднимает
    PasswordValueError, что без нашей проверки дало бы 500 вместо 422.
    """
    if not isinstance(value, str):
        return value  # тип проверит Pydantic своими сообщениями
    return validate_password(value)


# Переиспользуемые аннотированные типы для полей пароля.
PasswordField = Annotated[
    str,
    Field(min_length=PASSWORD_MIN_LENGTH, max_length=72),
    BeforeValidator(_validate_password),
]
EmailField = Annotated[EmailStr, BeforeValidator(_normalize_email)]


class UserRead(BaseModel):
    """Пользователь в ответе API: только безопасные поля.

    hashed_password здесь отсутствует намеренно: он не должен покидать
    пределы процесса даже в виде хэша — клиенту он не нужен.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    is_admin: bool


class UserRegister(BaseModel):
    """Вход POST /auth/register: только email и password.

    extra="forbid" — попытка прислать is_admin, id или что-то ещё даёт 422,
    а не молчаливое игнорирование: иначе клиент решил бы, что создал себе
    администратора.
    """

    model_config = ConfigDict(extra="forbid")

    email: EmailField
    password: PasswordField


class UserLogin(BaseModel):
    """Вход POST /auth/login: те же два поля и та же нормализация email.

    Именно поэтому вход ищет пользователя по нормализованному адресу:
    регистрация записала ровно такое значение.
    """

    model_config = ConfigDict(extra="forbid")

    email: EmailField
    password: PasswordField


class TokenResponse(BaseModel):
    """Ответ входа: access token и безопасные поля пользователя.

    token_type = "bearer" — так клиент понимает, что токен кладётся в
    заголовок `Authorization: Bearer <access_token>`.

    Поле user оставлено для удобства (клиенту не нужен отдельный запрос,
    чтобы узнать id и email), но оно не часть токена: в самом JWT лежат
    только sub/exp/iat/iss/aud — без пароля, хэша и признака роли.
    """

    access_token: str
    token_type: str
    user: UserRead
