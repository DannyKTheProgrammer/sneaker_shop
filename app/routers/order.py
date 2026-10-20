# Маршруты заказа (M5, шаг 2: POST /orders создаёт заказ).
#
# POST /orders — маршрут САМОГО пользователя (как корзина), без
# require_admin: заказ оформляет тот, кто предъявил токен. Владелец и цены
# берутся из БД, а не из тела запроса, поэтому тело либо отсутствует, либо
# пустое: см. OrderCreate (extra="forbid").
#
# На этом шаге создаются строки `orders` и `order_items` в одной транзакции.
# СПИСАНИЕ ОСТАТКА и ОЧИСТКА КОРЗИНЫ — следующий шаг (явно, чтобы не
# считать текущее поведение финальным).
#
# Роутер подключается только там, где разрешена запись (store_main), чтобы
# контрольная точка M2 осталась «только чтение каталога».

from typing import Annotated

from fastapi import APIRouter, Body, Depends, status

from app.routers.auth import get_current_user_id
from app.schemas.order import OrderCreate, OrderRead
from app.services import shop as shop_service

router = APIRouter(prefix="/orders", tags=["orders"])

CurrentUserId = Annotated[int, Depends(get_current_user_id)]


@router.post(
    "",
    response_model=OrderRead,
    status_code=status.HTTP_201_CREATED,
    summary="Создать заказ из корзины (снимок цен)",
    responses={
        201: {"description": "Заказ создан: id, status, total, created_at, позиции-снимки"},
        401: {"description": "Токен отсутствует или недействителен"},
        409: {"description": "Корзина пуста / не хватает остатка / сумма больше предела Numeric(12,2)"},
        422: {"description": "В теле есть поля (user_id, total, _test_hook, ...) или тело не JSON"},
    },
)
def create_order(
    user_id: CurrentUserId,
    payload: Annotated[OrderCreate | None, Body(description="Тело необязательно; допустимо только {}")] = None,
) -> OrderRead:
    """Создать заказ из своей корзины.

    Тело запроса: отсутствует или `{}`. `payload` не участвует в расчётах —
    он существует ровно для того, чтобы Pydantic отверг любое присланное
    поле (422): владелец берётся из токена, сумма — из БД. Без этого клиент
    мог бы попытаться подставить `user_id` или `total`.

    Что происходит внутри (порядок важен):

    1. новая сессия на операцию (авторизация уже открыла свою транзакцию
       чтением User — сюда она не «переезжает»);
    2. `users FOR UPDATE`;
    3. читается состав корзины; пустая корзина → 409 `Cart is empty`;
    4. `product_sizes FOR UPDATE` по возрастанию id;
    5. `products FOR SHARE` по возрастанию id;
    6. проверяются ВСЕ количества против актуального остатка; при нехватке
       любой позиции → 409 и в БД не меняется ничего;
    7. сумма считается как Decimal по ценам из БД, проверяется предел
       Numeric(12,2) = 9999999999.99;
    8. `Order(status='created', total)` → `flush` (получить id) →
       `OrderItem` на каждую позицию с `quantity` и `price_at_purchase` из
       заблокированного Product → один `commit`;
    9. DTO собирается до закрытия сессии.

    Ответ `201` и тело `OrderRead`: позиции содержат СНИМОК цены
    (`price_at_purchase`) и количество; `subtotal` считается из них.
    Названия товара в истории нет — в схеме у order_items нет такой
    колонки (появится только с миграцией, если понадобится).

    Чего пока нет: остаток НЕ списывается, корзина НЕ очищается. Поэтому
    повторный POST создаст второй заказ по тем же строкам — это временное
    состояние до следующего шага, а не финальное поведение.

    Блокировки не «бронируют» товар: они держатся до конца транзакции.
    После неё другой заказ может списать остаток, а администратор —
    изменить цену; на уже созданный заказ это не влияет, потому что в нём
    лежит снимок цены.
    """
    # Тело намеренно не используется: контракт — «ничего не прислать».
    _ = payload
    with shop_service.new_session() as session:
        return shop_service.create_order(session, user_id)
