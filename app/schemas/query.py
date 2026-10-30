# Query-параметры GET /products; числовые ограничения задаёт сам Pydantic.

from decimal import Decimal
from typing import Any, Annotated

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)

from app.schemas.catalog import MONEY_MAX, MONEY_MIN

Money = Annotated[
    Decimal, Field(ge=MONEY_MIN, lt=MONEY_MAX, max_digits=10, decimal_places=2)
]

SizeValue = Annotated[Decimal, Field(gt=0, le=999, decimal_places=1)]

Limit = Annotated[int, Field(ge=1, le=100)]

Offset = Annotated[int, Field(ge=0)]


def _strip(value: Any) -> Any:
    """Убрать пробелы по краям; пустая строка становится None (фильтр не задан)."""
    if not isinstance(value, str):
        return value
    trimmed = value.strip()
    return trimmed or None


# str | None: _strip может вернуть None.
Search = Annotated[str | None, Field(max_length=100), BeforeValidator(_strip)]


def _bounds_ordered(value: "ProductFilters") -> "ProductFilters":
    """min_price не может быть больше max_price."""
    if (
        value.min_price is not None
        and value.max_price is not None
        and value.min_price > value.max_price
    ):
        raise ValueError("min_price must not be greater than max_price")
    return value


class ProductFilters(BaseModel):
    """Query-параметры списка товаров; условия сочетаются через AND."""

    model_config = ConfigDict(extra="forbid")

    brand_id: Annotated[int, Field(gt=0, le=2147483647)] | None = None
    min_price: Money | None = None
    max_price: Money | None = None
    size: SizeValue | None = None
    search: Search | None = None
    limit: Limit = 20
    offset: Offset = 0

    _check_bounds = model_validator(mode="after")(_bounds_ordered)
