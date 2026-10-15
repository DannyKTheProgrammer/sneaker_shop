# Регистрация пользователей: хэширование пароля и запись в БД (M3).
#
# Здесь нет ни FastAPI, ни HTTP-кодов: сервис отвечает за пароль и за
# строку в таблице users, а «201/409/422» решает роутер.

from passlib.context import CryptContext
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import User
from app.schemas.auth import UserRegister

# bcrypt__rounds=12 — стоимость хэширования (2^12 итераций): подобрать
# пароль перебором заметно дороже, чем при стандартных 12 в старых
# версиях... значение фиксируем явно, чтобы оно не «поехало» от апгрейда
# библиотеки.
# bcrypt__truncate_error=True — если пароль длиннее 72 байт, passlib
# поднимет ошибку, а не обрежет его молча. Схему длины мы уже проверили в
# Pydantic, это вторая линия защиты.
pwd_context = CryptContext(
    schemes=["bcrypt"],
    bcrypt__rounds=12,
    bcrypt__truncate_error=True,
)

# SQLSTATE нарушения уникальности в PostgreSQL.
UNIQUE_VIOLATION = "23505"


class AuthError(Exception):
    """Ошибка регистрации, которую роутер переводит в HTTP-ответ."""


class EmailAlreadyRegistered(AuthError):
    """Нормализованный email уже занят."""


def hash_password(password: str) -> str:
    """Хэш пароля (bcrypt). Открытый пароль нигде не сохраняется."""
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Проверить пароль против хэша.

    Нужна для будущего входа и для проверок: «хэш в БД действительно от
    этого пароля». При неверном пароле или битом хэше возвращает False,
    а не исключение.
    """
    try:
        return pwd_context.verify(password, password_hash)
    except ValueError:
        return False


def _is_unique_violation(exc: IntegrityError) -> bool:
    """Нарушение уникальности (23505) в драйвере или в его причине."""
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    if diag is not None and getattr(diag, "sqlstate", None) == UNIQUE_VIOLATION:
        return True
    if getattr(exc.orig, "pgcode", None) == UNIQUE_VIOLATION:
        return True
    cause = getattr(exc.orig, "__cause__", None)
    return getattr(cause, "sqlstate", None) == UNIQUE_VIOLATION


def register_user(session: Session, data: UserRegister) -> User:
    """Зарегистрировать пользователя и вернуть его (без хэша наружу).

    Гонка регистрации: предварительный SELECT «есть ли такой email» ничего
    не гарантирует — между проверкой и вставкой другой запрос успеет
    вставить тот же адрес. Поэтому единственная настоящая защита —
    UNIQUE(email) в БД, а IntegrityError мы разбираем после отката:
    нарушение уникальности (23505) превращается в понятный отказ 409,
    всё остальное не маскируется.

    is_admin выставляется ЯВНО в False: даже если схема входа когда-нибудь
    изменится, эта строка не даст создать администратора регистрацией.
    Публичного маршрута назначения администратора нет вообще.
    """
    user = User(
        email=str(data.email),
        hashed_password=hash_password(data.password),
        is_admin=False,
    )
    session.add(user)
    try:
        # flush отправляет INSERT и сразу ловит нарушение ограничений,
        # не дожидаясь commit.
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        if _is_unique_violation(exc):
            raise EmailAlreadyRegistered(f"email {user.email} is already registered") from exc
        # Прочие нарушения целостности (например, NOT NULL) — не про
        # занятый email, и притворяться иначе нельзя.
        raise

    session.commit()
    return user
