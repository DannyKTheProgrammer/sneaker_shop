# Точка входа FastAPI.
#
# На этом шаге приложение умеет ровно одно: отвечать про доступность БД.
# Ни create_all, ни таблиц, ни бизнес-маршрутов здесь нет намеренно.
#
# Замечание по эксплуатации: логи и сообщения этого модуля на русском,
# а консоль Windows по умолчанию не UTF-8, поэтому при запуске без
# PYTHONUTF8=1 в терминале видно кракозябры. Это дефект отображения,
# а не логики; лечится запуском с PYTHONUTF8=1 (или chcp 65001).

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, status
from sqlalchemy.exc import SQLAlchemyError

from app.database import engine, ping

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


app = FastAPI(
    title="Sneaker Shop API (practice)",
    version="0.1.0",
    description="Учебный бэкенд магазина кроссовок. Текущий шаг: только проверка доступности БД.",
    lifespan=lifespan,
)


@app.get(
    "/health",
    summary="Проверка доступности API и БД",
    responses={
        200: {"description": "БД отвечает на SELECT 1", "content": {"application/json": {"example": {"status": "ok"}}}},
        503: {
            "description": "БД недоступна или пул соединений переполнен",
            "content": {"application/json": {"example": {"status": "unavailable"}}},
        },
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
