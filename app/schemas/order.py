# Схемы заказа. Владелец, позиции и сумма берутся из токена и БД, не от клиента.
# Названия товара в позиции нет: в order_items нет такой колонки.

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.schemas.money import DecimalStr
from app.schemas.query import Limit, Offset

# Совпадает с CHECK ck_order_status.
OrderStatus = Literal["created", "processing", "completed"]


class OrderStatusUpdate(BaseModel):
    """Тело PATCH /orders/{order_id}/status: только новый статус."""

    model_config = ConfigDict(extra="forbid")

    status: OrderStatus


class OrderListParams(BaseModel):
    """Query-параметры GET /orders: только страница; лишние (например, user_id) дают 422."""

    model_config = ConfigDict(extra="forbid")

    limit: Limit = 20
    offset: Offset = 0


class OrderCreate(BaseModel):
    """Вход POST /orders: полей нет, любое присланное поле даёт 422."""

    model_config = ConfigDict(extra="forbid")


class OrderItemRead(BaseModel):
    """Позиция заказа: снимок цены на момент покупки, subtotal = price_at_purchase * quantity."""

    product_size_id: int
    quantity: int
    price_at_purchase: DecimalStr
    subtotal: DecimalStr


class OrderRead(BaseModel):
    """Заказ; total — снимок суммы, а не пересчёт по текущим ценам."""

    id: int
    status: str
    total: DecimalStr
    created_at: datetime
    items: list[OrderItemRead]
