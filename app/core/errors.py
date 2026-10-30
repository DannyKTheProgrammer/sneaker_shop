# Бизнес-ошибки без зависимости от FastAPI: несут только status_code и detail.
# Имена ограничений БД, SQL и traceback наружу не уходят, только в логи.

import logging
from http import HTTPStatus

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class ShopError(Exception):
    """Базовая ошибка магазина: HTTP-код и безопасный detail."""

    status_code: int = HTTPStatus.BAD_REQUEST
    detail: str = "Request could not be processed"

    def __init__(self, detail: str | None = None) -> None:
        if detail is not None:
            self.detail = detail
        super().__init__(self.detail)


class ShopNotFound(ShopError):
    """Объекта нет или он принадлежит другому пользователю (404)."""

    status_code = HTTPStatus.NOT_FOUND
    detail = "Not found"


class ShopForbidden(ShopError):
    """Нет прав на операцию (403)."""

    status_code = HTTPStatus.FORBIDDEN
    detail = "Administrator privileges required"


class ShopConflict(ShopError):
    """Состояние данных не допускает операцию (409)."""

    status_code = HTTPStatus.CONFLICT
    detail = "Conflict"


async def shop_error_handler(_request: Request, exc: ShopError) -> JSONResponse:
    """Ответить на ShopError JSON с одним полем detail."""
    logger.warning("ShopError %s -> %s: %s", type(exc).__name__, exc.status_code, exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
