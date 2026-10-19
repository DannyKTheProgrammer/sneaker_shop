# Корзина (M4): POST /cart/items, GET /cart, PATCH и DELETE /cart/items/{item_id}.
#
# Этот роутер — про самого пользователя, а не про администратора. Поэтому
# здесь НЕТ require_admin: корзина принадлежит тому, кто предъявил токен, и
# администратор не имеет к чужой корзине никаких особых прав. Защита — та
# же зависимость get_current_user, что и у /auth/me.
#
# Владелец берётся ТОЛЬКО из проверенного токена: ни в теле запроса
# (extra="forbid" в CartItemAdd отклонит такую попытку 422), ни в
# query-параметрах поля user_id нет вовсе.
#
# Роутер подключается только там, где разрешена запись (app/store_main.py
# и app/main.py). Контрольная точка M2 (app/catalog_main.py) его не
# подключает, поэтому корзины в её OpenAPI нет вообще — там 404, а не 405.

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response, status

from app.routers.auth import get_current_user_id
from app.schemas.cart import CartItemAdd, CartItemRead, CartItemUpdate, CartRead
from app.services import shop as shop_service

router = APIRouter(prefix="/cart", tags=["cart"])

# Только id владельца: сама строка users нам больше не нужна, а её
# ORM-объект принадлежит сессии зависимости и «переезжать» в свежую
# транзакцию не должен (см. комментарий в app/services/shop.py).
CurrentUserId = Annotated[int, Depends(get_current_user_id)]

# id строки cart_items в пути. Обычный int с границами колонки int4: пути
# `/cart/items/abc`, `/cart/items/0` и `/cart/items/-1` отсекает Pydantic
# (422), и только корректный по форме, но несуществующий id доходит до
# запроса и превращается в 404.
ItemId = Annotated[int, Path(ge=1, le=2147483647, description="id строки cart_items")]


@router.get(
    "",
    response_model=CartRead,
    status_code=status.HTTP_200_OK,
    summary="Своя корзина: позиции и ориентировочная сумма",
    responses={
        200: {"description": "Позиции по возрастанию id и estimated_total; пустая корзина — [] и 0.00"},
        400: {"description": "Пользователь из токена больше не существует"},
        401: {"description": "Токен отсутствует или недействителен"},
    },
)
def read_cart(user_id: CurrentUserId) -> CartRead:
    """Вернуть свою корзину: позиции по возрастанию cart_items.id и сумму.

    Владелец берётся только из токена: параметра `user_id` у маршрута нет
    вовсе, поэтому чужую корзину этим запросом получить нельзя (лишний
    query-параметр FastAPI просто игнорирует — он не участвует в фильтре).

    Цена и остаток в каждой позиции — ТЕКУЩИЕ, из каталога: `price` из
    products, `stock` из product_sizes, `subtotal = price * quantity`.
    `estimated_total` — сумма этих subtotal, то есть оценка на сейчас. Она
    НЕ фиксируется в БД: изменится цена в каталоге — изменится и оценка.
    Точная, окончательная сумма появится только при оформлении заказа, где
    цена записывается снимком в `order_items.price_at_purchase` (история
    покупок при этом не зависит от будущих изменений каталога).

    Пустая корзина — это 200 с `{"items": [], "estimated_total": "0.00"}`,
    а не 404: «у вас пока ничего нет» — нормальное состояние, а не ошибка.

    Позиции без обёртки в объект вида {"items": [...]}: массив отдаётся
    внутри CartRead, потому что рядом лежит сумма. Порядок стабильный
    (ORDER BY cart_items.id), поэтому клиент может опираться на него при
    отрисовке списка.

    Сессия открывается заново внутри сервиса и закрывается сразу после
    возврата: DTO уже собран, ORM-объекты снаружи не живут.
    """
    with shop_service.new_session() as session:
        return shop_service.get_cart(session, user_id)


@router.post(
    "/items",
    response_model=CartItemRead,
    status_code=status.HTTP_200_OK,
    summary="Добавить размер в корзину (delta к количеству)",
    responses={
        200: {"description": "Позиция добавлена или её количество увеличено"},
        400: {"description": "Пользователь из токена больше не существует"},
        401: {"description": "Токен отсутствует или недействителен"},
        404: {"description": "Размера товара с таким id нет"},
        409: {"description": "Итоговое количество больше остатка на складе"},
        422: {"description": "Невалидные данные или лишние поля"},
    },
)
def add_cart_item(payload: CartItemAdd, user_id: CurrentUserId) -> CartItemRead:
    """Добавить `quantity` единиц размера `product_size_id` в корзину.

    `quantity` — это ДЕЛЬТА (прибавка): первый вызов создаёт позицию,
    повторный увеличивает её количество. Абсолютную установку делает
    PATCH /cart/items/{item_id} (маршрут ниже).

    Остаток НЕ уменьшается: корзина не резервирует товар. Проверяется лишь
    то, что итоговое количество не превышает остаток (иначе 409) — за
    вычетом остатка придёт очередь checkout.

    Сессия открывается заново внутри сервиса: зависимость аутентификации
    уже прочитала users и открыла транзакцию в своей сессии, а бизнес-
    операции нужен свежий снимок данных и свои блокировки (подробно —
    в app/services/shop.py).

    Ответ 200 (а не 201) в обоих случаях — и при вставке, и при увеличении:
    клиенту важно состояние позиции, а не то, была ли она создана; при этом
    идемпотентность не заявляется, потому что повторный вызов МЕНЯЕТ
    количество.
    """
    with shop_service.new_session() as session:
        return shop_service.add_cart_item(session, user_id, payload)


@router.patch(
    "/items/{item_id}",
    response_model=CartItemRead,
    status_code=status.HTTP_200_OK,
    summary="Установить абсолютное количество позиции",
    responses={
        200: {"description": "Количество заменено (не прибавлено), вернулась обновлённая позиция"},
        400: {"description": "Пользователь из токена больше не существует"},
        401: {"description": "Токен отсутствует или недействителен"},
        404: {"description": "Позиции с таким id нет ИЛИ она принадлежит другому пользователю"},
        409: {"description": "Запрошенное количество больше остатка на складе"},
        422: {"description": "Невалидные данные: quantity вне 1..2147483647, лишние поля, нечисловой id"},
    },
)
def update_cart_item(
    item_id: ItemId,
    payload: CartItemUpdate,
    user_id: CurrentUserId,
) -> CartItemRead:
    """Заменить количество позиции на `quantity` (абсолютное значение).

    `item_id` — это id строки `cart_items` (не id размера и не id товара):
    позиция адресуется своим идентификатором, а размер и товар в ней уже
    записаны.

    Изоляция корзин. Владелец берётся из токена, а позиция ищется ОДНОЙ
    выборкой с двумя условиями — `cart_items.id = :item_id AND
    cart_items.user_id = :user_id`. Поэтому:

    * чужой id неотличим от несуществующего: 404 и одинаковое тело ответа;
    * по коду ответа нельзя узнать, существует ли позиция у кого-то ещё;
    * администратор здесь тоже обычный пользователь: у роли нет доступа к
      чужим корзинам, и он получит те же 404.

    Количество — итоговое, а не прибавляемое (прибавка — это POST). Ноль и
    отрицательные отклоняет схема (422): чтобы убрать позицию, её удаляют.
    Количество больше остатка — 409, и данные при этом не меняются.
    """
    with shop_service.new_session() as session:
        return shop_service.update_cart_item(session, user_id, item_id, payload)


@router.delete(
    "/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить свою позицию из корзины",
    responses={
        204: {"description": "Позиция удалена, тело ответа пустое"},
        400: {"description": "Пользователь из токена больше не существует"},
        401: {"description": "Токен отсутствует или недействителен"},
        404: {"description": "Позиции с таким id нет ИЛИ она принадлежит другому пользователю"},
        422: {"description": "Нечисловой или неположительный id позиции"},
    },
)
def delete_cart_item(item_id: ItemId, user_id: CurrentUserId) -> Response:
    """Удалить свою позицию; успех — 204 с пустым телом.

    Тот же принцип изоляции, что у PATCH: одна выборка по (id, user_id).
    Чужая позиция → 404, повторное удаление → 404 (строки уже нет), и ни
    один отказ ничего не меняет в БД.

    Остаток не трогаем: корзина не резервирует товар, поэтому удаление
    позиции не возвращает и не списывает ни одной пары.
    """
    with shop_service.new_session() as session:
        shop_service.delete_cart_item(session, user_id, item_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
