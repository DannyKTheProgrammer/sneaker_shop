# Пароли (bcrypt) и JWT, без HTTP и БД.
# Соль случайная и хранится в хэше: сравнивать только через verify_password.

from datetime import UTC, datetime, timedelta
from functools import lru_cache

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

# rounds фиксирован явно; truncate_error запрещает молчаливое усечение пароля.
pwd_context = CryptContext(
    schemes=["bcrypt"],
    bcrypt__rounds=12,
    bcrypt__truncate_error=True,
)

# Предел bcrypt задан в байтах, не в символах (кириллица: 2 байта на символ).
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_BYTES = 72


class PasswordTooLong(ValueError):
    """Пароль длиннее предела bcrypt."""


class PasswordContainsNul(ValueError):
    """В пароле есть NUL-байт."""


def validate_password(password: str) -> str:
    """Проверить пароль до хэширования и вернуть его.

    Дублирует проверки схемы, чтобы их нельзя было обойти прямым вызовом hash_password.
    """
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"password must be at least {PASSWORD_MIN_LENGTH} characters")
    if len(password.encode("utf-8")) > PASSWORD_MAX_BYTES:
        raise PasswordTooLong(
            f"password must be at most {PASSWORD_MAX_BYTES} bytes in UTF-8 "
            "(bcrypt uses only the first 72 bytes)"
        )
    if "\x00" in password:
        raise PasswordContainsNul("password must not contain NUL characters")
    return password


def hash_password(password: str) -> str:
    """Вернуть bcrypt-хэш пароля."""
    return pwd_context.hash(validate_password(password))


def verify_password(password: str, password_hash: str) -> bool:
    """Проверить пароль против хэша; при неверном пароле или битом хэше вернуть False."""
    if not password or not password_hash:
        return False
    try:
        return pwd_context.verify(password, password_hash)
    except ValueError:
        return False


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """Хэш-заглушка для входа с несуществующим email.

    Выравнивает время ответа с неверным паролем, но не гарантирует constant-time.
    """
    return pwd_context.hash("dummy-password-not-used-anywhere")


def dummy_hash() -> str:
    """Вернуть хэш-заглушку."""
    return _dummy_hash()


# --- JWT ------------------------------------------------------------------
# Claims: sub, exp, iat, iss, aud. Роли в токене нет: она проверяется по БД.


class InvalidToken(Exception):
    """Токен отсутствует, просрочен, подделан или относится к другому приложению."""


# Диапазон int4 PostgreSQL: sub вне него должен давать 401, а не 500.
POSTGRES_INT_MIN = -2_147_483_648
POSTGRES_INT_MAX = 2_147_483_647

REQUIRED_CLAIMS = ("exp", "sub", "iss", "aud")


def create_access_token(user_id: int, expires_delta: timedelta | None = None) -> str:
    """Подписать access token; expires_delta переопределяет срок."""
    now = datetime.now(UTC)
    lifetime = expires_delta or timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + lifetime).timestamp()),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    return jwt.encode(
        payload,
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str) -> int:
    """Проверить подпись и claims токена и вернуть id пользователя из sub.

    Алгоритм берётся из настроек, не из заголовка токена. sub должен быть
    ASCII-цифрами в пределах int4. Любой отказ — InvalidToken.
    """
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
        )
    except JWTError as exc:
        raise InvalidToken("access token is invalid or expired") from exc

    missing = [claim for claim in REQUIRED_CLAIMS if claims.get(claim) in (None, "")]
    if missing:
        raise InvalidToken(f"access token misses required claims: {', '.join(missing)}")

    subject = claims["sub"]
    if not isinstance(subject, str) or not subject.isascii() or not subject.isdigit():
        # isdigit() пропускает Unicode-цифры, поэтому ещё и isascii().
        raise InvalidToken("access token subject is not a numeric user id")

    user_id = int(subject)
    if not (POSTGRES_INT_MIN <= user_id <= POSTGRES_INT_MAX):
        raise InvalidToken("access token subject is out of range for a user id")
    return user_id
