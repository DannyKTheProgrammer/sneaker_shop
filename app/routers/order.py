# Маршруты заказа (M5): POST /orders, GET /orders, GET /orders/{order_id},
# PATCH /orders/{order_id}/status (администратор).
#
# POST /orders — маршрут САМОГО пользователя (как корзина), без
# require_admin: заказ оформляет тот, кто предъявил токен. Владелец и цены
# берутся из БД, а не из тела запроса, поэтому тело либо отсутствует, либо
# пустое: см. OrderCreate (extra="forbid").
#
# Одна транзакция: `orders`, `order_items` (снимок цены), списание остатка и
# очистка своей корзины. Гонка двух покупателей за последнюю пару: второй
# ждёт на product_sizes FOR UPDATE и после commit первого получает 409
# (проверено `_checkout_race_probe.py`).
#
# Роутер подключается только там, где разрешена запись (final_main), чтобы
# сборка M2 (include_writes=False) осталась «только чтение каталога».

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Path, Query, status

from app.dependencies import get_current_admin_id, get_current_user_id
from app.schemas.order import OrderCreate, OrderListParams, OrderRead, OrderStatusUpdate
from app.services import shop as shop_service

router = APIRouter(prefix="/orders", tags=["orders"])

CurrentUserId = Annotated[int, Depends(get_current_user_id)]
CurrentAdminId = Annotated[int, Depends(get_current_admin_id)]

# Границы int4, как у ItemId корзины: orders.id — Integer.
OrderId = Annotated[int, Path(ge=1, le=2147483647, description="id заказа")]


@router.get(
    "",
    response_model=list[OrderRead],
    summary="Мои заказы (новые первыми)",
    responses={
        401: {"description": "Токен отсутствует или недействителен"},
        422: {"description": "limit вне 1..100, offset < 0 или лишний параметр"},
    },
)
def list_orders(
    user_id: CurrentUserId,
    page: Annotated[OrderListParams, Query()],
) -> list[OrderRead]:
    """Заказы текущего пользователя: `ORDER BY id DESC`, `limit`/`offset`.

    Только свои: фильтр `orders.user_id = <id из токена>`, параметра
    «чей заказ» нет. Пустая история — `[]`, а не 404. Цены в позициях —
    снимок `price_at_purchase`, поэтому правка каталога историю не меняет.
    """
    return shop_service.list_orders(user_id, page.limit, page.offset)


@router.get(
    "/{order_id}",
    response_model=OrderRead,
    summary="Мой заказ по id",
    responses={
        401: {"description": "Токен отсутствует или недействителен"},
        404: {"description": "Заказа нет или он чужой (в том числе для администратора)"},
        422: {"description": "order_id не целое или вне 1..2147483647"},
    },
)
def get_order(order_id: OrderId, user_id: CurrentUserId) -> OrderRead:
    """Один заказ: `WHERE id = :order_id AND user_id = :user_id`.

    Чужой id неотличим от несуществующего (404 с одинаковым телом). Это
    личный маршрут: роль не проверяется, администратор чужих заказов здесь
    тоже не видит.
    """
    return shop_service.get_order(user_id, order_id)


@router.patch(
    "/{order_id}/status",
    response_model=OrderRead,
    summary="Сменить статус заказа (только администратор)",
    responses={
        200: {"description": "Статус сменён, или он уже был таким (без изменений)"},
        401: {"description": "Токен отсутствует или недействителен"},
        403: {"description": "Пользователь не администратор"},
        404: {"description": "Заказа нет"},
        409: {"description": "Переход не разрешён: пропуск шага или обратный ход"},
        422: {"description": "Неизвестный статус, лишнее поле, тело не JSON, плохой id"},
    },
)
def update_order_status(
    order_id: OrderId,
    payload: OrderStatusUpdate,
    admin_id: CurrentAdminId,
) -> OrderRead:
    """Учебное правило: `created → processing → completed`, только вперёд.

    Повтор текущего статуса — 200 без записи; пропуск и обратный ход — 409.
    `get_current_admin_id` отсекает не-администратора (403) в короткой
    auth-сессии, которая закрывается до вызова сервиса; в сервис уходит
    только id, и там роль перечитывается в свежей транзакции
    (`users FOR SHARE`) перед `orders FOR UPDATE`. Остаток, позиции заказа
    и корзина не меняются.
    """
    return shop_service.update_order_status(admin_id, order_id, payload.status)


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

    1. новая сессия на операцию (авторизация прочитала User в своей
       короткой сессии и уже закрыла её — сюда приходит только id);
    2. `users FOR UPDATE`;
    3. читается состав корзины; пустая корзина → 409 `Cart is empty`;
    4. `product_sizes FOR UPDATE` по возрастанию id;
    5. `products FOR SHARE` по возрастанию id;
    6. проверяются ВСЕ количества против актуального остатка; при нехватке
       любой позиции → 409 и в БД не меняется ничего;
    7. сумма считается как Decimal по ценам из БД, проверяется предел
       Numeric(12,2) = 9999999999.99;
    8. `Order(status='created', total)` → `flush` (получить id) →
       `OrderItem` на каждую позицию (`price_at_purchase` — снимок цены) →
       `UPDATE product_sizes SET stock = stock - :qty WHERE id = :id AND
       stock >= :qty` по возрастанию id → `DELETE FROM cart_items WHERE
       user_id = :uid` (только своя корзина) → ОДИН `commit` на выходе из
       менеджера транзакции;
    9. DTO собирается ПОСЛЕ успешного commit (до этого 201 не отдаётся) и
       после закрытия сессии — из простых значений, без ORM-объектов.

    Ответ `201` и тело `OrderRead`: позиции содержат СНИМОК цены
    (`price_at_purchase`) и количество; `subtotal` считается из них.
    Названия товара в истории нет — в схеме у order_items нет такой
    колонки (появится только с миграцией, если понадобится).

    Атомарность: заказ, позиции, списание остатка и очистка корзины — одна
    транзакция. Любое исключение (в том числе гонка остатка, `rowcount=0`)
    откатывает ВСЁ: ни заказа, ни позиций, ни изменённых остатков, ни
    очищенной корзины. Поэтому 201 — это «уже зафиксировано», а не «почти
    получилось».

    После успешного заказа корзина пуста, поэтому повторный `POST /orders`
    вернёт `409 Cart is empty`. Автоматических повторов нет: если исход
    неизвестен (обрыв соединения), клиент повторяет сам — сервер не угадывает.

    Блокировки не «бронируют» товар: они держатся до конца транзакции.
    После неё другой заказ может списать остаток, а администратор —
    изменить цену; на уже созданный заказ это не влияет, потому что в нём
    лежит снимок цены.
    """
    # Тело намеренно не используется: контракт — «ничего не прислать».
    _ = payload
    return shop_service.create_order(user_id)
