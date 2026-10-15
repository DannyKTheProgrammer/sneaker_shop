# Аутентификация: регистрация и вход (M3).
#
# Здесь нет ни FastAPI, ни HTTP-кодов: сервис отвечает за пароль и за
# строку в таблице users, а «201/401/409/422» решает роутер.
#
# Криптография пароля живёт в app/core/security.py и вызывается оттуда:
# этот модуль отвечает только за БД и правила доступа.

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import dummy_hash, hash_password, verify_password
from app.models import User
from app.schemas.auth import UserLogin, UserRegister

# SQLSTATE нарушения уникальности в PostgreSQL.
UNIQUE_VIOLATION = "23505"

# Одно и то же сообщение для «нет такого email» и «неверный пароль»:
# разные тексты позволили бы перебором выяснять, какие адреса
# зарегистрированы.
INVALID_CREDENTIALS_MESSAGE = "Invalid email or password"


class AuthError(Exception):
    """Ошибка аутентификации, которую роутер переводит в HTTP-ответ."""


class EmailAlreadyRegistered(AuthError):
    """Нормализованный email уже занят."""


class InvalidCredentials(AuthError):
    """Пользователь не найден ИЛИ пароль не подошёл — без уточнений."""


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


def get_user_by_id(session: Session, user_id: int) -> User | None:
    """Найти пользователя по id (для проверки токена).

    None, а не исключение: решение «401 или 200» принимает зависимость
    аутентификации, а не сервис.
    """
    return session.get(User, user_id)


def authenticate_user(session: Session, data: UserLogin) -> User:
    """Проверить пару email/пароль и вернуть пользователя.

    Пароль сверяется ТОЛЬКО через verify_password(введённый, сохранённый
    хэш): соль лежит внутри сохранённой строки, и сравнивать с ней заново
    посчитанный хэш нельзя — они разные даже для верного пароля.

    Если email не найден, выполняется ровно та же по стоимости проверка,
    но против заранее вычисленного dummy_hash. Смысл — не дать отличить
    «нет такого пользователя» от «неверный пароль» по времени ответа и не
    выдать существование адреса. Это снижение разницы, а не гарантия
    постоянного времени: сеть, БД и загрузка машины тоже влияют на ответ.

    Возвращаем ту же ошибку InvalidCredentials в обоих случаях, чтобы текст
    ответа тоже не подсказывал, что именно не так.
    """
    user = session.scalars(select(User).where(User.email == str(data.email))).first()
    if user is None:
        # Проверяем против заглушки и всё равно отказываем.
        verify_password(data.password, dummy_hash())
        raise InvalidCredentials(INVALID_CREDENTIALS_MESSAGE)

    if not verify_password(data.password, user.hashed_password):
        raise InvalidCredentials(INVALID_CREDENTIALS_MESSAGE)

    return user
