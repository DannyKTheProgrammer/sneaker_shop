# HTTP-маршруты каталога (M2).
#
# Здесь только чтение. POST/PUT/PATCH/DELETE не регистрируются намеренно:
# изменять каталог сможет только администратор, а require_admin появится
# в M3. Отсутствующий маршрут даёт 405 Method Not Allowed и ничего не
# меняет в БД — это ожидаемое поведение текущего этапа, а не недоделка.

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_session
from app.schemas.catalog import BrandRead, ProductRead
from app.services import catalog as catalog_service

router = APIRouter(tags=["catalog"])

# Сессия на один запрос: FastAPI откроет её перед обработчиком и закроет
# после ответа (generate-зависимость из app.database.get_session).
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/brands", response_model=list[BrandRead], summary="Список брендов")
def list_brands(session: SessionDep) -> list[BrandRead]:
    """Вернуть все бренды, отсортированные по id.

    Массив без обёртки и без total: постраничность появится позже, когда
    в ней будет смысл. Пустой каталог — это пустой массив, а не 404.
    """
    return catalog_service.list_brands(session)


@router.get("/products", response_model=list[ProductRead], summary="Список товаров")
def list_products(session: SessionDep) -> list[ProductRead]:
    """Вернуть все товары с размерами, отсортированные по id.

    Фильтр по бренду появится позже; сейчас неизвестный параметр просто
    игнорируется FastAPI, а неизвестный бренд в будущем фильтре даст
    пустой массив, а не 404: «нет таких товаров» — не ошибка клиента.
    """
    return catalog_service.list_products(session)


@router.get(
    "/products/{product_id}",
    response_model=ProductRead,
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
