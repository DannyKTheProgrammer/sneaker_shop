# Пароли: хэширование и проверка (M3).
#
# Здесь только криптография пароля — без HTTP, без БД и без моделей.
# Так этот модуль можно использовать и в регистрации, и во входе, и в
# служебных скриптах, не подтягивая за собой сервисы.
#
# Про соль. bcrypt сам генерирует случайную соль и хранит её внутри строки
# хэша (формат $2b$12$<соль><хеш>). Поэтому два хэша ОДНОГО пароля всегда
# разные, и сравнивать вычисленный хэш с сохранённой строкой бессмысленно —
# сравнение совпадёт только случайно. Правильный способ один:
# verify_password(введённый_пароль, сохранённый_хэш) — passlib сам достанет
# соль из сохранённой строки и повторит вычисление.

from datetime import UTC, datetime, timedelta
from functools import lru_cache

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

# bcrypt__rounds=12 — стоимость (2^12 итераций), фиксируем явно, чтобы она
# не «поехала» от обновления библиотеки.
# bcrypt__truncate_error=True — при пароле длиннее 72 байт passlib поднимет
# ошибку, а не обрежет пароль молча.
pwd_context = CryptContext(
    schemes=["bcrypt"],
    bcrypt__rounds=12,
    bcrypt__truncate_error=True,
)

# Ограничения bcrypt: используется только первый 71 байт + NUL-терминатор,
# то есть 72 байта — предел. Для UTF-8 это не равно 72 символам: кириллица
# занимает 2 байта на символ.
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_BYTES = 72


class PasswordTooLong(ValueError):
    """Пароль длиннее предела bcrypt — хэшировать нельзя."""


class PasswordContainsNul(ValueError):
    """В пароле есть NUL-байт: строка оборвалась бы на нём."""


def validate_password(password: str) -> str:
    """Проверить пароль ДО хэширования и вернуть его же.

    Проверки те же, что в схеме регистрации, и держатся здесь, чтобы
    «отклонить до хэширования» нельзя было обойти, вызвав hash_password
    напрямую из скрипта или будущего кода: тихого усечения быть не должно.
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
    """Хэш пароля (bcrypt со случайной солью).

    Открытый пароль никуда не сохраняется и не логируется: функция
    возвращает только строку хэша.
    """
    return pwd_context.hash(validate_password(password))


def verify_password(password: str, password_hash: str) -> bool:
    """Проверить пароль против сохранённого хэша.

    Именно так и только так: соль берётся из password_hash. Сравнивать
    заново вычисленный хэш со строкой нельзя — соль random, строки разные.

    При неверном пароле или битом хэше возвращает False, а не исключение:
    решение «что делать с False» принимает вызывающий код.
    """
    if not password or not password_hash:
        return False
    try:
        return pwd_context.verify(password, password_hash)
    except ValueError:
        # Битая строка хэша или несовместимый формат: это не «пароль верный».
        return False


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """Хэш-заглушка для несуществующих пользователей.

    Вычисляется один раз (при первом обращении) из вымышленного локального
    пароля. Нужен при входе: если email не найден, мы всё равно выполняем
    ту же по стоимости проверку пароля против заглушки, чтобы «нет такого
    email» и «неверный пароль» обрабатывались одинаково и не различались
    по времени ответа сколько-нибудь заметно.

    Это снижение наблюдаемой разницы, а не гарантия постоянного времени:
    на время ответа влияют сеть, БД и загрузка машины. Обещать идеальный
    constant-time здесь нельзя.
    """
    return pwd_context.hash("dummy-password-not-used-anywhere")


def dummy_hash() -> str:
    """Публичный доступ к хэшу-заглушке (нужен сервису входа)."""
    return _dummy_hash()


# --- JWT (M3) -------------------------------------------------------------
#
# Токен подписывается HS256 секретом из настроек (JWT_SECRET в .env или
# окружении). Запасного ключа в коде нет: если секрет не задан, приложение
# не стартует — это лучше, чем молча подписывать токены известным всем
# ключом.
#
# В claims кладём только то, что нужно для доступа: sub (id пользователя),
# exp, iat, iss, aud. Пароля, его хэша и признака роли там нет: токен —
# не место для секретов, а роль всё равно проверяется по БД при каждом
# запросе, поэтому устаревший токен не даст лишних прав.


class InvalidToken(Exception):
    """Токен отсутствует, просрочен, подделан или относится к другому приложению."""


# Диапазон типа integer в PostgreSQL (int4). sub должен попадать в него,
# иначе поиск по id либо не найдёт пользователя, либо упадёт на приведении
# типа — и то и другое должно быть 401, а не 500.
POSTGRES_INT_MIN = -2_147_483_648
POSTGRES_INT_MAX = 2_147_483_647

# Claims, без которых токен не считается полным.
REQUIRED_CLAIMS = ("exp", "sub", "iss", "aud")


def create_access_token(user_id: int, expires_delta: timedelta | None = None) -> str:
    """Подписать access token для пользователя.

    expires_delta можно передать явно — это нужно проверкам срока действия
    (например, токен, который истекает через секунду).
    """
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
    """Проверить подпись и claims и вернуть id пользователя из sub.

    Порядок проверок и почему так:

    1. Подпись. jwt.decode с явным algorithms=["HS256"] — алгоритм не
       берётся из заголовка токена, иначе атакующий мог бы подсунуть
       "alg": "none" или другой алгоритм. Ключ — только из настроек.
    2. iss и aud: передаются в decode, поэтому чужой издатель или чужое
       приложение не проходят.
    3. exp: python-jose требует его наличия и проверяет срок; токен без exp
       отвергается (это в REQUIRED_CLAIMS).
    4. Обязательные claims проверяются явно по списку.
    5. sub: строка, состоящая ТОЛЬКО из ASCII-цифр. str.isdigit() здесь не
       годится — он пропускает Unicode-цифры вроде '١٢', которые int()
       потом отвергнет (или, хуже, примет не так, как ожидается).
    6. sub должен влезать в int4 PostgreSQL: слишком большое число не
       должно превращаться в 500 при запросе к БД.

    Декодированному payload не доверяем ни в чём, кроме этих проверенных
    полей: никакой роли, email или прав оттуда не берётся — за ними
    обращаемся в БД.
    """
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],   # явный список, не из токена
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
        )
    except JWTError as exc:
        # Сюда попадают: битая подпись, истёкший exp, отсутствующий exp,
        # чужой iss/aud, неразбираемый токен.
        raise InvalidToken("access token is invalid or expired") from exc

    missing = [claim for claim in REQUIRED_CLAIMS if claims.get(claim) in (None, "")]
    if missing:
        raise InvalidToken(f"access token misses required claims: {', '.join(missing)}")

    subject = claims["sub"]
    if not isinstance(subject, str) or not subject.isascii() or not subject.isdigit():
        # isascii + isdigit: только '0'-'9', без знаков, пробелов и
        # Unicode-цифр.
        raise InvalidToken("access token subject is not a numeric user id")

    user_id = int(subject)
    if not (POSTGRES_INT_MIN <= user_id <= POSTGRES_INT_MAX):
        raise InvalidToken("access token subject is out of range for a user id")
    return user_id
