# Заказы. Владелец и цены берутся из токена и БД, не из тела запроса.
# Только PATCH статуса доступен администратору.

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, status

from app.dependencies import get_current_admin_id, get_current_user_id
from app.schemas.order import OrderCreate, OrderListParams, OrderRead, OrderStatusUpdate
from app.services import shop as shop_service

router = APIRouter(prefix="/orders", tags=["orders"])

CurrentUserId = Annotated[int, Depends(get_current_user_id)]
CurrentAdminId = Annotated[int, Depends(get_current_admin_id)]

# Границы int4.
OrderId = Annotated[int, Path(ge=1, le=2147483647, description="id заказа")]


@router.get(
    "",
    response_model=list[OrderRead],
    summary="Мои заказы (новые первыми)",
    responses={
        401: {"description": "Токен отсутствует или недействителен"},
        422: {"description": "limit вне 1..100, offset < 0 или лишний параметр"},
    },
)
def list_orders(
    user_id: CurrentUserId,
    page: Annotated[OrderListParams, Query()],
) -> list[OrderRead]:
    """Свои заказы, новые первыми; пустая история — `[]`."""
    return shop_service.list_orders(user_id, page.limit, page.offset)


@router.get(
    "/{order_id}",
    response_model=OrderRead,
    summary="Мой заказ по id",
    responses={
        401: {"description": "Токен отсутствует или недействителен"},
        404: {"description": "Заказа нет или он чужой (в том числе для администратора)"},
        422: {"description": "order_id не целое или вне 1..2147483647"},
    },
)
def get_order(order_id: OrderId, user_id: CurrentUserId) -> OrderRead:
    """Свой заказ; чужой неотличим от несуществующего (404, в том числе для администратора)."""
    return shop_service.get_order(user_id, order_id)


@router.patch(
    "/{order_id}/status",
    response_model=OrderRead,
    summary="Сменить статус заказа (только администратор)",
    responses={
        200: {"description": "Статус сменён, или он уже был таким (без изменений)"},
        401: {"description": "Токен отсутствует или недействителен"},
        403: {"description": "Пользователь не администратор"},
        404: {"description": "Заказа нет"},
        409: {"description": "Переход не разрешён: пропуск шага или обратный ход"},
        422: {"description": "Неизвестный статус, лишнее поле, тело не JSON, плохой id"},
    },
)
def update_order_status(
    order_id: OrderId,
    payload: OrderStatusUpdate,
    admin_id: CurrentAdminId,
) -> OrderRead:
    """Сменить статус: `created → processing → completed`, только вперёд.

    Повтор текущего статуса — 200 без записи; пропуск и обратный ход — 409.
    Роль перепроверяется в сервисе под `users FOR SHARE`.
    """
    return shop_service.update_order_status(admin_id, order_id, payload.status)


@router.post(
    "",
    response_model=OrderRead,
    status_code=status.HTTP_201_CREATED,
    summary="Создать заказ из корзины (снимок цен)",
    responses={
        201: {"description": "Заказ создан: id, status, total, created_at, позиции-снимки"},
        401: {"description": "Токен отсутствует или недействителен"},
        409: {"description": "Корзина пуста / не хватает остатка / сумма больше предела Numeric(12,2)"},
        422: {"description": "В теле есть поля (user_id, total, _test_hook, ...) или тело не JSON"},
    },
)
def create_order(
    user_id: CurrentUserId,
    payload: Annotated[OrderCreate | None, Body(description="Тело необязательно; допустимо только {}")] = None,
) -> OrderRead:
    """Создать заказ из своей корзины одной транзакцией (снимок цен, списание остатка, очистка корзины).

    Тело отсутствует или `{}`: payload нужен лишь затем, чтобы отклонить любые поля (422).
    Пустая корзина, нехватка остатка или превышение Numeric(12,2) дают 409 и полный откат.
    """
    _ = payload
    return shop_service.create_order(user_id)
