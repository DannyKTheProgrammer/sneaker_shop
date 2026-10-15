# Схемы параметров запроса для GET /products (M3).
#
# Отдельный модуль от catalog.py: там тела запросов и ответов (JSON),
# здесь — query-параметры. Общее у них одно: типы и ограничения.
#
# Числовые ограничения делает сам Pydantic, без ручных валидаторов:
#   Field(ge=0, max_digits=10, decimal_places=2)  ->  >= 0, не больше двух
#       знаков после точки, не больше 8 знаков до точки, NaN/Infinity
#       отклоняются как не конечные числа;
#   Field(gt=0, le=999, decimal_places=1)         ->  размер 0 < size <= 999
#       с одним дробным знаком.
# Это проверено отдельным прогоном: '10.999' -> 422, 'NaN' -> 422,
# '42.55' -> 422, '-1' -> 422.

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

# Денежные границы включительные: >= min_price и <= max_price.
Money = Annotated[
    Decimal, Field(ge=MONEY_MIN, lt=MONEY_MAX, max_digits=10, decimal_places=2)
]

# Размер: положительный, не больше 999, один дробный знак.
SizeValue = Annotated[Decimal, Field(gt=0, le=999, decimal_places=1)]

# limit: по умолчанию 20, допустимо 1..100.
Limit = Annotated[int, Field(ge=1, le=100)]

# offset: по умолчанию 0, отрицательный запрещён.
Offset = Annotated[int, Field(ge=0)]


def _strip(value: Any) -> Any:
    """Убрать пробелы по краям у строковых query-параметров.

    После strip пустая строка превращается в None, то есть «фильтр не
    задан»: search='   ' ничего не сужает, а не ищет пробел.
    """
    if not isinstance(value, str):
        return value
    trimmed = value.strip()
    return trimmed or None


# str | None, а не просто str: валидатор _strip может вернуть None, и тип
# обязан это допускать, иначе Pydantic отвергнет собственный результат
# («Input should be a valid string») — так и было до правки: search='   '
# давал 422 вместо «фильтр не задан».
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
    """Query-параметры списка товаров.

    Валидация собрана здесь, а не в роутере: FastAPI принимает эту модель
    как обычную зависимость, поэтому 422 приходит из одного места, а
    обработчик получает уже проверенные значения.

    Любая комбинация параметров допустима; условия сочетаются через AND.
    Не задан параметр — соответствующего условия в SQL просто нет.
    """

    model_config = ConfigDict(extra="forbid")

    brand_id: Annotated[int, Field(gt=0, le=2147483647)] | None = None
    min_price: Money | None = None
    max_price: Money | None = None
    size: SizeValue | None = None
    search: Search | None = None
    limit: Limit = 20
    offset: Offset = 0

    _check_bounds = model_validator(mode="after")(_bounds_ordered)
