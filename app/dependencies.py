# Зависимости FastAPI: сессия, текущий пользователь и роль.
# Пользователь и роль читаются из БД по sub токена, не из claims.

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import InvalidToken, decode_access_token
from app.database import SessionLocal, get_session
from app.models import User
from app.services import auth as auth_service

SessionDep = Annotated[Session, Depends(get_session)]

# auto_error=False: без заголовка отдаём наш 401, а не встроенный 403.
bearer_scheme = HTTPBearer(auto_error=False, description="Вставьте access_token из /auth/login")

WWW_AUTHENTICATE = {"WWW-Authenticate": "Bearer"}


def _unauthorized(detail: str = "Could not validate credentials") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers=WWW_AUTHENTICATE,
    )


def _forbidden() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Administrator privileges required",
    )


BearerCredentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


def _user_from_token(credentials: HTTPAuthorizationCredentials | None, session: Session) -> User:
    """Проверить Bearer-токен и прочитать пользователя в переданной сессии (иначе 401)."""
    if credentials is None or not credentials.credentials:
        raise _unauthorized("Not authenticated")
    try:
        user_id = decode_access_token(credentials.credentials)
    except InvalidToken as exc:
        raise _unauthorized() from exc

    user = auth_service.get_user_by_id(session, user_id)
    if user is None:
        raise _unauthorized()
    return user


def get_current_user(credentials: BearerCredentials, session: SessionDep) -> User:
    """Пользователь из access token; сессия живёт до конца запроса.

    Нет токена, токен неверен или пользователь удалён: 401. Смена роли в БД
    действует сразу, без перевыпуска токена.
    """
    return _user_from_token(credentials, session)


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_current_user_id(credentials: BearerCredentials) -> int:
    """id пользователя из токена (корзина и заказы).

    Читает пользователя в короткой сессии, закрытой до вызова сервиса:
    иначе запрос держит два соединения и под нагрузкой упирается в pool_timeout.
    """
    with SessionLocal() as session:
        return _user_from_token(credentials, session).id


def get_current_admin_id(credentials: BearerCredentials) -> int:
    """id администратора из токена; иначе 401/403.

    Это ранний отказ: сервис перепроверяет роль под users FOR SHARE.
    """
    with SessionLocal() as session:
        user = _user_from_token(credentials, session)
        if user.is_admin is not True:
            raise _forbidden()
        return user.id


def require_admin(current_user: CurrentUser) -> User:
    """Пропустить только администратора (иначе 403).

    Строгое `is True`: NULL в колонке не даёт доступа.
    """
    if current_user.is_admin is not True:
        raise _forbidden()
    return current_user


AdminUser = Annotated[User, Depends(require_admin)]
