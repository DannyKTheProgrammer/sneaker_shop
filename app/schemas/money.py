# Decimal в ответах API: в JSON — строка без экспоненты ("7990.00", "100").
#
# Раньше то же делал `json_encoders={Decimal: ...}` в model_config каждой
# схемы ответа. В Pydantic 2 это устаревший механизм (PydanticDeprecatedSince20,
# удалят в V3), поэтому формат вынесен в тип поля.
#
# Поведение сохранено точно:
#   * JSON — format(value, "f"), как у прежнего энкодера: Decimal("1E+2")
#     уходит как "100", а не "1E+2";
#   * в Python (model_dump() без mode="json") значение остаётся Decimal —
#     сервисы складывают subtotal без float;
#   * в OpenAPI поле описано так же, как Pydantic описывал Decimal:
#     строка с тем же pattern (WithJsonSchema только для режима сериализации,
#     описание входа не меняется).
#
# Тип ставится ровно на те поля, где раньше действовал json_encoders: он
# относился только к полям своей модели, не к вложенным (например,
# ProductSizeRead.size внутри ProductRead его не получал и не получает).

from decimal import Decimal
from typing import Annotated

from pydantic import PlainSerializer, WithJsonSchema

# Тот же шаблон, что Pydantic 2 генерирует для Decimal в JSON Schema.
DECIMAL_STRING_PATTERN = r"^(?!^[-+.]*$)[+-]?0*\d*\.?\d*$"


def _plain_decimal(value: Decimal) -> str:
    return format(value, "f")


DecimalStr = Annotated[
    Decimal,
    PlainSerializer(_plain_decimal, return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "pattern": DECIMAL_STRING_PATTERN}, mode="serialization"),
]
