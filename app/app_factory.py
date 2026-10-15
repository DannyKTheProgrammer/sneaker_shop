# Общая сборка приложения: состав маршрутов задаётся параметром.
#
# Зачем фабрика, а не два почти одинаковых файла: M2 и M3 отличаются ровно
# одним — подключён ли роутер записи. Дублировать health, роутеры и
# обработчик ошибок в двух модулях значило бы гарантированно их разъехать.
#
#   build_app(include_writes=True)  → app/store_main.py  (M3: чтение + запись)
#   build_app(include_writes=False) → app/catalog_main.py (M2: только чтение)

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.database import engine, ping
from app.routers.auth import router as auth_router
from app.routers.catalog import router as catalog_router
from app.routers.catalog_write import router as catalog_write_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Управление временем жизни ресурсов приложения.

    При остановке освобождаем пул соединений и закрываем их.
    Без dispose() соединения остаются висеть до завершения процесса,
    что мешает аккуратному перезапуску (особенно с --reload).

    Важно: в Windows «жёсткое» убийство процесса (Stop-Process, закрытие
    окна) не даёт выполнить этот код — срабатывает только штатное завершение
    (Ctrl+C в терминале, SIGTERM в Linux/контейнере).
    """
    logger.info("Приложение запускается: Engine будет создан лениво при первом запросе")
    yield
    engine.dispose()
    logger.info("Engine освобождён: пул соединений закрыт")


def _redact_validation_errors(errors: list[dict]) -> list[dict]:
    """Убрать из ошибок валидации значения запроса.

    Pydantic кладёт в ошибку само значение поля ('input') и контекст
    ('ctx'). Для пароля это означало бы вернуть клиенту его же пароль в
    открытом виде — в том числе когда пароль не подошёл (422). Оставляем
    только type/loc/msg/url: клиенту этого достаточно, чтобы понять, что
    именно он прислал не так, а секрет не покидает процесс.
    """
    return [
        {key: value for key, value in error.items() if key not in ("input", "ctx")}
        for error in errors
    ]


def build_app(*, include_writes: bool) -> FastAPI:
    """Собрать приложение; include_writes=True добавляет маршруты записи."""
    version = "0.5.0" if include_writes else "0.2.0"
    description = (
        "Учебный бэкенд магазина кроссовок. M3: чтение каталога, регистрация, "
        "вход с выдачей JWT и запись каталога администратором."
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

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        """Тот же 422, но без значений полей в теле ответа.

        Нужен из-за регистрации и входа: без маскировки неверный пароль
        вернулся бы клиенту в поле input.
        """
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
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
        """Проверить доступность БД реальным запросом SELECT 1.

        /docs и /openapi.json открываются и без БД: они целиком строятся
        из схемы приложения и к PostgreSQL не обращаются. Поэтому открытие
        документации НЕ подтверждает, что БД доступна — подтверждает только
        ответ этого эндпоинта.
        """
        try:
            ping()
        except SQLAlchemyError:
            # Ловим именно ошибки доступа к БД: недоступность сервера,
            # истёкший statement_timeout, исчерпанный pool_timeout.
            # Такой except не прячет ошибки остальной программы: например,
            # ошибка в коде обработчика по-прежнему всплывёт наружу.
            # Детали (URL, логин, traceback) остаются в логах сервера и
            # клиенту не отдаются.
            logger.exception("Health check: база данных недоступна")
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "unavailable"}

        return {"status": "ok"}

    return app
