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

# Ограничения bcrypt: он принимает пароль строкой, но использует только
# первые 72 БАЙТА UTF-8. Кириллица в UTF-8 занимает 2 байта на символ,
# поэтому 72 символа — это ещё не 72 байта. Проверяем и то, и другое,
# иначе bcrypt молча обрежет пароль (или с truncate_error=True упадёт
# невнятной ошибкой уже внутри хэширования).
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_BYTES = 72


def _normalize_email(value: Any) -> Any:
    """Нормализация email: strip + lower — выбранная политика проекта.

    Нужна, чтобы один и тот же адрес в разном регистре не создавал двух
    пользователей. В БД попадает уже нормализованное значение, поэтому
    UNIQUE(email) защищает от дублей с точностью до регистра.
    """
    return value.strip().lower() if isinstance(value, str) else value


def _validate_password(value: Any) -> Any:
    """Проверить длину в символах и в байтах UTF-8, запретить NUL.

    NUL-байт нельзя пропускать: строка обрывается на нём в C-библиотеках
    (bcrypt в их числе), и пароль «abc\\x00def» вёл бы себя как «abc».
    """
    if not isinstance(value, str):
        return value  # тип проверит Pydantic своими сообщениями
    if len(value) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"password must be at least {PASSWORD_MIN_LENGTH} characters")
    if len(value.encode("utf-8")) > PASSWORD_MAX_BYTES:
        raise ValueError(
            f"password must be at most {PASSWORD_MAX_BYTES} bytes in UTF-8 "
            "(bcrypt uses only the first 72 bytes)"
        )
    if "\x00" in value:
        raise ValueError("password must not contain NUL characters")
    return value


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

    email: Annotated[EmailStr, BeforeValidator(_normalize_email)]
    # max_length=72 символов — грубая рамка, точную (по байтам) делает
    # _validate_password.
    password: Annotated[
        str,
        Field(min_length=PASSWORD_MIN_LENGTH, max_length=72),
        BeforeValidator(_validate_password),
    ]
