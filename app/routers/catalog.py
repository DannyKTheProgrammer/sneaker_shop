# Публичные чтения каталога: GET /brands, GET /products (с фильтрами и
# страницей), GET /products/{id}.
#
# Здесь только чтение. POST/PUT/PATCH/DELETE не регистрируются намеренно:
# изменять каталог сможет только администратор, а require_admin появится
# в M3. Отсутствующий маршрут даёт 405 Method Not Allowed и ничего не
# меняет в БД — это ожидаемое поведение текущего этапа, а не недоделка.

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.dependencies import SessionDep
from app.schemas.catalog import BrandRead, ProductRead
from app.schemas.query import ProductFilters
from app.services import catalog as catalog_service

router = APIRouter(tags=["catalog"])


@router.get("/brands", summary="Список брендов")
def list_brands(session: SessionDep) -> list[BrandRead]:
    """Вернуть все бренды, отсортированные по id.

    Массив без обёртки и без total: постраничность появится позже, когда
    в ней будет смысл. Пустой каталог — это пустой массив, а не 404.
    """
    return catalog_service.list_brands(session)


@router.get("/products", summary="Список товаров")
def list_products(
    session: SessionDep,
    filters: Annotated[ProductFilters, Query()],
) -> list[ProductRead]:
    """Вернуть товары с размерами: фильтры через AND, сортировка по id, страница.

    Параметры (все необязательные, кроме limit/offset с значениями по умолчанию):

    * `brand_id` — точное совпадение;
    * `min_price` / `max_price` — включительные границы цены; `min_price`
      больше `max_price` даёт 422 (проверяется в ProductFilters);
    * `size` — есть размер с таким значением И остатком больше нуля
      (правило наличия этого шага);
    * `search` — подстрока в названии без учёта регистра, `%` и `_`
      считаются буквальными символами;
    * `limit` (по умолчанию 20, максимум 100) и `offset` (по умолчанию 0).

    Неизвестный бренд или несуществующий размер — это пустой массив, а не
    404: «ничего не найдено» не ошибка клиента. Некорректные значения
    параметров (отрицательная цена, `NaN`, размер больше 999, три знака
    после точки, `limit=0`, `offset=-1`) отклоняет Pydantic с 422 — до
    обращения к БД.

    Порядок ответа — по возрастанию `id`; состав страниц не гарантирован
    при параллельных вставках и удалениях (см. пояснение в сервисе).
    """
    return catalog_service.list_products(session, filters)


@router.get(
    "/products/{product_id}",
    summary="Карточка товара",
    responses={404: {"description": "Товара с таким id нет"}},
)
def get_product(product_id: int, session: SessionDep) -> ProductRead:
    """Вернуть один товар с размерами.

    Нет товара — 404. Пустой список размеров при этом не 404: товар
    существует, просто у него пока нет размеров.
    """
    product = catalog_service.get_product(session, product_id)
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )
    return product
