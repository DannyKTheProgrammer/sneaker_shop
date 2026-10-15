# Схемы каталога (M2 — чтение, M3 — вход для записи).
#
# Зачем отдельный слой: ORM-модель описывает таблицу, а схема — то, что
# реально уходит клиенту и принимается от клиента. Это разные контракты:
# в модели есть внутренние поля (например, hashed_password у User),
# которых в ответе быть не должно, а во входе не должно быть полей,
# которыми управляет сервер (id, created_at).
#
# from_attributes=True разрешает собирать схему прямо из ORM-объекта
# (чтение атрибутов), без ручного перекладывания полей.

from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    HttpUrl,
)

# brand_id ссылается на brands.id: Integer в PostgreSQL = 32 бита,
# поэтому больше 2147483647 база всё равно не примет.
BrandId = Annotated[int, Field(gt=0, le=2147483647)]

# Границы для Numeric(10, 2): 10 знаков всего, 2 после точки,
# значит целая часть — до 8 знаков.
MONEY_MIN = Decimal("0")
MONEY_MAX = Decimal("100000000")


def _strip_name(value: Any) -> Any:
    """Убрать пробелы по краям до проверки длины.

    «   » (одни пробелы) после strip становится пустой строкой и не
    проходит min_length=1 — это и требуется: имя из пробелов не имя.
    """
    return value.strip() if isinstance(value, str) else value


def _validate_price(value: Any) -> Any:
    """Цена: конечное число, больше нуля, не больше двух знаков после точки.

    Зачем проверка знаков, если колонка Numeric(10, 2): PostgreSQL молча
    округлил бы 10.999 до 11.00, и клиент получил бы товар по цене, которую
    не называл. Лучше отказать на входе.
    """
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
    """description по умолчанию — пустая строка, а не NULL.

    Колонка объявлена NOT NULL DEFAULT '', поэтому None превращаем в '':
    иначе Pydantic отдал бы None и вставка упала бы на ограничении.
    """
    return "" if value is None else value


def _url_to_str(value: Any) -> Any:
    """HttpUrl -> str перед колонкой Text.

    Нужно не для ответа (там json_encoders сам делает строку), а для записи:
    psycopg не умеет адаптировать объект HttpUrl к типу text.
    """
    if isinstance(value, HttpUrl):
        return str(value)
    return value


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


class ProductWrite(BaseModel):
    """Вход для создания и замены карточки товара.

    Контракт специально отделён от ProductRead и от ORM-модели:

    * во входе НЕТ id и created_at — их назначает сервер;
    * во входе НЕТ sizes — размеры и остатки читаются из seed и этим
      контрактом не редактируются вообще (управление остатком — отдельная
      задача, не «поле карточки»);
    * лишние поля запрещены (extra="forbid"): опечатка в имени поля или
      попытка прислать id/is_admin обернётся 422, а не молчаливым
      игнорированием.

    PUT по этому контракту ЗАМЕНЯЕТ редактируемые поля карточки:
    name, brand_id и price обязательны, а пропущенные description и
    image_url сбрасываются к значениям по умолчанию ('' и null). Это
    осознанно: PUT — полная замена представления, а не частичное
    обновление (для частичного был бы PATCH).
    """

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=120), BeforeValidator(_strip_name)]
    brand_id: BrandId
    # Numeric(10, 2): 10 знаков всего, 2 после точки => целая часть до 8 знаков.
    price: Annotated[Decimal, Field(gt=0, lt=Decimal("100000000")), BeforeValidator(_validate_price)]
    description: Annotated[
        str, Field(max_length=2000), BeforeValidator(_validate_description)
    ] = ""
    # HttpUrl проверяет схему и хост; ExtraHttpUrl не нужен: 'ftp://' и
    # 'not-a-url' отклоняются. По умолчанию — null.
    image_url: Annotated[HttpUrl | None, BeforeValidator(_url_to_str)] = None

    def writable_values(self) -> dict[str, Any]:
        """Значения для записи в колонки products (image_url уже строка)."""
        return {
            "name": self.name,
            "brand_id": self.brand_id,
            "price": self.price,
            "description": self.description,
            "image_url": str(self.image_url) if self.image_url is not None else None,
        }
