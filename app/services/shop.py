# Корзина и заказы. Без FastAPI и HTTP-кодов (их выбирает роутер).
#
# Каждая операция работает в своей свежей сессии/транзакции (transaction()), а не в
# сессии запроса: та уже держит снимок после аутентификации. Из auth приходит только user.id.
#
# Порядок блокировок (против deadlock): users FOR UPDATE -> product_sizes FOR UPDATE
# по возрастанию id -> products FOR SHARE по возрастанию id. Замки живут до COMMIT/ROLLBACK.

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ShopConflict, ShopError, ShopForbidden, ShopNotFound
from app.database import SessionLocal
from app.models import CartItem, Order, OrderItem, Product, ProductSize, User
from app.schemas.cart import CartItemAdd, CartItemRead, CartItemUpdate, CartRead
from app.schemas.order import OrderItemRead, OrderRead
from app.services.unit_of_work import transaction

# SQLSTATE нарушения уникальности в PostgreSQL.
UNIQUE_VIOLATION = "23505"

# UNIQUE(user_id, product_size_id): отличает конфликт «пара уже в корзине» от прочих нарушений.
CART_SIZE_UNIQUE = "uq_cart_size"

# Предел orders.total (Numeric(12,2)); сверх него отказываем 409 вместо 500.
MAX_ORDER_TOTAL = Decimal("9999999999.99")

# Начальный статус заказа; должен проходить CHECK status в БД.
ORDER_STATUS_CREATED = "created"

# Денежный ноль с двумя знаками: пустая корзина отдаёт "0.00".
MONEY_ZERO = Decimal("0.00")

# Границы int4 (cart_items.quantity).
INT4_MIN = -2147483648
INT4_MAX = 2147483647


class CartItemNotFound(ShopNotFound):
    """Позиции корзины нет или она чужая (404)."""

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


class CartEmptyConflict(ShopConflict):
    """В корзине нет ни одной позиции (409)."""

    detail = "Cart is empty"


class OrderStockConflict(ShopConflict):
    """Хотя бы одной позиции не хватает на складе (409)."""

    detail = "Not enough stock for one of the cart items"


class OrderTotalConflict(ShopConflict):
    """Сумма заказа не влезает в Numeric(12,2) (409)."""

    detail = "Order total exceeds the allowed maximum"


class OrderNotFound(ShopNotFound):
    """Заказа нет или он чужой (404)."""

    detail = "Order not found"


class OrderStatusConflict(ShopConflict):
    """Переход статуса не разрешён (409)."""

    detail = "Order status transition is not allowed"


def new_session() -> Session:
    """Вернуть обычную SessionLocal() для тестов и инструментов, не для сервисов."""
    return SessionLocal()


def _lock_user(session: Session, user_id: int) -> None:
    """Заблокировать строку владельца (users FOR UPDATE), сериализуя его операции.

    Пользователь не найден -> ShopError (400): токен уже проверен, значит аккаунт удалили.
    """
    owner = session.execute(
        select(User).where(User.id == user_id).with_for_update(of=User)
    ).scalar_one_or_none()
    if owner is None:
        raise ShopError("Authenticated user no longer exists")


def _lock_product_size(session: Session, product_size_id: int) -> ProductSize:
    """Заблокировать размер (product_sizes FOR UPDATE) и вернуть его.

    Размера нет -> ProductSizeNotFound.
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
    """Прочитать товар без блокировки (FOR SHARE берёт только checkout)."""
    product = session.get(Product, product_id)
    if product is None:  # pragma: no cover - FK гарантирует существование
        raise ShopError("Product for this size no longer exists")
    return product


def _find_cart_item(session: Session, user_id: int, product_size_id: int) -> CartItem | None:
    """Найти позицию по паре (пользователь, размер) или None."""
    return session.execute(
        select(CartItem).where(
            CartItem.user_id == user_id,
            CartItem.product_size_id == product_size_id,
        )
    ).scalar_one_or_none()


def _integrity_error_kind(exc: IntegrityError) -> str | None:
    """Вернуть имя нарушенного ограничения, если драйвер его сообщил."""
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    return getattr(diag, "constraint_name", None)


def add_cart_item(user_id: int, data: CartItemAdd) -> CartItemRead:
    """Добавить размер в корзину (POST /cart/items) в своей транзакции."""
    with transaction() as session:
        return _add_cart_item(session, user_id, data)


def _add_cart_item(session: Session, user_id: int, data: CartItemAdd) -> CartItemRead:
    """Добавить размер или увеличить количество; commit делает transaction().

    Итоговое количество должно влезать в int4 и не превышать остаток, иначе
    CartStockConflict. Остаток не списывается: корзина ничего не резервирует.
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
        raise CartStockConflict(
            f"Only {product_size.stock} item(s) of this size are in stock"
        )

    try:
        session.flush()
    except IntegrityError as exc:
        # Маскируем только гонку на uq_cart_size; прочие нарушения пробрасываем.
        if _integrity_error_kind(exc) == CART_SIZE_UNIQUE or _is_unique_violation(exc):
            raise CartConflict() from exc
        raise

    saved = _load_cart_item(session, item.id)
    if saved is None:  # pragma: no cover - сразу после flush строки быть не может
        raise CartItemNotFound()
    return to_read(saved)


def _get_owned_item(session: Session, user_id: int, item_id: int) -> CartItem | None:
    """Найти позицию по id и владельцу одним запросом или None.

    Чужая и несуществующая позиции неотличимы (оба 404). Блокировки нет:
    строку защищает замок владельца.
    """
    return session.execute(
        select(CartItem).where(
            CartItem.id == item_id,
            CartItem.user_id == user_id,
        )
    ).scalar_one_or_none()


def update_cart_item(user_id: int, item_id: int, data: CartItemUpdate) -> CartItemRead:
    """Установить абсолютное количество позиции (PATCH) в своей транзакции."""
    with transaction() as session:
        return _update_cart_item(session, user_id, item_id, data)


def _update_cart_item(
    session: Session, user_id: int, item_id: int, data: CartItemUpdate
) -> CartItemRead:
    """Заменить количество позиции; commit делает transaction().

    Нет позиции -> CartItemNotFound; количество больше остатка -> CartStockConflict.
    """
    _lock_user(session, user_id)

    item = _get_owned_item(session, user_id, item_id)
    if item is None:
        raise CartItemNotFound()

    product_size = _lock_product_size(session, item.product_size_id)
    product = _get_product(session, product_size.product_id)

    if data.quantity > product_size.stock:
        raise CartStockConflict(
            f"Only {product_size.stock} item(s) of this size are in stock"
        )

    item.quantity = data.quantity
    session.flush()

    return _item_read(item, product_size, product)


def delete_cart_item(user_id: int, item_id: int) -> None:
    """Удалить свою позицию из корзины (DELETE) в своей транзакции."""
    with transaction() as session:
        _delete_cart_item(session, user_id, item_id)


def _delete_cart_item(session: Session, user_id: int, item_id: int) -> None:
    """Удалить позицию; commit делает transaction().

    Нет позиции или она чужая -> CartItemNotFound; повторный DELETE тоже 404.
    Размер не блокируется: остаток не меняется.
    """
    _lock_user(session, user_id)

    item = _get_owned_item(session, user_id, item_id)
    if item is None:
        raise CartItemNotFound()

    session.delete(item)
    session.flush()


def _is_unique_violation(exc: IntegrityError) -> bool:
    """Проверить, что это нарушение уникальности (23505)."""
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    if diag is not None and getattr(diag, "sqlstate", None) == UNIQUE_VIOLATION:
        return True
    if getattr(exc.orig, "pgcode", None) == UNIQUE_VIOLATION:
        return True
    cause = getattr(exc.orig, "__cause__", None)
    return getattr(cause, "sqlstate", None) == UNIQUE_VIOLATION


def _load_cart_item(session: Session, item_id: int) -> CartItem | None:
    """Загрузить позицию вместе с размером и товаром (selectinload)."""
    stmt = (
        select(CartItem)
        .options(
            selectinload(CartItem.product_size).selectinload(ProductSize.product)
        )
        .where(CartItem.id == item_id)
    )
    return session.execute(stmt).scalar_one_or_none()


def _cart_rows(
    session: Session, user_id: int
) -> list[tuple[CartItem, ProductSize, Product]]:
    """Вернуть позиции корзины с размером и товаром одним JOIN-запросом, по возрастанию id."""
    stmt = (
        select(CartItem, ProductSize, Product)
        .join(ProductSize, ProductSize.id == CartItem.product_size_id)
        .join(Product, Product.id == ProductSize.product_id)
        .where(CartItem.user_id == user_id)
        .order_by(CartItem.id)
    )
    return list(session.execute(stmt).all())


def get_cart(user_id: int) -> CartRead:
    """Вернуть свою корзину: позиции и ориентировочную сумму."""
    with transaction() as session:
        return _get_cart(session, user_id)


def _get_cart(session: Session, user_id: int) -> CartRead:
    """Собрать корзину; DTO строится до выхода из transaction().

    Замок владельца заставляет чтение дождаться уже идущих операций этого
    пользователя. Цены текущие, поэтому сумма — оценка.
    """
    _lock_user(session, user_id)

    items: list[CartItemRead] = []
    total = MONEY_ZERO
    for item, product_size, product in _cart_rows(session, user_id):
        read = _item_read(item, product_size, product)
        items.append(read)
        total += read.subtotal

    return CartRead(items=items, estimated_total=total)


def _item_read(item: CartItem, product_size: ProductSize, product: Product) -> CartItemRead:
    """Собрать DTO позиции из позиции, размера и товара."""
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


def to_read(item: CartItem) -> CartItemRead:
    """Собрать DTO из позиции с загруженными product_size и product."""
    return _item_read(item, item.product_size, item.product_size.product)


def _subtotal(quantity: int, price: Decimal) -> Decimal:
    """Вернуть стоимость позиции: количество * цена."""
    return price * quantity


def _lock_product_sizes(session: Session, size_ids: list[int]) -> dict[int, ProductSize]:
    """Заблокировать размеры FOR UPDATE по возрастанию id и вернуть их по id."""
    ordered = sorted(set(size_ids))
    rows = session.execute(
        select(ProductSize)
        .where(ProductSize.id.in_(ordered))
        .order_by(ProductSize.id)
        .with_for_update(of=ProductSize)
    ).scalars().all()
    return {row.id: row for row in rows}


def _lock_products_for_share(session: Session, product_ids: list[int]) -> dict[int, Product]:
    """Заблокировать товары FOR SHARE по возрастанию id и вернуть их по id.

    Цена не меняется до конца транзакции, а параллельные заказы не мешают друг другу.
    """
    ordered = sorted(set(product_ids))
    rows = session.execute(
        select(Product)
        .where(Product.id.in_(ordered))
        .order_by(Product.id)
        .with_for_update(of=Product, read=True)
    ).scalars().all()
    return {row.id: row for row in rows}


# Тестовый hook внедрения ошибок (не HTTP-параметр); по умолчанию None.
_FAULT_HOOK: Callable[[str], None] | None = None


def _fault(stage: str) -> None:
    """Вызвать тестовый hook на стадии after_order, after_first_stock или after_clear_cart."""
    hook = _FAULT_HOOK
    if hook is not None:
        hook(stage)


def create_order(user_id: int) -> OrderRead:
    """Оформить заказ из корзины одной транзакцией (POST /orders).

    Все проверки идут до записей. Пустая корзина -> CartEmptyConflict,
    нехватка остатка -> OrderStockConflict, слишком большая сумма -> OrderTotalConflict.
    DTO собирается после commit из простых значений.
    """
    with transaction() as session:
        order_id, status, total, created_at, items = _create_order(session, user_id)

    return OrderRead(
        id=order_id,
        status=status,
        total=total,
        created_at=created_at,
        items=[
            OrderItemRead(
                product_size_id=product_size_id,
                quantity=quantity,
                price_at_purchase=price,
                subtotal=price * quantity,
            )
            for product_size_id, quantity, price in items
        ],
    )


def _create_order(
    session: Session, user_id: int
) -> tuple[int, str, Decimal, datetime, list[tuple[int, int, Decimal]]]:
    """Выполнить проверки и записи checkout без commit.

    Возвращает (id заказа, статус, сумма, created_at, [(id размера, количество, цена)]).
    """
    _lock_user(session, user_id)

    raw = session.execute(
        select(CartItem.id, CartItem.product_size_id, CartItem.quantity)
        .where(CartItem.user_id == user_id)
        .order_by(CartItem.id)
    ).all()

    if not raw:
        raise CartEmptyConflict()

    sizes = _lock_product_sizes(session, [row[1] for row in raw])
    products = _lock_products_for_share(
        session, [size.product_id for size in sizes.values()]
    )

    prepared: list[tuple[int, Product, int, Decimal]] = []
    total = MONEY_ZERO
    for _item_id, product_size_id, quantity in raw:
        size = sizes.get(product_size_id)
        if size is None:  # pragma: no cover - FK не даёт такого состояния
            raise ShopError("Cart item refers to a missing product size")
        if quantity > size.stock:
            raise OrderStockConflict(
                f"Not enough stock for product size {product_size_id}: "
                f"requested {quantity}, available {size.stock}"
            )
        product = products[size.product_id]
        subtotal = _subtotal(quantity, product.price)
        total += subtotal
        prepared.append((product_size_id, product, quantity, subtotal))

    if total > MAX_ORDER_TOTAL:
        raise OrderTotalConflict(
            f"Order total {total} exceeds the maximum {MAX_ORDER_TOTAL}"
        )

    order = Order(user_id=user_id, status=ORDER_STATUS_CREATED, total=total)
    session.add(order)
    session.flush()  # нужен order.id для order_items
    _fault("after_order")

    for product_size_id, product, quantity, _subtotal_value in prepared:
        session.add(
            OrderItem(
                order_id=order.id,
                product_size_id=product_size_id,
                quantity=quantity,
                # Снимок цены на момент покупки.
                price_at_purchase=product.price,
            )
        )
    session.flush()

    # Списание по возрастанию id; stock >= qty в WHERE — страховка от отрицательного остатка.
    first_stock_update = True
    for product_size_id, _product, quantity, _subtotal_value in sorted(
        prepared, key=lambda row: row[0]
    ):
        result = session.execute(
            update(ProductSize)
            .where(ProductSize.id == product_size_id, ProductSize.stock >= quantity)
            .values(stock=ProductSize.stock - quantity)
        )
        if result.rowcount != 1:
            raise OrderStockConflict(
                f"Stock for product size {product_size_id} changed during checkout"
            )
        if first_stock_update:
            _fault("after_first_stock")
            first_stock_update = False

    session.execute(delete(CartItem).where(CartItem.user_id == user_id))
    _fault("after_clear_cart")

    if order.created_at is None:  # pragma: no cover - RETURNING заполняет сразу
        session.refresh(order)
    return (
        order.id,
        order.status,
        order.total,
        order.created_at,
        [(product_size_id, quantity, product.price)
         for product_size_id, product, quantity, _subtotal_value in prepared],
    )


def _order_read(order: Order) -> OrderRead:
    """Собрать DTO заказа (items загружены, вызывать внутри сессии).

    Цены берутся из снимка order_items; позиции сортируются по id.
    """
    return OrderRead(
        id=order.id,
        status=order.status,
        total=order.total,
        created_at=order.created_at,
        items=[
            OrderItemRead(
                product_size_id=item.product_size_id,
                quantity=item.quantity,
                price_at_purchase=item.price_at_purchase,
                subtotal=item.price_at_purchase * item.quantity,
            )
            for item in sorted(order.items, key=lambda row: row.id)
        ],
    )


def list_orders(user_id: int, limit: int, offset: int) -> list[OrderRead]:
    """Вернуть свои заказы, новые первыми, постранично (без замка владельца)."""
    with transaction() as session:
        orders = session.scalars(
            select(Order)
            .options(selectinload(Order.items))
            .where(Order.user_id == user_id)
            .order_by(Order.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return [_order_read(order) for order in orders]


def get_order(user_id: int, order_id: int) -> OrderRead:
    """Вернуть свой заказ; чужой и несуществующий дают OrderNotFound (роль не проверяется)."""
    with transaction() as session:
        order = session.scalars(
            select(Order)
            .options(selectinload(Order.items))
            .where(Order.id == order_id, Order.user_id == user_id)
        ).one_or_none()
        if order is None:
            raise OrderNotFound()
        return _order_read(order)


# Статус меняется только вперёд на один шаг: created -> processing -> completed.
ORDER_STATUS_NEXT = {"created": "processing", "processing": "completed"}


def _lock_admin_for_share(session: Session, admin_id: int) -> None:
    """Перечитать роль под users FOR SHARE; не администратор или нет строки -> ShopForbidden."""
    is_admin = session.execute(
        select(User.is_admin)
        .where(User.id == admin_id)
        .with_for_update(of=User, read=True)
    ).scalar_one_or_none()
    if is_admin is not True:
        raise ShopForbidden()


def update_order_status(admin_id: int, order_id: int, new_status: str) -> OrderRead:
    """Сменить статус заказа (администратор) в своей транзакции.

    Блокировки: users FOR SHARE (роль не снимут до commit), затем orders FOR UPDATE.
    Повтор текущего статуса — без записи; иной переход -> OrderStatusConflict.
    """
    with transaction() as session:
        _lock_admin_for_share(session, admin_id)

        order = session.scalars(
            select(Order)
            .options(selectinload(Order.items))
            .where(Order.id == order_id)
            .with_for_update(of=Order)
        ).one_or_none()
        if order is None:
            raise OrderNotFound()

        if new_status != order.status:
            if ORDER_STATUS_NEXT.get(order.status) != new_status:
                raise OrderStatusConflict(
                    f"Order status transition {order.status} -> {new_status} is not allowed"
                )
            order.status = new_status
            session.flush()
        return _order_read(order)
