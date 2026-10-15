# Маршруты аутентификации (M3: регистрация и вход).
#
# Выдачи JWT здесь пока нет — вход подтверждает пару email/пароль и
# возвращает пользователя с access_token=null. Админские маршруты каталога
# появятся вместе с require_admin на следующем шаге.

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_session
from app.schemas.auth import TokenResponse, UserLogin, UserRead, UserRegister
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

SessionDep = Annotated[Session, Depends(get_session)]


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Регистрация пользователя",
    responses={
        409: {"description": "Нормализованный email уже занят"},
        422: {"description": "Невалидные данные или лишние поля"},
    },
)
def register(payload: UserRegister, session: SessionDep) -> UserRead:
    """Зарегистрировать пользователя и вернуть его безопасные поля.

    Принимаются только `email` и `password`. Email нормализуется
    (strip + lower) до проверок и до записи. Пароль проверяется по длине
    в символах и в байтах UTF-8; NUL-символы запрещены. Пароль никогда не
    возвращается: ответ — `UserRead` (id, email, is_admin), а в ошибках
    валидации значение `password` замаскировано обработчиком в app.main.

    Занятый email → 409. Проверка опирается на UNIQUE(email) в БД, а не на
    предварительный SELECT: при гонке двух одновременных регистраций
    вторая получит отказ от базы, и он будет переведён в тот же 409.
    """
    try:
        return auth_service.register_user(session, payload)
    except auth_service.EmailAlreadyRegistered as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        ) from exc


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Вход по email и паролю",
    responses={
        401: {"description": "Неверная пара email/пароль"},
        422: {"description": "Невалидные данные или лишние поля"},
    },
)
def login(payload: UserLogin, session: SessionDep) -> TokenResponse:
    """Проверить пару email/пароль.

    Пароль сверяется с сохранённым хэшем через `verify_password` (соль
    внутри хэша, поэтому повторное вычисление и сравнение строк не
    используется). Если email не найден, выполняется такая же проверка
    против dummy_hash, и ответ тот же самый — 401 без подробностей: по
    ответу нельзя понять, существует ли такой адрес.

    Токена пока нет: `access_token` = null, `token_type` = "none". JWT —
    следующий шаг; выдумывать несуществующий токен не будем.
    """
    try:
        user = auth_service.authenticate_user(session, payload)
    except auth_service.InvalidCredentials as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    return TokenResponse(access_token=None, token_type="none", user=UserRead.model_validate(user))
