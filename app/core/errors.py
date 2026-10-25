# Ошибки бизнес-уровня и их безопасный перевод в HTTP (M4: корзина).
#
# Зачем общий тип: сервис не должен импортировать FastAPI (правило
# «в сервисе нет HTTP-ошибок»), но HTTP-слой всё равно обязан отличать
# «не найдено» (404) от «конфликт состояния» (409) и «ошибка данных» (422).
# Поэтому исключение несёт только два безопасных факта:
#
#   status_code — каким HTTP-кодом это показать клиенту;
#   detail      — короткий текст для поля "detail", БЕЗ имён ограничений БД,
#                 SQL, параметров и traceback.
#
# Имена таблиц/ограничений и текст исключения драйвера наружу не уходят
# никогда: по ним клиент узнаёт устройство схемы. Они остаются в логах
# сервера (logger.exception), где их видит разработчик.

import logging
from http import HTTPStatus

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class ShopError(Exception):
    """Базовая ошибка магазина: HTTP-код и безопасный текст ответа.

    Один обработчик на всё дерево исключений (см. shop_error_handler)
    избавляет от «лестницы except» в каждом маршруте: добавили новый тип
    ошибки — достаточно указать ей status_code и detail.
    """

    status_code: int = HTTPStatus.BAD_REQUEST
    detail: str = "Request could not be processed"

    def __init__(self, detail: str | None = None) -> None:
        if detail is not None:
            self.detail = detail
        super().__init__(self.detail)


class ShopNotFound(ShopError):
    """Запрошенного объекта нет или он принадлежит другому пользователю."""

    status_code = HTTPStatus.NOT_FOUND
    detail = "Not found"


class ShopForbidden(ShopError):
    """Пользователь известен, но прав на операцию нет (403)."""

    status_code = HTTPStatus.FORBIDDEN
    detail = "Administrator privileges required"


class ShopConflict(ShopError):
    """Запрос корректен по форме, но состояние данных его не допускает."""

    status_code = HTTPStatus.CONFLICT
    detail = "Conflict"


async def shop_error_handler(_request: Request, exc: ShopError) -> JSONResponse:
    """Единый безопасный ответ на любую ShopError.

    В тело уходит только detail — без класса исключения, без str(exc)
    «как есть», без SQL и без имени нарушенного ограничения. Сам факт
    ошибки остаётся в логе сервера: там str(exc) допустим.
    """
    logger.warning("ShopError %s -> %s: %s", type(exc).__name__, exc.status_code, exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
