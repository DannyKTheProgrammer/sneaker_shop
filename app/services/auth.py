# Аутентификация: регистрация и вход. Без FastAPI и HTTP-кодов (их выбирает роутер).
# Хэширование пароля — в app/core/security.py.

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import dummy_hash, hash_password, verify_password
from app.models import User
from app.schemas.auth import UserLogin, UserRegister

# SQLSTATE нарушения уникальности в PostgreSQL.
UNIQUE_VIOLATION = "23505"

# Одно сообщение для «нет email» и «неверный пароль»: не раскрывает, какие адреса зарегистрированы.
INVALID_CREDENTIALS_MESSAGE = "Invalid email or password"


class AuthError(Exception):
    """Ошибка аутентификации, которую роутер переводит в HTTP-ответ."""


class EmailAlreadyRegistered(AuthError):
    """Нормализованный email уже занят."""


class InvalidCredentials(AuthError):
    """Пользователь не найден или пароль не подошёл."""


def _is_unique_violation(exc: IntegrityError) -> bool:
    """Проверить, что это нарушение уникальности (23505)."""
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    if diag is not None and getattr(diag, "sqlstate", None) == UNIQUE_VIOLATION:
        return True
    if getattr(exc.orig, "pgcode", None) == UNIQUE_VIOLATION:
        return True
    cause = getattr(exc.orig, "__cause__", None)
    return getattr(cause, "sqlstate", None) == UNIQUE_VIOLATION


def register_user(session: Session, data: UserRegister) -> User:
    """Зарегистрировать пользователя.

    Занятый email определяется по UNIQUE(email) (23505 -> EmailAlreadyRegistered),
    а не предварительным SELECT: он не защищает от гонки. is_admin всегда False.
    """
    user = User(
        email=str(data.email),
        hashed_password=hash_password(data.password),
        is_admin=False,
    )
    session.add(user)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        if _is_unique_violation(exc):
            raise EmailAlreadyRegistered(f"email {user.email} is already registered") from exc
        raise

    session.commit()
    return user


def get_user_by_id(session: Session, user_id: int) -> User | None:
    """Вернуть пользователя по id или None."""
    return session.get(User, user_id)


def authenticate_user(session: Session, data: UserLogin) -> User:
    """Проверить email и пароль; иначе InvalidCredentials.

    Для неизвестного email проверка идёт против dummy_hash, чтобы сгладить
    разницу во времени ответа.
    """
    user = session.scalars(select(User).where(User.email == str(data.email))).first()
    if user is None:
        verify_password(data.password, dummy_hash())
        raise InvalidCredentials(INVALID_CREDENTIALS_MESSAGE)

    if not verify_password(data.password, user.hashed_password):
        raise InvalidCredentials(INVALID_CREDENTIALS_MESSAGE)

    return user
