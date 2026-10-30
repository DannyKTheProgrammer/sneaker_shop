# Корзина. Владелец берётся только из токена, у администратора особых прав на чужую корзину нет.

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response, status

from app.dependencies import get_current_user_id
from app.schemas.cart import CartItemAdd, CartItemRead, CartItemUpdate, CartRead
from app.services import shop as shop_service

router = APIRouter(prefix="/cart", tags=["cart"])

# Только id: ORM-объект пользователя не передаётся в сервис.
CurrentUserId = Annotated[int, Depends(get_current_user_id)]

# Границы int4: некорректный id отсекается как 422, несуществующий даёт 404.
ItemId = Annotated[int, Path(ge=1, le=2147483647, description="id строки cart_items")]


@router.get(
    "",
    response_model=CartRead,
    status_code=status.HTTP_200_OK,
    summary="Своя корзина: позиции и ориентировочная сумма",
    responses={
        200: {"description": "Позиции по возрастанию id и estimated_total; пустая корзина — [] и 0.00"},
        400: {"description": "Пользователь из токена больше не существует"},
        401: {"description": "Токен отсутствует или недействителен"},
    },
)
def read_cart(user_id: CurrentUserId) -> CartRead:
    """Вернуть свою корзину (позиции по возрастанию id) и ориентировочную сумму.

    Цены и остатки текущие, сумма не фиксируется; снимок цены делает оформление заказа.
    Пустая корзина — 200, не 404.
    """
    return shop_service.get_cart(user_id)


@router.post(
    "/items",
    response_model=CartItemRead,
    status_code=status.HTTP_200_OK,
    summary="Добавить размер в корзину (delta к количеству)",
    responses={
        200: {"description": "Позиция добавлена или её количество увеличено"},
        400: {"description": "Пользователь из токена больше не существует"},
        401: {"description": "Токен отсутствует или недействителен"},
        404: {"description": "Размера товара с таким id нет"},
        409: {"description": "Итоговое количество больше остатка на складе"},
        422: {"description": "Невалидные данные или лишние поля"},
    },
)
def add_cart_item(payload: CartItemAdd, user_id: CurrentUserId) -> CartItemRead:
    """Добавить `quantity` единиц размера в корзину.

    `quantity` — дельта: повторный вызов увеличивает количество (не идемпотентно).
    Остаток не резервируется; итог больше остатка даёт 409. Ответ всегда 200.
    """
    return shop_service.add_cart_item(user_id, payload)


@router.patch(
    "/items/{item_id}",
    response_model=CartItemRead,
    status_code=status.HTTP_200_OK,
    summary="Установить абсолютное количество позиции",
    responses={
        200: {"description": "Количество заменено (не прибавлено), вернулась обновлённая позиция"},
        400: {"description": "Пользователь из токена больше не существует"},
        401: {"description": "Токен отсутствует или недействителен"},
        404: {"description": "Позиции с таким id нет ИЛИ она принадлежит другому пользователю"},
        409: {"description": "Запрошенное количество больше остатка на складе"},
        422: {"description": "Невалидные данные: quantity вне 1..2147483647, лишние поля, нечисловой id"},
    },
)
def update_cart_item(
    item_id: ItemId,
    payload: CartItemUpdate,
    user_id: CurrentUserId,
) -> CartItemRead:
    """Установить абсолютное количество позиции.

    `item_id` — id строки cart_items. Чужая позиция неотличима от несуществующей (404,
    в том числе для администратора). Количество больше остатка даёт 409.
    """
    return shop_service.update_cart_item(user_id, item_id, payload)


@router.delete(
    "/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить свою позицию из корзины",
    responses={
        204: {"description": "Позиция удалена, тело ответа пустое"},
        400: {"description": "Пользователь из токена больше не существует"},
        401: {"description": "Токен отсутствует или недействителен"},
        404: {"description": "Позиции с таким id нет ИЛИ она принадлежит другому пользователю"},
        422: {"description": "Нечисловой или неположительный id позиции"},
    },
)
def delete_cart_item(item_id: ItemId, user_id: CurrentUserId) -> Response:
    """Удалить свою позицию (204); чужая или уже удалённая даёт 404."""
    shop_service.delete_cart_item(user_id, item_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
