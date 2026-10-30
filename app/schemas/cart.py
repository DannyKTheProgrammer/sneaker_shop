# Схемы корзины. Во входных схемах только размер и количество: владелец,
# цена и остаток берутся из токена и БД, лишние поля дают 422.

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.schemas.money import DecimalStr

# Границы int4.
PrimaryKey = Annotated[StrictInt, Field(ge=1, le=2147483647)]

# Strict: 1.0, "1" и true отклоняются. Верхняя граница из int4, не из остатка:
# превышение остатка — это 409, а не 422.
Quantity = Annotated[StrictInt, Field(ge=1, le=2147483647)]


class CartItemAdd(BaseModel):
    """Вход POST /cart/items; quantity — прибавка, а не итоговое значение."""

    model_config = ConfigDict(extra="forbid")

    product_size_id: PrimaryKey
    quantity: Quantity


class CartItemUpdate(BaseModel):
    """Вход PATCH /cart/items/{item_id}; quantity — итоговое значение (ноль отклоняется)."""

    model_config = ConfigDict(extra="forbid")

    quantity: Quantity


class CartProductRead(BaseModel):
    """Товар внутри позиции корзины."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class CartSizeRead(BaseModel):
    """Размер внутри позиции корзины."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    size: Decimal
    product_id: int
    product: CartProductRead


class CartItemRead(BaseModel):
    """Плоская позиция корзины; subtotal = quantity * price (price берётся из products)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    product_size_id: int
    product_id: int
    name: str
    size: DecimalStr
    price: DecimalStr
    quantity: int
    stock: int
    subtotal: DecimalStr


class CartRead(BaseModel):
    """Ответ GET /cart; estimated_total — оценка по текущим ценам, в БД не хранится."""

    items: list[CartItemRead]
    estimated_total: DecimalStr
