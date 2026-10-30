# Схемы каталога: контракт API отделён от ORM-моделей.

from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    HttpUrl,
)

from app.schemas.money import DecimalStr

# Границы int4.
BrandId = Annotated[int, Field(gt=0, le=2147483647)]

# Numeric(10, 2): целая часть до 8 знаков.
MONEY_MIN = Decimal("0")
MONEY_MAX = Decimal("100000000")


def _strip_name(value: Any) -> Any:
    """Убрать пробелы по краям до проверки длины."""
    return value.strip() if isinstance(value, str) else value


def _validate_price(value: Any) -> Any:
    """Цена: конечное число не более чем с двумя знаками (иначе PostgreSQL молча округлит)."""
    if value is None:
        return value
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("price must be a decimal number") from exc
    if not number.is_finite():
        raise ValueError("price must be a finite number (NaN and Infinity are not allowed)")
    exponent = number.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -2:
        raise ValueError("price must have at most 2 decimal places")
    return number


def _validate_description(value: Any) -> Any:
    """None -> '' (колонка NOT NULL)."""
    return "" if value is None else value


def _url_to_str(value: Any) -> Any:
    """HttpUrl -> str: psycopg не адаптирует HttpUrl к text."""
    if isinstance(value, HttpUrl):
        return str(value)
    return value


class BrandRead(BaseModel):
    """Бренд без вложенных товаров."""

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
    """Карточка товара с размерами; price в JSON — строка (DecimalStr)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    brand_id: int
    price: DecimalStr
    description: str
    image_url: str | None
    sizes: list[ProductSizeRead]


class ProductWrite(BaseModel):
    """Вход для создания и замены карточки товара.

    Без id, created_at и sizes; лишние поля дают 422. При PUT пропущенные
    description и image_url сбрасываются к '' и null.
    """

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=120), BeforeValidator(_strip_name)]
    brand_id: BrandId
    price: Annotated[Decimal, Field(gt=0, lt=Decimal("100000000")), BeforeValidator(_validate_price)]
    description: Annotated[
        str, Field(max_length=2000), BeforeValidator(_validate_description)
    ] = ""
    image_url: Annotated[HttpUrl | None, BeforeValidator(_url_to_str)] = None

    def writable_values(self) -> dict[str, Any]:
        """Значения для записи в колонки products."""
        return {
            "name": self.name,
            "brand_id": self.brand_id,
            "price": self.price,
            "description": self.description,
            "image_url": str(self.image_url) if self.image_url is not None else None,
        }
