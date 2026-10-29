# Зависимости FastAPI: сессия на запрос, текущий пользователь и роль.
#
# Пользователь всегда читается из БД по sub проверенного JWT; роль и факт
# существования берутся из строки users, а не из содержимого токена.
#
#   SessionDep / CurrentUser / AdminUser — сессия из Depends(get_session),
#       живёт до конца запроса (/auth/me, запись каталога);
#   get_current_user_id / get_current_admin_id — короткая собственная сессия,
#       закрывается до вызова сервиса корзины и заказов (дефект D1).

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.security import InvalidToken, decode_access_token
from app.database import SessionLocal, get_session
from app.models import User
from app.services import auth as auth_service

SessionDep = Annotated[Session, Depends(get_session)]

# Схема безопасности для OpenAPI: поле для токена в Authorize.
# auto_error=False — чтобы отсутствие заголовка давало наш 401 с
# WWW-Authenticate, а не встроенный 403 без пояснений.
bearer_scheme = HTTPBearer(auto_error=False, description="Вставьте access_token из /auth/login")

# Один и тот же заголовок для всех отказов доступа.
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
    """Проверить Bearer-токен и прочитать его пользователя в переданной сессии.

    Общая часть get_current_user и зависимостей «только id» ниже: отличаются
    они лишь тем, чья это сессия и когда она закрывается.
    """
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
    """Пользователь из access token.

    Шаги:

    1. Заголовок `Authorization: Bearer <token>` разбирает HTTPBearer с
       auto_error=False. Нет заголовка или он не Bearer → 401 от нас (а не
       встроенный 403), всегда с WWW-Authenticate: Bearer.
    2. Подпись, срок, iss, aud и формат sub проверяет
       core.security.decode_access_token. Декодированному payload мы не
       доверяем: из него берётся ТОЛЬКО id пользователя.
    3. Пользователь читается из БД по этому id. Возвращается настоящая
       строка User, а не то, что написано в токене: роль, email и факт
       существования берутся из базы и потому не могут быть подделаны
       содержимым токена. Нет строки → 401 (токен подписан нами, но
       пользователь удалён).

    Побочный эффект, который нам и нужен: изменение роли в БД действует
    на следующем же запросе с уже выданным токеном — перевыпуск не нужен.

    Сессия здесь — из Depends(get_session) и живёт до конца запроса. Это
    годится там, где той же сессией дальше работает сам маршрут (/auth/me,
    запись каталога). Для корзины и заказов — см. get_current_user_id.
    """
    return _user_from_token(credentials, session)


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_current_user_id(credentials: BearerCredentials) -> int:
    """Только id пользователя из проверенного токена (корзина и заказы).

    Наружу уходит не ORM-объект, а целое число: бизнес-операции корзины и
    checkout работают в СВОЕЙ свежей сессии (см. app/services/shop.py), и
    передавать туда объект из чужой сессии нельзя — чтение его атрибутов
    может пойти в старую транзакцию со старым снимком.

    Пользователь читается в КОРОТКОЙ сессии, которая закрывается до вызова
    сервиса: close() откатывает её неявную транзакцию и возвращает соединение
    в пул. Раньше здесь была сессия из Depends(get_session), и она держала
    второе соединение «idle in transaction» до конца запроса. Под параллельной
    нагрузкой запросы занимали весь пул первыми соединениями и падали 500 по
    pool_timeout, ожидая второе (дефект D1). Теперь в каждый момент запрос
    держит не больше одного соединения.

    Проверка токена при этом не ослабляется: id берётся из строки users,
    прочитанной из БД по sub токена, то есть удалённый пользователь получит
    401 ещё здесь. Блокировку строки владельца сервис берёт заново, в своей
    транзакции.
    """
    with SessionLocal() as session:
        return _user_from_token(credentials, session).id


def get_current_admin_id(credentials: BearerCredentials) -> int:
    """id администратора из проверенного токена; иначе 401/403.

    То же, что get_current_user_id, плюс проверка роли — для маршрутов, где
    дальше работает сервис в своей сессии (PATCH /orders/{id}/status).
    Сессия закрывается до вызова сервиса, а сервис перечитывает роль под
    users FOR SHARE в своей транзакции: проверка здесь — ранний отказ 403,
    решающая — там.
    """
    with SessionLocal() as session:
        user = _user_from_token(credentials, session)
        if user.is_admin is not True:
            raise _forbidden()
        return user.id


def require_admin(current_user: CurrentUser) -> User:
    """Пропустить только администратора (иначе 403).

    Роль берётся из строки пользователя, прочитанной из БД на этом же
    запросе, а не из claims токена: иначе достаточно было бы один раз
    получить админский токен или подделать claim, чтобы права остались
    навсегда. Здесь же снятие роли действует сразу.

    Проверяется строго `is True`: если колонка когда-нибудь окажется NULL
    (схема разошлась с ожиданием), доступ всё равно будет закрыт.
    """
    if current_user.is_admin is not True:
        raise _forbidden()
    return current_user


AdminUser = Annotated[User, Depends(require_admin)]
