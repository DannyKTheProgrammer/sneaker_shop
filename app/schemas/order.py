# Схемы заказа (M5, шаг 2: POST /orders создаёт заказ).
#
# Вход заказа ПУСТОЙ по смыслу: клиент не присылает ни владельца, ни сумму,
# ни позиции. Владелец берётся из проверенного токена, позиции — из корзины
# в БД, сумма считается по ценам из БД. Поэтому:
#
#   * OrderCreate — модель БЕЗ полей и с extra="forbid": допустимо тело
#     `{}` или отсутствие тела, а ЛЮБОЕ присланное поле (user_id, total,
#     _test_hook, items, ...) отклоняется 422. Это главная защита контракта:
#     клиент не может ни подставить чужого владельца, ни назвать свою цену.
#
#   * OrderRead — ответ на СОЗДАННЫЙ заказ: id, status, total, created_at и
#     позиции. Позиция — это снимок: `price_at_purchase` (цена ОДНОЙ пары на
#     момент покупки) и `quantity`. subtotal считается из этих двух величин.
#
# Названия товара в позиции заказа НЕТ намеренно: в схеме M1 у order_items
# нет колонки name, история хранит только ссылку на размер
# (product_size_id), количество и цену. Если когда-нибудь понадобится имя в
# истории (например, товар переименовали), это будет отдельная миграция:
# добавить снимок name в order_items. Добавлять сюда «текущее» name из
# каталога было бы обманом: это не часть истории заказа.
#
# Деньги — Decimal и уходят в JSON строкой ("21970.00", "7990.00"), как во
# всём проекте: float для денег не используется.

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.schemas.query import Limit, Offset


class OrderListParams(BaseModel):
    """Query-параметры GET /orders: только страница, никаких фильтров.

    `limit` 1..100 (по умолчанию 20) и `offset` >= 0 — те же типы, что у
    каталога (app/schemas/query.py), поэтому `limit=0`, `limit=101`,
    `offset=-1` и `limit=abc` дают 422 до обращения к БД. `extra="forbid"`
    отвергает лишнее, например `?user_id=2`: владелец берётся только из токена.
    """

    model_config = ConfigDict(extra="forbid")

    limit: Limit = 20
    offset: Offset = 0


class OrderCreate(BaseModel):
    """Вход POST /orders: полей нет вообще.

    Пустая модель — это не «недоделка», а контракт: всё, что нужно для
    заказа, сервер берёт сам (user.id из токена, корзину и цены из БД).
    Поэтому любое поле, присланное клиентом, — ошибка: `extra="forbid"`
    превращает попытку `{"user_id": 2}` или `{"total": "1.00"}` в 422.
    """

    model_config = ConfigDict(extra="forbid")


class OrderItemRead(BaseModel):
    """Позиция СОЗДАННОГО заказа (снимок, а не вид на текущий каталог).

    `price_at_purchase` — цена, которая записана в `order_items` в момент
    оформления. Она не меняется, даже если товар подорожает или подешевеет.
    `subtotal = price_at_purchase * quantity` — считает сервер.

    `product_size_id` остаётся ссылкой на строку размеров: по ней при
    необходимости можно найти товар, но цена из каталога к истории уже
    отношения не имеет.
    """

    model_config = ConfigDict(
        json_encoders={Decimal: lambda value: format(value, "f")},
    )

    product_size_id: int
    quantity: int
    price_at_purchase: Decimal
    subtotal: Decimal


class OrderRead(BaseModel):
    """Созданный заказ: шапка и позиции-снимки.

    `total` — снимок суммы на момент оформления (`orders.total`), а не
    пересчёт по текущим ценам: пересчёт дал бы другую цифру после изменения
    каталога, и история стала бы недостоверной.

    `created_at` заполняет сервер БД (`func.now()`), поэтому в ответе время
    создания, а не время клиента.
    """

    model_config = ConfigDict(
        json_encoders={Decimal: lambda value: format(value, "f")},
    )

    id: int
    status: str
    total: Decimal
    created_at: datetime
    items: list[OrderItemRead]
