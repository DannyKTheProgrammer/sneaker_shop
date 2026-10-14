# Схемы ответов для каталога (M2).
#
# Зачем отдельный слой: ORM-модель описывает таблицу, а схема — то, что
# реально уходит клиенту. Это разные контракты: в модели есть внутренние
# поля (например, hashed_password у User), которых в ответе быть не должно.
#
# from_attributes=True разрешает собирать схему прямо из ORM-объекта
# (чтение атрибутов), без ручного перекладывания полей.

from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class BrandRead(BaseModel):
    """Бренд: только id и имя, без товаров внутри."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class ProductSizeRead(BaseModel):
    """Размер товара и остаток."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    size: Decimal
    stock: int


class ProductRead(BaseModel):
    """Карточка товара вместе с размерами.

    price и size — Decimal: в Python это точные десятичные числа, а в JSON
    они уходят строками ("7990.00"), потому что JSON не различает int и
    Decimal, а float для денег недопустим. Строку в JSON даёт
    json_encoders ниже.
    """

    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={Decimal: lambda value: format(value, "f")},
    )

    id: int
    name: str
    brand_id: int
    price: Decimal
    description: str
    image_url: str | None
    sizes: list[ProductSizeRead]
