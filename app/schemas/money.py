# Decimal в JSON-ответах как строка без экспоненты ("7990.00", "100");
# в Python значение остаётся Decimal.

from decimal import Decimal
from typing import Annotated

from pydantic import PlainSerializer, WithJsonSchema

# Шаблон Pydantic 2 для Decimal в JSON Schema.
DECIMAL_STRING_PATTERN = r"^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$"


def _plain_decimal(value: Decimal) -> str:
    return format(value, "f")


DecimalStr = Annotated[
    Decimal,
    PlainSerializer(_plain_decimal, return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "pattern": DECIMAL_STRING_PATTERN}, mode="serialization"),
]
