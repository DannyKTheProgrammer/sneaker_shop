# Схемы корзины (M4, шаг «POST /cart/items»).
#
# Вход и выход — два разных контракта, как и в каталоге:
#
#   CartItemAdd  — то, что клиент имеет право прислать: размер и количество.
#                  Ни user_id, ни цены, ни остатка здесь нет: владельца
#                  берут из проверенного токена, а цену и остаток — из БД.
#                  Лишнее поле (например, user_id) даёт 422.
#
#   CartItemRead — то, что клиент видит в ответе: позиция «как её понимает
#                  магазин»: размер, товар, цена, остаток и стоимость
#                  строки (subtotal).
#
# Деньги и размер — Decimal и уходят в JSON строкой ("7990.00", "42.0"):
# так задано во всём проекте, float для денег не используется.

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt

# id строки product_sizes: Integer в PostgreSQL = 32 бита.
PrimaryKey = Annotated[StrictInt, Field(ge=1, le=2147483647)]

# Количество в корзине: целое, не меньше 1 и в пределах int4.
# Почему strict: 1.0, "1" и true — это не «единица» в JSON, а другой тип.
# Молчаливое приведение скрыло бы ошибку клиента, поэтому такие значения
# отклоняются 422. Верхняя граница берётся из типа колонки, а не из
# остатка: остаток меняется, и «количество больше остатка» — это 409
# (состояние данных), а не 422 (форма запроса).
Quantity = Annotated[StrictInt, Field(ge=1, le=2147483647)]


class CartItemAdd(BaseModel):
    """Вход POST /cart/items: что добавить и сколько.

    Поле quantity — это ДЕЛЬТА (прибавка) для этого маршрута: 2 означает
    «добавь ещё две пары», а не «сделай ровно 2». Абсолютную установку
    количества делает PATCH отдельным шагом (следующая часть M4).
    """

    model_config = ConfigDict(extra="forbid")

    product_size_id: PrimaryKey
    quantity: Quantity


class CartProductRead(BaseModel):
    """Товар внутри позиции корзины: только нужные клиенту поля."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class CartSizeRead(BaseModel):
    """Размер внутри позиции корзины.

    price берётся из ТОВАРА (products.price), а не из размера: в
    product_sizes цены нет, цена — свойство товара.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    size: Decimal
    product_id: int
    product: CartProductRead


class CartItemRead(BaseModel):
    """Ответ POST /cart/items: позиция корзины с товаром, ценой и остатком.

    Поля плоские (без вложенных объектов), потому что клиенту нужна одна
    строка «что лежит в корзине», а не дерево. Собираются они из трёх
    связанных объектов БД:

      id, quantity          — сама строка cart_items;
      product_size_id, size, stock — строка product_sizes;
      product_id, name, price      — карточка products;
      subtotal              — вычисляемое сервером quantity * price.

    price и subtotal — строки в JSON ("7990.00", "15980.00"). subtotal
    округлять не нужно: quantity целое, а price уже Decimal с двумя
    знаками из numeric(10,2), поэтому произведение точное.
    """

    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={Decimal: lambda value: format(value, "f")},
    )

    id: int
    product_size_id: int
    product_id: int
    name: str
    size: Decimal
    price: Decimal
    quantity: int
    stock: int
    subtotal: Decimal
