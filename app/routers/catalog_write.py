# Запись каталога; require_admin стоит на весь роутер.

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.dependencies import SessionDep, require_admin
from app.schemas.catalog import ProductRead, ProductWrite
from app.services import catalog as catalog_service

router = APIRouter(tags=["catalog"], dependencies=[Depends(require_admin)])


def _conflict(exc: Exception) -> HTTPException:
    """Перевести конфликт сервиса в 409."""
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
    """Создать карточку товара без размеров (ProductWrite их не принимает)."""
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
    """Полная замена полей (не PATCH): пропущенные description и image_url сбрасываются.

    Сначала 404 (нет товара), затем 409 (неверный бренд).
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
    """Удалить товар и его размеры; ссылки из корзины или заказов дают 409 и полный откат."""
    try:
        catalog_service.delete_product(session, product_id)
    except catalog_service.ProductNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except catalog_service.CatalogConflict as exc:
        raise _conflict(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
