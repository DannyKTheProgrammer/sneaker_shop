# Запись каталога (M3): создание, замена и удаление товара.
#
# Все три маршрута защищены require_admin — это одна зависимость на весь
# роутер, а не проверка внутри каждого обработчика. Отсюда правило: если
# маршрут зарегистрирован в этом файле, он админский по построению; GET-ы
# живут в app/routers/catalog.py и остаются публичными.
#
# Роутер подключается только там, где запись разрешена (app/store_main.py).
# Контрольная точка M2 (app/catalog_main.py) его не подключает, поэтому её
# OpenAPI по-прежнему содержит только GET.

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.database import get_session
from app.routers.auth import require_admin
from app.schemas.catalog import ProductRead, ProductWrite
from app.services import catalog as catalog_service

router = APIRouter(tags=["catalog"], dependencies=[Depends(require_admin)])

SessionDep = Annotated[Session, Depends(get_session)]


def _conflict(exc: Exception) -> HTTPException:
    """Перевести конфликт сервиса в 409.

    Ссылка из истории/корзины — это конфликт состояния, а не ошибка
    клиента в данных, поэтому 409, а не 422.
    """
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.post(
    "/products",
    status_code=status.HTTP_201_CREATED,
    summary="Создать товар (только администратор)",
    responses={
        401: {"description": "Токен отсутствует или недействителен"},
        403: {"description": "Пользователь не администратор"},
        409: {"description": "Бренда с таким id нет"},
        422: {"description": "Невалидные данные или лишние поля"},
    },
)
def create_product(payload: ProductWrite, session: SessionDep) -> ProductRead:
    """Создать карточку товара.

    Размеры не создаются: их источник — seed, контракт ProductWrite их не
    принимает (extra="forbid"), поэтому у нового товара sizes пустой.
    """
    try:
        return catalog_service.create_product(session, payload)
    except catalog_service.BrandNotFound as exc:
        raise _conflict(exc) from exc
    except catalog_service.CatalogConflict as exc:
        raise _conflict(exc) from exc


@router.put(
    "/products/{product_id}",
    summary="Заменить карточку товара (только администратор)",
    responses={
        401: {"description": "Токен отсутствует или недействителен"},
        403: {"description": "Пользователь не администратор"},
        404: {"description": "Товара с таким id нет"},
        409: {"description": "Бренда с таким id нет"},
        422: {"description": "Невалидные данные или лишние поля"},
    },
)
def update_product(product_id: int, payload: ProductWrite, session: SessionDep) -> ProductRead:
    """Полная замена редактируемых полей (не PATCH).

    Порядок отказов: сначала 404 (нет товара — обновлять нечего), затем 409
    (товар есть, но бренд невалиден). Пропущенные description и image_url
    сбрасываются к '' и null — так работает контракт PUT.
    """
    try:
        return catalog_service.update_product(session, product_id, payload)
    except catalog_service.ProductNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except catalog_service.BrandNotFound as exc:
        raise _conflict(exc) from exc
    except catalog_service.CatalogConflict as exc:
        raise _conflict(exc) from exc


@router.delete(
    "/products/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить товар вместе с размерами (только администратор)",
    responses={
        204: {"description": "Удалено, тело ответа пустое"},
        401: {"description": "Токен отсутствует или недействителен"},
        403: {"description": "Пользователь не администратор"},
        404: {"description": "Товара с таким id нет"},
        409: {"description": "На размеры товара ссылаются корзина или заказы"},
    },
)
def delete_product(product_id: int, session: SessionDep) -> Response:
    """Удалить товар и его размеры одной транзакцией.

    Если на любой размер ссылается cart_items или order_items — 409 и
    полный откат: ни карточка, ни размеры, ни ссылки не меняются. Успех —
    204 с пустым телом, повторный DELETE того же id — 404.
    """
    try:
        catalog_service.delete_product(session, product_id)
    except catalog_service.ProductNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except catalog_service.CatalogConflict as exc:
        raise _conflict(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
