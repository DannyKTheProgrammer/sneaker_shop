# Корзина (M4, шаг «POST /cart/items»).
#
# Этот роутер — про самого пользователя, а не про администратора. Поэтому
# здесь НЕТ require_admin: корзина принадлежит тому, кто предъявил токен, и
# администратор не имеет к чужой корзине никаких особых прав. Защита — та
# же зависимость get_current_user, что и у /auth/me.
#
# Владелец берётся ТОЛЬКО из проверенного токена: в теле запроса поля
# user_id нет (extra="forbid" в CartItemAdd отклонит такую попытку 422).
#
# Роутер подключается только там, где разрешена запись (app/store_main.py
# и app/main.py). Контрольная точка M2 (app/catalog_main.py) его не
# подключает, поэтому корзины в её OpenAPI нет вообще — там 404, а не 405.

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.routers.auth import get_current_user_id
from app.schemas.cart import CartItemAdd, CartItemRead
from app.services import shop as shop_service

router = APIRouter(prefix="/cart", tags=["cart"])

# Только id владельца: сама строка users нам больше не нужна, а её
# ORM-объект принадлежит сессии зависимости и «переезжать» в свежую
# транзакцию не должен (см. комментарий в app/services/shop.py).
CurrentUserId = Annotated[int, Depends(get_current_user_id)]


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
    повторный увеличивает её количество. Абсолютную установку делает PATCH
    (следующая часть M4).

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
