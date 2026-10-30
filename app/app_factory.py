# Сборка приложения; include_writes управляет подключением роутеров записи.

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import ShopError, shop_error_handler
from app.database import engine, ping
from app.routers.auth import router as auth_router
from app.routers.cart import router as cart_router
from app.routers.catalog import router as catalog_router
from app.routers.catalog_write import router as catalog_write_router
from app.routers.order import router as order_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Освободить пул соединений при остановке приложения.

    При жёстком убийстве процесса (Windows) код не выполняется.
    """
    logger.info("Приложение запускается: Engine будет создан лениво при первом запросе")
    yield
    engine.dispose()
    logger.info("Engine освобождён: пул соединений закрыт")


def _redact_validation_errors(errors: list[dict]) -> list[dict]:
    """Убрать из ошибок валидации 'input' и 'ctx', чтобы не вернуть пароль клиенту."""
    return [
        {key: value for key, value in error.items() if key not in ("input", "ctx")}
        for error in errors
    ]


def build_app(*, include_writes: bool) -> FastAPI:
    """Собрать приложение; include_writes=True добавляет маршруты записи."""
    version = "0.13.0" if include_writes else "0.2.0"
    description = (
        "Учебный бэкенд магазина кроссовок. M3: чтение каталога, регистрация, "
        "вход с выдачей JWT и запись каталога администратором. "
        "M4: корзина пользователя (POST /cart/items, GET /cart, "
        "PATCH /cart/items/{item_id}, DELETE /cart/items/{item_id}). "
        "M5: POST /orders создаёт заказ одной транзакцией: снимок цен, "
        "списание остатка и очистка корзины (201); GET /orders и "
        "GET /orders/{order_id} — история своих заказов; PATCH /orders/{order_id}/status — "
        "смена статуса администратором (created → processing → completed)."
        if include_writes
        else "Учебный бэкенд магазина кроссовок. M2 (контрольная точка): "
             "только чтение каталога, запись не подключена."
    )
    app = FastAPI(
        title="Sneaker Shop API (practice)",
        version=version,
        description=description,
        lifespan=lifespan,
    )

    app.include_router(catalog_router)
    app.include_router(auth_router)
    if include_writes:
        app.include_router(catalog_write_router)
        app.include_router(cart_router)
        app.include_router(order_router)

    @app.exception_handler(ShopError)
    async def handle_shop_error(request: Request, exc: ShopError) -> JSONResponse:
        """Перевести ShopError в JSON с кодом и detail."""
        return await shop_error_handler(request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        """422 без значений полей (иначе пароль вернулся бы в input)."""
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": _redact_validation_errors(exc.errors())},
        )

    @app.get(
        "/health",
        summary="Проверка доступности API и БД",
        responses={
            200: {"description": "БД отвечает на SELECT 1",
                  "content": {"application/json": {"example": {"status": "ok"}}}},
            503: {"description": "БД недоступна или пул соединений переполнен",
                  "content": {"application/json": {"example": {"status": "unavailable"}}}},
        },
    )
    def health(response: Response) -> dict[str, str]:
        """Проверить доступность БД запросом SELECT 1.

        /docs работает и без БД, поэтому доступность подтверждает только этот эндпоинт.
        """
        try:
            ping()
        except SQLAlchemyError:
            # Только ошибки БД; детали остаются в логах, клиенту не отдаются.
            logger.exception("Health check: база данных недоступна")
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "unavailable"}

        return {"status": "ok"}

    return app
