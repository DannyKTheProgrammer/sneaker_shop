# Публичное чтение каталога; запись — в catalog_write.py.

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.dependencies import SessionDep
from app.schemas.catalog import BrandRead, ProductRead
from app.schemas.query import ProductFilters
from app.services import catalog as catalog_service

router = APIRouter(tags=["catalog"])


@router.get("/brands", summary="Список брендов")
def list_brands(session: SessionDep) -> list[BrandRead]:
    """Вернуть все бренды по возрастанию id."""
    return catalog_service.list_brands(session)


@router.get("/products", summary="Список товаров")
def list_products(
    session: SessionDep,
    filters: Annotated[ProductFilters, Query()],
) -> list[ProductRead]:
    """Вернуть товары с размерами: фильтры через AND, сортировка по id, страница.

    `size` требует остаток больше нуля; `search` — подстрока без учёта регистра,
    `%` и `_` буквальные. Ничего не найдено — пустой массив; неверные параметры — 422.
    """
    return catalog_service.list_products(session, filters)


@router.get(
    "/products/{product_id}",
    summary="Карточка товара",
    responses={404: {"description": "Товара с таким id нет"}},
)
def get_product(product_id: int, session: SessionDep) -> ProductRead:
    """Вернуть товар с размерами; нет товара — 404."""
    product = catalog_service.get_product(session, product_id)
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )
    return product
