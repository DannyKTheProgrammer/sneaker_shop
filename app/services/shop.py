# Корзина: бизнес-операции поверх БД (M4, шаг «POST /cart/items»).
#
# Здесь нет ни FastAPI, ни HTTP-кодов: сервис отвечает на вопрос «что стало
# с корзиной», а «200/404/409» решает роутер (в этом шаге — общий обработчик
# ShopError из app/core/errors.py).
#
# ГЛАВНОЕ ОТЛИЧИЕ ОТ СЕРВИСОВ КАТАЛОГА: своя, СВЕЖАЯ сессия на операцию.
#
# Почему нельзя обойтись сессией из Depends(get_session): зависимость
# аутентификации (get_current_user) уже прочитала строку users ЭТОЙ ЖЕ
# сессией, то есть открыла транзакцию (autobegin) и держит снимок данных
# (READ COMMITTED — снимок на момент первого оператора). Дальше в этой
# транзакции блокировка FOR UPDATE и проверка остатка опирались бы на
# снимок, сделанный ДО того, как мы взяли блокировку: классическая гонка
# «прочитал старое — записал новое». Поэтому бизнес-операция открывает
# новую сессию (значит, новую транзакцию и свежий снимок), а из слоя
# аутентификации в неё передаётся только user.id — не ORM-объект, который
# привязан к чужой сессии.
#
# ПРОТОКОЛ БЛОКИРОВОК (общий для корзины и checkout, порядок обязателен):
#
#   1. строка users ЗАБЛОКИРОВАНА (SELECT ... FOR UPDATE) — это «замок
#      владельца». Все операции корзины и checkout одного пользователя
#      выстраиваются в очередь, поэтому две одновременные вставки не могут
#      прочитать quantity=1 и обе записать 2 (потерянное обновление);
#   2. строки product_sizes блокируются по возрастанию id (в этом шаге
#      размер один);
#   3. checkout дополнительно держит products FOR SHARE по возрастанию id.
#
# Единый порядок захвата (сначала users, потом product_sizes, потом
# products) — это защита от взаимной блокировки (deadlock): если бы один
# запрос шёл products → users, а другой users → products, PostgreSQL
# обнаружил бы цикл и убил одну из транзакций.
#
# Замки живут до COMMIT или ROLLBACK — то есть ровно одну бизнес-операцию.

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ShopConflict, ShopError, ShopNotFound
from app.database import SessionLocal
from app.models import CartItem, Product, ProductSize, User
from app.schemas.cart import CartItemAdd, CartItemRead

# SQLSTATE нарушения уникальности в PostgreSQL (как в services/auth.py).
UNIQUE_VIOLATION = "23505"

# Имя ограничения UNIQUE(user_id, product_size_id) из миграции M1. Нужно,
# чтобы отличить ожидаемый конфликт «эта пара уже в корзине» от любого
# другого нарушения целостности: второе маскировать нельзя.
CART_SIZE_UNIQUE = "uq_cart_size"

# Границы колонки quantity в cart_items: Integer = 32 бита со знаком.
INT4_MIN = -2147483648
INT4_MAX = 2147483647


class CartItemNotFound(ShopNotFound):
    """Позиции корзины нет ИЛИ она принадлежит другому пользователю (404)."""

    detail = "Cart item not found"


class ProductSizeNotFound(ShopNotFound):
    """Размера товара с таким id нет (404)."""

    detail = "Product size not found"


class CartStockConflict(ShopConflict):
    """Итоговое количество больше остатка на складе (409)."""

    detail = "Not enough stock for the requested quantity"


class CartConflict(ShopConflict):
    """Гонка на уникальности или иной конфликт целостности (409)."""

    detail = "Cart item could not be saved because of a concurrent change"


def new_session() -> Session:
    """Свежая сессия под одну бизнес-операцию.

    Возвращаем именно Session, а не контекстный менеджер: вызывающий код
    (сервисная функция) сам решает, когда коммитить и когда откатывать, и
    закрывает сессию в finally. Так владелец транзакции остаётся один —
    эта функция, а не FastAPI-зависимость.
    """
    return SessionLocal()


def _lock_user(session: Session, user_id: int) -> None:
    """Шаг 1: заблокировать строку владельца (замок на пользователя).

    `SELECT ... FROM users WHERE id = :id FOR UPDATE` заставляет PostgreSQL
    взять эксклюзивную блокировку строки. Вторая такая же операция по тому
    же пользователю будет ЖДАТЬ здесь, пока первая не закоммитит, — поэтому
    последовательность «прочитать quantity → прибавить → записать» не может
    перемешаться с другой такой же.

    `with_for_update(of=User)` — это FOR UPDATE OF users: блокируем строку
    именно таблицы users. Без `of` PostgreSQL (и SQLAlchemy) в запросе с
    JOIN заблокировал бы строки всех таблиц запроса; здесь JOIN нет, но
    явное указание таблицы фиксирует намерение и переживёт добавление JOIN.

    Пользователь не найден — 400 ShopError, а не 401: к этому моменту
    токен уже проверен по БД, поэтому «нет строки» означает не «плохой
    вход», а рассинхронизацию (например, аккаунт удалили в промежутке).
    Строка нужна ещё и потому, что user_id в cart_items — внешний ключ:
    без неё вставка упала бы на FK.
    """
    owner = session.execute(
        select(User).where(User.id == user_id).with_for_update(of=User)
    ).scalar_one_or_none()
    if owner is None:
        raise ShopError("Authenticated user no longer exists")


def _lock_product_size(session: Session, product_size_id: int) -> ProductSize:
    """Шаг 2: заблокировать строку размера и вернуть её.

    FOR UPDATE здесь нужен, чтобы остаток и цена не изменились между нашей
    проверкой и COMMIT: иначе параллельный checkout мог бы списать остаток,
    пока мы проверяем «хватает ли», и мы бы записали количество, которое
    склад уже не покрывает.

    Ленивой связи `ProductSize.product` здесь нет (в модели M1 у размера
    обратной связи с товаром не объявлено), поэтому карточку товара
    читаем явно — см. add_cart_item.
    """
    product_size = session.execute(
        select(ProductSize)
        .where(ProductSize.id == product_size_id)
        .with_for_update(of=ProductSize)
    ).scalar_one_or_none()
    if product_size is None:
        raise ProductSizeNotFound()
    return product_size


def _get_product(session: Session, product_id: int) -> Product:
    """Карточка товара (имя и цена) по id размера.

    FOR SHARE здесь НЕ берём намеренно: это шаг корзины, он остаток не
    списывает. Блокировку products FOR SHARE добавляет checkout — там она
    защищает цену от изменения в момент оформления. Для ответа «сколько
    стоит пара сейчас» достаточно прочитать цену в той же транзакции.
    """
    product = session.get(Product, product_id)
    if product is None:  # pragma: no cover - FK гарантирует существование
        raise ShopError("Product for this size no longer exists")
    return product


def _find_cart_item(session: Session, user_id: int, product_size_id: int) -> CartItem | None:
    """Позиция пары (пользователь, размер) или None, если её ещё нет.

    FOR UPDATE на этой строке не нужен: гонку двух вставок уже сериализует
    замок владельца (шаг 1). UNIQUE(user_id, product_size_id) остаётся
    страховкой: если замок когда-нибудь снимут, база не даст двум строкам
    описывать одну пару, и мы получим понятный 409 вместо тихого дубля.
    """
    return session.execute(
        select(CartItem).where(
            CartItem.user_id == user_id,
            CartItem.product_size_id == product_size_id,
        )
    ).scalar_one_or_none()


def _integrity_error_kind(exc: IntegrityError) -> str | None:
    """Имя нарушенного ограничения, если PostgreSQL его сообщил.

    Нужно только для лога: в ответ клиенту имя ограничения не попадает
    (это подсказка об устройстве схемы).
    """
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    return getattr(diag, "constraint_name", None)


def add_cart_item(session: Session, user_id: int, data: CartItemAdd) -> CartItemRead:
    """Добавить размер в корзину пользователя на delta единиц.

    Порядок шагов (он же протокол блокировок):

    1. заблокировать владельца (users FOR UPDATE) — только по user.id;
    2. заблокировать размер (product_sizes FOR UPDATE) и убедиться, что он
       существует: нет размера → ProductSizeNotFound (404);
    3. найти пару (user, size): если строки нет — создать с quantity=delta,
       если есть — прибавить delta к текущему количеству;
    4. проверить ИТОГОВОЕ количество: оно должно помещаться в int4 и не
       превышать остаток размера. Иначе CartStockConflict (409);
    5. зафиксировать транзакцию (commit) и вернуть позицию с товаром.

    Остаток склада НЕ уменьшается: корзина ничего не резервирует. Списание
    остатка — задача оформления заказа (checkout), где остаток проверяется
    повторно под блокировкой. Поэтому «двое положили в корзину последнюю
    пару» — норма, а «двое её купили» — нет.

    Проверка переполнения нужна отдельно от проверки остатка: quantity —
    это Integer (int4), и если бы мы позволили сумме выйти за 2147483647,
    PostgreSQL отклонил бы UPDATE с ошибкой numeric value out of range,
    то есть клиент получил бы 500 вместо понятного 409.
    """
    _lock_user(session, user_id)
    product_size = _lock_product_size(session, data.product_size_id)
    product = _get_product(session, product_size.product_id)

    item = _find_cart_item(session, user_id, data.product_size_id)
    if item is None:
        item = CartItem(
            user_id=user_id,
            product_size_id=data.product_size_id,
            quantity=data.quantity,
        )
        session.add(item)
    else:
        item.quantity = item.quantity + data.quantity

    if item.quantity > INT4_MAX or item.quantity < INT4_MIN:
        raise CartStockConflict(
            "Total quantity does not fit into the integer column"
        )
    if item.quantity > product_size.stock:
        # В сообщении для клиента — только числа, без имён колонок.
        raise CartStockConflict(
            f"Only {product_size.stock} item(s) of this size are in stock"
        )

    try:
        session.commit()
    except IntegrityError as exc:
        # Единственный ожидаемый случай — UNIQUE(user_id, product_size_id):
        # параллельная вставка той же пары. Остальные нарушения целостности
        # не маскируем — пусть всплывают как 500, это ошибка в коде.
        #
        # Имя ограничения читаем ДО rollback: после отката объект исключения
        # остаётся, но зависеть от этого не стоит.
        constraint = _integrity_error_kind(exc)
        session.rollback()
        if constraint == CART_SIZE_UNIQUE or _is_unique_violation(exc):
            raise CartConflict() from exc
        raise

    # Строка уже в БД; читаем её вместе с размером и товаром ОДНИМ запросом
    # на связь (selectinload), чтобы ответ не сделал ленивых запросов.
    saved = _load_cart_item(session, item.id)
    if saved is None:  # pragma: no cover - сразу после commit строки быть не может
        raise CartItemNotFound()
    return to_read(saved)


def _is_unique_violation(exc: IntegrityError) -> bool:
    """Нарушение уникальности (23505) в драйвере или в его причине.

    Та же проверка, что в services/auth.py: psycopg кладёт SQLSTATE в
    `exc.orig.diag.sqlstate`, но в разных версиях он оказывается то в
    `pgcode`, то в `__cause__` — проверяем все три места.
    """
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    if diag is not None and getattr(diag, "sqlstate", None) == UNIQUE_VIOLATION:
        return True
    if getattr(exc.orig, "pgcode", None) == UNIQUE_VIOLATION:
        return True
    cause = getattr(exc.orig, "__cause__", None)
    return getattr(cause, "sqlstate", None) == UNIQUE_VIOLATION


def _load_cart_item(session: Session, item_id: int) -> CartItem | None:
    """Позиция корзины вместе с размером и товаром (без ленивых запросов).

    Цепочка selectinload: CartItem → ProductSize → Product. Каждый уровень
    даёт ровно один дополнительный SELECT с IN (...), а не по запросу на
    строку, поэтому N+1 здесь невозможен.
    """
    stmt = (
        select(CartItem)
        .options(
            selectinload(CartItem.product_size).selectinload(ProductSize.product)
        )
        .where(CartItem.id == item_id)
    )
    return session.execute(stmt).scalar_one_or_none()


def to_read(item: CartItem) -> CartItemRead:
    """Собрать плоский ответ из ORM-объекта (id, товар, размер, subtotal)."""
    product_size = item.product_size
    product = product_size.product
    return CartItemRead(
        id=item.id,
        product_size_id=product_size.id,
        product_id=product.id,
        name=product.name,
        size=product_size.size,
        price=product.price,
        quantity=item.quantity,
        stock=product_size.stock,
        subtotal=_subtotal(item.quantity, product.price),
    )


def _subtotal(quantity: int, price: Decimal) -> Decimal:
    """Стоимость позиции: количество * цена за пару.

    Decimal на Decimal даёт точный результат (никаких float-погрешностей).
    Умножение на int допустимо: int приводится к Decimal без потери
    точности, а результат остаётся Decimal.
    """
    return price * quantity
