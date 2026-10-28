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
# Сама аутентификация для этих маршрутов (get_current_user_id,
# get_current_admin_id) читает users в КОРОТКОЙ сессии и закрывает её до
# вызова сервиса, так что запрос не держит два соединения пула сразу (D1).
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

from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ShopConflict, ShopError, ShopForbidden, ShopNotFound
from app.database import SessionLocal
from app.models import CartItem, Order, OrderItem, Product, ProductSize, User
from app.schemas.cart import CartItemAdd, CartItemRead, CartItemUpdate, CartRead
from app.schemas.order import OrderItemRead, OrderRead

# SQLSTATE нарушения уникальности в PostgreSQL (как в services/auth.py).
UNIQUE_VIOLATION = "23505"

# Имя ограничения UNIQUE(user_id, product_size_id) из миграции M1. Нужно,
# чтобы отличить ожидаемый конфликт «эта пара уже в корзине» от любого
# другого нарушения целостности: второе маскировать нельзя.
CART_SIZE_UNIQUE = "uq_cart_size"

# Предел orders.total: колонка Numeric(12,2) — 12 значащих цифр, из них две
# после точки, значит максимум 9999999999.99. Сумму больше этого PostgreSQL
# не сохранит, поэтому отказываем заранее и понятным 409, а не 500 из драйвера.
MAX_ORDER_TOTAL = Decimal("9999999999.99")

# Начальный статус заказа. Значение обязано попадать в CHECK
# status IN ('created', 'processing', 'completed') из миграции M1.
ORDER_STATUS_CREATED = "created"

# SQLSTATE, означающие «транзакцию прервали, работу можно безопасно повторить»:
#   40P01 deadlock_detected        — PostgreSQL выбрал нас жертвой циклa;
#   40001 serialization_failure    — конфликт сериализации (на READ COMMITTED
#                                    редок, но бывает при SSI/повторах);
#   55P03 lock_not_available       — сработал наш собственный lock_timeout;
#   57014 query_canceled           — сработал statement_timeout.
RETRYABLE_SQLSTATES = frozenset({"40P01", "40001", "55P03", "57014"})

# Текст ответа для 409, когда транзакцию прервали. Именно эта строка уходит
# клиенту: он может повторить запрос сам (но сервер НЕ повторяет его скрыто).
CONCURRENT_RETRY_DETAIL = "concurrent_change_retry"

# Локальные (SET LOCAL) таймауты транзакции сервиса. Они действуют только до
# конца транзакции и не протекают в соседние запросы, взявшие то же соединение
# из пула. Значения выбраны так: lock_timeout (3 c) — ожидание замка владельца
# или строки размера; statement_timeout (5 c) — общий предел одного оператора.
OWNER_LOCK_TIMEOUT_MS = 3000
STATEMENT_TIMEOUT_MS = 5000

# Ноль в денежном масштабе: numeric(10,2) хранит два знака после точки, и
# пустая корзина должна отдавать "0.00", а не "0" (Decimal("0") ушёл бы в
# JSON как "0").
MONEY_ZERO = Decimal("0.00")

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


class CartEmptyConflict(ShopConflict):
    """Оформлять нечего: в корзине нет ни одной позиции (409).

    Не 404: корзина пользователя существует всегда (это его набор строк),
    просто пуста. И не 400: запрос корректен, состояние данных не позволяет
    выполнить операцию — это и есть 409.
    """

    detail = "Cart is empty"


class OrderStockConflict(ShopConflict):
    """Хотя бы одной позиции не хватает на складе (409).

    Проверяется ДО любых записей: если не хватает даже второй позиции,
    заказ не создаётся и остатки не меняются ни у первой, ни у второй.
    """

    detail = "Not enough stock for one of the cart items"


class OrderTotalConflict(ShopConflict):
    """Сумма заказа не влезает в Numeric(12,2) (409).

    Максимум — 9999999999.99. Отказ до записей: иначе PostgreSQL отклонил бы
    INSERT с ошибкой numeric field overflow, и клиент получил бы 500.
    """

    detail = "Order total exceeds the allowed maximum"


class OrderNotFound(ShopNotFound):
    """Заказа нет ИЛИ он принадлежит другому пользователю (404)."""

    detail = "Order not found"


class OrderStatusConflict(ShopConflict):
    """Переход статуса не разрешён учебным правилом (409)."""

    detail = "Order status transition is not allowed"


class ConcurrentChangeConflict(ShopConflict):
    """Транзакцию прервала конкуренция: deadlock, конфликт, таймаут (409).

    Отдаёт `{"detail": "concurrent_change_retry"}` и означает: ничего не
    зафиксировано, клиент может повторить операцию сам. Сервер НЕ повторяет
    её автоматически: при обрыве соединения исход неизвестен, и повтор мог бы
    создать второй заказ.
    """

    detail = CONCURRENT_RETRY_DETAIL


def new_session() -> Session:
    """Сессия «как есть»: для тестов и для инструментов, не для сервиса.

    Бизнес-операции M4–M5 сессию сами не создают через эту функцию — они
    входят в `transaction()`, который задаёт изоляцию, таймауты и владеет
    commit/rollback. Здесь `SessionLocal()` оставлен для проб (нужен Engine,
    внутрь которого они вставляют listener) и для ручных экспериментов.
    """
    return SessionLocal()


def _sqlstate(exc: BaseException) -> str | None:
    """SQLSTATE из исключения драйвера (psycopg кладёт его в разные места).

    Приоритет: `orig.sqlstate`, затем `orig.diag.sqlstate`, затем
    `orig.pgcode`/`__cause__.sqlstate`. Возвращает None, если код не найден —
    тогда ошибка не будет замаскирована под «повторите».
    """
    orig = getattr(exc, "orig", None)
    candidates = (orig, getattr(orig, "diag", None), getattr(orig, "__cause__", None))
    for candidate in candidates:
        if candidate is None:
            continue
        for attribute in ("sqlstate", "pgcode"):
            value = getattr(candidate, attribute, None)
            if isinstance(value, str) and len(value) == 5:
                return value
    return None


def _rollback_quietly(session: Session) -> None:
    """Откатить, не скрывая исходную ошибку.

    rollback сам может упасть (например, соединение уже разорвано), но это
    не повод потерять настоящее исключение: его пробросит вызывающий код.
    """
    with suppress(Exception):
        session.rollback()


@contextmanager
def transaction() -> Iterator[Session]:
    """Новая SessionLocal().begin() на одну бизнес-операцию корзины или checkout.

    Зачем нужен отдельный менеджер, а не `with session.begin()` в роутере:

    1. **Autobegin конфликт.** Сессия, в которой аутентификация прочитала
       `User`, уже в открытой транзакции (SQLAlchemy 2.0 начинает её неявно —
       autobegin). Вызвать в ней `session.begin()` нельзя: будет
       `InvalidRequestError`. Поэтому сервис открывает СВОЮ сессию
       (`SessionLocal()`) и работает в ней; из auth-слоя приходит только
       проверенный `user.id`, а короткая auth-сессия к этому моменту уже
       закрыта (`get_current_user_id`, дефект D1).
    2. **Изоляция задаётся ДО чтений.** `SET TRANSACTION ISOLATION LEVEL
       READ COMMITTED` — первый оператор после явного `begin()` (begin() не
       отправляет SQL), поэтому ни одно чтение не успевает пройти раньше.
    3. **Локальные таймауты.** `SET LOCAL lock_timeout`/`statement_timeout`
       действуют до конца транзакции и не остаются на соединении в пуле.
    4. **Один владелец commit/rollback.** Commit — на нормальном выходе,
       rollback — при любом исключении; `close()` — всегда, в finally. Auth
       сессия при этом не коммитится и не «спасает» операцию.
    5. **Повторяемые ошибки — отдельный 409.** SQLSTATE 40P01/40001/55P03/57014
       (deadlock, serialization, сработавший lock_timeout, statement_timeout)
       сначала откатываются, затем превращаются в `ConcurrentChangeConflict`
       (409 `concurrent_change_retry`). Все прочие ошибки (включая
       `IntegrityError` и ошибки в коде) пробрасываются как есть и не
       маскируются под «повторите».
    """
    session = SessionLocal()
    try:
        session.begin()  # явный begin, транзакция ещё не начата (сессия новая)
        session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
        session.execute(text(f"SET LOCAL lock_timeout = '{OWNER_LOCK_TIMEOUT_MS}ms'"))
        session.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_MS}ms'"))
        yield session
    except DBAPIError as exc:
        # Сюда попадают и ошибки от flush (например, UNIQUE): откатываем, но
        # маскируем только те, что в RETRYABLE_SQLSTATES.
        _rollback_quietly(session)
        if _sqlstate(exc) in RETRYABLE_SQLSTATES:
            raise ConcurrentChangeConflict() from exc
        raise
    except BaseException:
        # ShopError и любые программные ошибки: откат и проброс без маскировки.
        _rollback_quietly(session)
        raise
    else:
        try:
            session.commit()
        except DBAPIError as exc:
            _rollback_quietly(session)
            if _sqlstate(exc) in RETRYABLE_SQLSTATES:
                raise ConcurrentChangeConflict() from exc
            raise
    finally:
        # Сессия закрывается всегда: соединение возвращается в пул, а если
        # транзакция осталась открытой (например, после ошибки) — откатывается.
        session.close()


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


def add_cart_item(user_id: int, data: CartItemAdd) -> CartItemRead:
    """Добавить размер в корзину (POST /cart/items), своя транзакция.

    Принимает только проверенный `user.id` и данные запроса: сессию, изоляцию
    и commit/rollback даёт `transaction()`.
    """
    with transaction() as session:
        return _add_cart_item(session, user_id, data)


def _add_cart_item(session: Session, user_id: int, data: CartItemAdd) -> CartItemRead:
    """Тело добавления: блокировки, проверки, запись. Commit — вне функции.

    Порядок шагов (он же протокол блокировок):

    1. заблокировать владельца (users FOR UPDATE) — только по user.id;
    2. заблокировать размер (product_sizes FOR UPDATE) и убедиться, что он
       существует: нет размера → ProductSizeNotFound (404);
    3. найти пару (user, size): если строки нет — создать с quantity=delta,
       если есть — прибавить delta к текущему количеству;
    4. проверить ИТОГОВОЕ количество: оно должно помещаться в int4 и не
       превышать остаток размера. Иначе CartStockConflict (409);
    5. `flush` — отправить INSERT/UPDATE в текущую транзакцию и получить id;
       фиксацию сделает `transaction()` на выходе (ровно один commit).

    Остаток склада НЕ уменьшается: корзина ничего не резервирует. Списание
    остатка — задача checkout, где остаток проверяется повторно под блокировкой.

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
        # flush, а не commit: фиксация — в transaction(). Здесь же ловится
        # UNIQUE(user_id, product_size_id) — гонка двух вставок одной пары.
        session.flush()
    except IntegrityError as exc:
        # Остальные нарушения целостности не маскируем: пусть всплывают,
        # это ошибка в коде или схеме, а не «конфликт корзины».
        if _integrity_error_kind(exc) == CART_SIZE_UNIQUE or _is_unique_violation(exc):
            raise CartConflict() from exc
        raise

    # Строка уже в БД (в этой транзакции); читаем её с размером и товаром
    # ОДНИМ запросом на связь (selectinload), чтобы ответ без ленивых запросов.
    saved = _load_cart_item(session, item.id)
    if saved is None:  # pragma: no cover - сразу после flush строки быть не может
        raise CartItemNotFound()
    return to_read(saved)


# --- изменение и удаление позиции (M4, шаг 3) ----------------------------
#
# Ключевое отличие от add_cart_item: позиция адресуется своим id (id строки
# cart_items), а не парой (user, size). Из этого сразу следует вопрос
# доступа: чужой id не должен быть ни виден, ни изменяем. Ответ на него —
# одна выборка `_get_owned_item`, где в WHERE одновременно стоят и id, и
# user_id. Ниже — почему именно так, а не «прочитать, потом сравнить».


def _get_owned_item(session: Session, user_id: int, item_id: int) -> CartItem | None:
    """Позиция, которая одновременно имеет id=item_id И принадлежит user_id.

    ОДИН SELECT с двумя условиями в WHERE:

        SELECT ... FROM cart_items
        WHERE cart_items.id = :item_id AND cart_items.user_id = :user_id

    Почему это важнее, чем «найти по id, потом сравнить user_id в Python»:

    * Нет окна между проверкой и использованием. При двух шагах строка
      читается одной транзакцией, а проверяется в Python; при одном
      запросе условие владельца — часть того же SELECT, и «между» ничего
      не вставить.
    * Чужой id неотличим от несуществующего: оба дают 404 и одинаковое
      тело ответа, поэтому по ответу нельзя выяснить, существует ли у
      кого-то ещё позиция с таким id (это утечка, oracle по коду ответа).
    * Нельзя забыть фильтр: он в одном месте, а не в каждом вызове.
    * Дешевле: один запрос вместо двух на каждый вызов.

    Возвращаем None (а не исключение): «не найдено» и «чужое» — для
    вызывающего одно и то же, и он сам решает, что это 404.

    Блокировку (FOR UPDATE) здесь НЕ берём: строка cart_items защищена
    замком владельца (users FOR UPDATE, шаг 1), который уже взят
    вызывающей функцией, а сама позиция меняется только в этой транзакции.
    """
    return session.execute(
        select(CartItem).where(
            CartItem.id == item_id,
            CartItem.user_id == user_id,
        )
    ).scalar_one_or_none()


def update_cart_item(user_id: int, item_id: int, data: CartItemUpdate) -> CartItemRead:
    """Установить АБСОЛЮТНОЕ количество позиции (PATCH), своя транзакция."""
    with transaction() as session:
        return _update_cart_item(session, user_id, item_id, data)


def _update_cart_item(
    session: Session, user_id: int, item_id: int, data: CartItemUpdate
) -> CartItemRead:
    """Тело PATCH: блокировки, проверка остатка, запись. Commit — вне функции.

    Порядок шагов (тот же протокол, что у остальных операций корзины):

    1. заблокировать владельца (users FOR UPDATE);
    2. найти позицию одной выборкой по (id, user_id): нет или чужая →
       CartItemNotFound (404). Замок владельца гарантирует, что эту строку
       не удалит и не изменит параллельная операция того же пользователя,
       пока мы работаем;
    3. заблокировать размер позиции (product_sizes FOR UPDATE) — он нужен
       для проверки остатка, и остаток не должен измениться до COMMIT;
    4. проверить `quantity <= stock`: иначе CartStockConflict (409).
       Ноль и отрицательные сюда не доходят — их отсекает схема (422), а
       нижняя граница ещё и CHECK quantity > 0 в БД;
    5. записать quantity = data.quantity (ЗАМЕНА, не прибавка) и `flush`;
       фиксацию сделает `transaction()` на выходе.

    Ответ 200 с той же формой, что у POST /cart/items: клиенту после
    PATCH обычно нужен пересчитанный subtotal.

    Почему запись количества не может «переполнить» int4: схема ограничивает
    quantity сверху 2147483647, а сама запись — присваивание, не сложение.
    Поэтому проверки на переполнение здесь нет (она нужна только в POST,
    где количество суммируется).
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

    # DTO собираем из объектов, которые уже держим: связи (размер и товар)
    # прочитаны явно, лишнего SELECT на ответ нет.
    return _item_read(item, product_size, product)


def delete_cart_item(user_id: int, item_id: int) -> None:
    """Удалить свою позицию из корзины (DELETE → 204), своя транзакция."""
    with transaction() as session:
        _delete_cart_item(session, user_id, item_id)


def _delete_cart_item(session: Session, user_id: int, item_id: int) -> None:
    """Тело DELETE: блокировка владельца, поиск своей строки, удаление.

    Шаги: заблокировать владельца → найти позицию по (id, user_id) → удалить
    строку и `flush`; commit сделает `transaction()`.

    Размер (product_sizes) здесь НЕ блокируется: остаток не меняется ни на
    единицу, а значит и защищать нечего. Это же отличие от checkout, где
    списание остатка требует замка на строку размера.

    Чужая позиция — тот же CartItemNotFound (404), что и отсутствующая.
    Повторный DELETE того же id тоже даёт 404 (строки уже нет), а не 204:
    идемпотентности у нас нет намеренно, иначе было бы невозможно отличить
    «удалил» от «и не было».
    """
    _lock_user(session, user_id)

    item = _get_owned_item(session, user_id, item_id)
    if item is None:
        raise CartItemNotFound()

    session.delete(item)
    session.flush()


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


# --- чтение корзины (M4, шаг 2: GET /cart) -------------------------------
#
# Отличие от add_cart_item: здесь ничего не пишется, поэтому и commit не
# нужен — сессия закрывается с ROLLBACK (в логе SQLAlchemy так и видно:
# BEGIN (implicit) ... ROLLBACK). Блокировка владельца берётся сознательно:
# протокол корзины единый для всех операций (users FOR UPDATE первым шагом),
# и чтение не исключение — см. пояснение в get_cart.


def _cart_rows(
    session: Session, user_id: int
) -> list[tuple[CartItem, ProductSize, Product]]:
    """Все позиции корзины пользователя с размером и товаром ОДНИМ запросом.

    Здесь именно явный JOIN, а не selectinload (как в add_cart_item):
    чтение всей корзины — это всегда «каждая позиция нужна вместе с размером
    и товаром», поэтому JOIN даёт ровно один оператор вместо трёх.

    Три вещи, которые нельзя убрать из этого запроса:

    * `WHERE cart_items.user_id == user_id` — фильтр владельца. Он
      единственный источник «у кого какая корзина», и user_id приходит из
      проверенного токена, а не из параметров запроса;
    * `ORDER BY cart_items.id` — стабильный порядок: без ORDER BY
      PostgreSQL вправе вернуть строки в любом порядке, и ответ «прыгал» бы
      между одинаковыми запросами;
    * INNER JOIN по внешним ключам — позиция без размера или размера без
      товара невозможна (FK в схеме), поэтому LEFT JOIN здесь не нужен и
      только скрыл бы битые данные.

    Возвращаем список кортежей (CartItem, ProductSize, Product) — ровно три
    сущности из SELECT, в том же порядке, как они объявлены в select().
    """
    stmt = (
        select(CartItem, ProductSize, Product)
        .join(ProductSize, ProductSize.id == CartItem.product_size_id)
        .join(Product, Product.id == ProductSize.product_id)
        .where(CartItem.user_id == user_id)
        .order_by(CartItem.id)
    )
    return list(session.execute(stmt).all())


def get_cart(user_id: int) -> CartRead:
    """Своя корзина: позиции по возрастанию id и ориентировочная сумма.

    Принимает только `user.id`; сессию, изоляцию и commit/rollback даёт
    `transaction()`. Чтение тоже идёт через менеджер: так действуют единые
    READ COMMITTED и локальные таймауты, а `FOR UPDATE` на владельце
    снимается ровно на COMMIT.
    """
    with transaction() as session:
        return _get_cart(session, user_id)


def _get_cart(session: Session, user_id: int) -> CartRead:
    """Тело чтения корзины: замок владельца, один JOIN, DTO в памяти.

    Порядок шагов:

    1. `_lock_user` — заблокировать строку владельца (общий протокол
       корзины и checkout: users FOR UPDATE идёт первым, потом
       product_sizes, потом products);
    2. один SELECT с JOIN размеров и товаров, фильтр по user_id, ORDER BY id;
    3. собрать DTO (CartItemRead для каждой строки) и посчитать
       estimated_total прямо в памяти.

    Зачем блокировка на чтении. Формально ответ «позиции + сумма» уже
    согласован без неё: один SELECT в READ COMMITTED берёт единый снимок,
    поэтому позиции и цены не могут быть из разных состояний. Блокировка
    даёт другое — ожидание уже начатых операций этого пользователя: если
    параллельно идёт "добавление в корзину", GET дождётся его COMMIT и
    вернёт состояние ПОСЛЕ него, а не «до». Цена этого решения — чтение
    корзины одного пользователя сериализуется с его же записями (и с
    FK-проверками, которым нужна строка users). Если такая свежесть не
    нужна, достаточно убрать один вызов `_lock_user` — остальной код не
    меняется.

    Цена берётся ТЕКУЩАЯ из products, а не из заказа, поэтому
    estimated_total — оценка: см. пояснение в docstring CartRead.

    DTO собирается до выхода из `transaction()`: CartItemRead/CartRead —
    обычные Pydantic-модели (Decimal, int, str), им не нужна живая сессия.
    """
    _lock_user(session, user_id)

    items: list[CartItemRead] = []
    total = MONEY_ZERO
    for item, product_size, product in _cart_rows(session, user_id):
        read = _item_read(item, product_size, product)
        items.append(read)
        # Оба слагаемых — Decimal с двумя знаками после точки из
        # numeric(10,2), поэтому сумма точная и float не участвует.
        total += read.subtotal

    return CartRead(items=items, estimated_total=total)


def _item_read(item: CartItem, product_size: ProductSize, product: Product) -> CartItemRead:
    """Собрать плоский DTO позиции из трёх объектов.

    Одна функция на два способа получить связки: JOIN (чтение корзины) и
    selectinload (ответ на добавление). Иначе поля пришлось бы перечислять
    дважды, и они разъехались бы при первом же изменении контракта.
    """
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
    """DTO позиции из ORM-объекта, у которого загружены product_size.product."""
    return _item_read(item, item.product_size, item.product_size.product)


def _subtotal(quantity: int, price: Decimal) -> Decimal:
    """Стоимость позиции: количество * цена за пару.

    Decimal на Decimal даёт точный результат (никаких float-погрешностей).
    Умножение на int допустимо: int приводится к Decimal без потери
    точности, а результат остаётся Decimal.
    """
    return price * quantity


# --- checkout, шаг 1: подготовка заказа (M5) ------------------------------
#
# Здесь ТОЛЬКО порядок и проверки. Записей нет ни одной: ни orders, ни
# order_items, ни списания остатка, ни очистки корзины. Поэтому и функция
# называется prepare_order, а не create_order, и возвращает предпросмотр.
# Место, где появятся записи, помечено ниже отдельным блоком.
#
# ПРОТОКОЛ БЛОКИРОВОК (общий с корзиной, здесь плюс products):
#   1. users FOR UPDATE                  — сериализует операции владельца;
#   2. product_sizes FOR UPDATE          — по возрастанию id;
#   3. products FOR SHARE                — по возрастанию id.
# Почему именно в этом порядке: он совпадает с корзиной, поэтому две сессии
# не могут взяться за одни и те же строки наоборот (иначе deadlock). Все
# размеры и товары блокируются ДО первой проверки количества, чтобы
# значения stock/price не изменились между проверкой и будущей записью.


def _lock_product_sizes(session: Session, size_ids: list[int]) -> dict[int, ProductSize]:
    """Заблокировать размеры на запись по ВОЗРАСТАНИЮ id.

    `ORDER BY id` + `FOR UPDATE` задаёт порядок обработки строк, поэтому два
    заказа с пересекающимся набором размеров блокируются в одном и том же
    порядке и не образуют цикл ожидания.

    Возвращаем сами строки: stock нужен для проверки, и он гарантированно
    актуален (прочитан под блокировкой, а не до неё).
    """
    ordered = sorted(set(size_ids))
    rows = session.execute(
        select(ProductSize)
        .where(ProductSize.id.in_(ordered))
        .order_by(ProductSize.id)
        .with_for_update(of=ProductSize)
    ).scalars().all()
    return {row.id: row for row in rows}


def _lock_products_for_share(session: Session, product_ids: list[int]) -> dict[int, Product]:
    """Заблокировать товары в режиме FOR SHARE по возрастанию id.

    FOR SHARE (а не FOR UPDATE) выбран осознанно: цену мы только ЧИТАЕМ, но
    не хотим, чтобы её поменяли между проверкой и созданием заказа. FOR
    SHARE не мешает другим читателям и другим FOR SHARE, но конфликтует с
    UPDATE/DELETE строки products — то есть администратор не сможет изменить
    цену, пока эта транзакция жива. Заодно это слабее FOR UPDATE: два
    одновременных заказа не блокируют друг друга на товарах.
    """
    ordered = sorted(set(product_ids))
    rows = session.execute(
        select(Product)
        .where(Product.id.in_(ordered))
        .order_by(Product.id)
        .with_for_update(of=Product, read=True)
    ).scalars().all()
    return {row.id: row for row in rows}


# Стадии для внедрения ошибок в тестах. Это НЕ HTTP-параметр: клиент не может
# включить hook запросом, в OpenAPI его нет, а по умолчанию он None (никаких
# ошибок). Тест подменяет `shop._FAULT_HOOK` напрямую и проверяет, что откат
# возвращает БД в исходное состояние. Оставлен в модуле намеренно: без такой
# «точки инъекции» атомарность отката пришлось бы проверять только чтением кода.
_FAULT_HOOK: Callable[[str], None] | None = None


def _fault(stage: str) -> None:
    """Вызвать тестовый hook внедрения ошибки на указанной стадии.

    Стадии: `after_order` (заказ вставлен, позиции ещё нет), `after_first_stock`
    (остаток первого размера уже уменьшен), `after_clear_cart` (корзина уже
    очищена, фиксации ещё нет). В обычной работе ничего не делает.
    """
    hook = _FAULT_HOOK
    if hook is not None:
        hook(stage)


def create_order(user_id: int) -> OrderRead:
    """Оформить заказ: проверки и записи в ОДНОЙ транзакции, один commit.

    Порядок (он же протокол блокировок):

    1. `_lock_user` — users FOR UPDATE по user.id из токена;
    2. прочитать состав корзины (id строки, id размера, количество) — это
       ещё не проверка, а только список того, что нужно заблокировать.
       Читать до блокировок безопасно: замок владельца не даст никому
       изменить корзину этого пользователя, пока мы работаем;
    3. пустая корзина → CartEmptyConflict (409);
    4. заблокировать ВСЕ размеры корзины (FOR UPDATE, по возрастанию id);
    5. заблокировать ВСЕ их товары (FOR SHARE, по возрастанию id);
    6. проверить ВСЕ количества против актуального stock — ДО любых записей:
       если не хватает хотя бы одной (пусть даже второй по счёту) позиции,
       отказ 409 и в БД не меняется ничего;
    7. посчитать сумму как Decimal по ценам ИЗ БД (не по сумме от клиента и
       не на float) и проверить предел Numeric(12,2);
    8. ЗАПИСИ, все внутри одной транзакции:
       `Order(status='created', total)` → flush (получить id) →
       `OrderItem` на каждую позицию (`price_at_purchase` — снимок цены) →
       `UPDATE product_sizes SET stock = stock - :qty WHERE id = :id AND
       stock >= :qty` для каждого размера (по возрастанию id) →
       `DELETE FROM cart_items WHERE user_id = :uid` (только своя корзина) →
       единственный `commit` на выходе из `transaction()`;
    9. DTO собирается ПОСЛЕ успешного commit и уже без сессии — из простых
       значений (id, status, total, created_at, позиции), а не из ORM-объектов.

    **Почему `WHERE stock >= :qty` в UPDATE.** Проверка в шаге 6 сделана под
    блокировкой, поэтому обычно всё сходится. Условие в самом UPDATE — это
    последняя страховка: если остаток всё-таки изменился, `rowcount` будет 0,
    и мы получим 409 с полным откатом, а не отрицательный остаток. Значение
    `stock >= 0` в БД (CHECK) при этом никогда не нарушается.

    **Чего не гарантируют блокировки.** Они живут до конца этой транзакции.
    После commit другой заказ может списать остаток, а администратор —
    изменить цену; на созданный заказ это не влияет, потому что цена в нём —
    снимок. Замок не «бронирует» товар навсегда.

    **Повторный вызов.** После успешного заказа корзина очищена, поэтому
    следующий `POST /orders` вернёт 409 `Cart is empty` — без автоматических
    повторов (outcome неизвестен только при обрыве соединения, и повторять
    такой checkout автоматически нельзя).
    """
    with transaction() as session:
        order_id, status, total, created_at, items = _create_order(session, user_id)

    # DTO собирается ПОСЛЕ успешного commit и после закрытия сессии: здесь
    # только простые значения, ни одного ORM-объекта, поэтому ленивых
    # запросов и DetachedInstanceError быть не может.
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
    """Тело checkout без менеджера транзакции: проверки, затем записи.

    Возвращает простые значения: id заказа, статус, сумму, created_at и
    список (id размера, количество, снимок цены). Из них после commit
    собирается DTO — уже без сессии.
    """
    _lock_user(session, user_id)

    # Шаг 2: состав корзины (без JOIN — только то, что нужно для блокировок).
    raw = session.execute(
        select(CartItem.id, CartItem.product_size_id, CartItem.quantity)
        .where(CartItem.user_id == user_id)
        .order_by(CartItem.id)
    ).all()

    # Шаг 3: пустая корзина — конфликт состояния, а не 404.
    if not raw:
        raise CartEmptyConflict()

    # Шаги 4–5: блокировки в детерминированном порядке.
    sizes = _lock_product_sizes(session, [row[1] for row in raw])
    products = _lock_products_for_share(
        session, [size.product_id for size in sizes.values()]
    )

    # Шаг 6: ВСЕ проверки количеств — до первой записи.
    # В prepared попадает всё нужное для записей и для DTO: id размера,
    # снимок цены (product.price), количество и стоимость позиции.
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

    # Шаг 7: сумма Decimal из БД и предел Numeric(12,2).
    if total > MAX_ORDER_TOTAL:
        raise OrderTotalConflict(
            f"Order total {total} exceeds the maximum {MAX_ORDER_TOTAL}"
        )

    # Шаг 8: записи. Статус и сумма — явно: клиент их не присылает.
    order = Order(user_id=user_id, status=ORDER_STATUS_CREATED, total=total)
    session.add(order)
    # flush, а не commit: нужен order.id для FK в order_items.
    session.flush()
    _fault("after_order")

    for product_size_id, product, quantity, _subtotal_value in prepared:
        session.add(
            OrderItem(
                order_id=order.id,
                product_size_id=product_size_id,
                quantity=quantity,
                # Снимок цены: именно это значение останется в истории.
                price_at_purchase=product.price,
            )
        )
    # Отправляем INSERT-ы позиций до списания остатка: если ограничение не
    # пройдёт, откат вернёт и заказ, и остатки.
    session.flush()

    # Остаток уменьшаем по ВОЗРАСТАНИЮ id (тот же порядок, что у блокировок),
    # отдельным UPDATE на каждый размер. Коммита внутри цикла нет.
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
            # Остаток изменился между проверкой и записью (гонка). Полный
            # откат: ни заказа, ни позиций, ни остатков.
            raise OrderStockConflict(
                f"Stock for product size {product_size_id} changed during checkout"
            )
        if first_stock_update:
            _fault("after_first_stock")
            first_stock_update = False

    # Очищаем ТОЛЬКО свою корзину: user_id из токена, чужие строки не тронуты.
    session.execute(delete(CartItem).where(CartItem.user_id == user_id))
    _fault("after_clear_cart")

    if order.created_at is None:  # pragma: no cover - RETURNING заполняет сразу
        session.refresh(order)
    # Наружу — простые значения: DTO соберётся уже после commit и без сессии.
    return (
        order.id,
        order.status,
        order.total,
        order.created_at,
        [(product_size_id, quantity, product.price)
         for product_size_id, product, quantity, _subtotal_value in prepared],
    )


# --- история заказов (M5): GET /orders и GET /orders/{order_id} -----------
#
# Только чтение, поэтому замка владельца (users FOR UPDATE) здесь НЕТ:
# история не меняет ни корзину, ни остаток, и ей незачем ждать параллельный
# checkout того же пользователя. Сессия всё равно своя, через transaction():
# те же READ COMMITTED и локальные таймауты, а DTO собирается ВНУТРИ блока —
# пока сессия активна, поэтому ленивых запросов к закрытой сессии нет.
#
# selectinload(Order.items) даёт два оператора: SELECT orders ... и SELECT
# order_items WHERE order_id IN (...). В READ COMMITTED у них разные снимки,
# но это безопасно: позиции заказа после commit checkout не меняются, а
# новый заказ, появившийся между операторами, в список уже не попадёт.


def _order_read(order: Order) -> OrderRead:
    """DTO заказа из ORM-объекта с загруженными items (вызывать в сессии).

    Цена — `price_at_purchase` из order_items (снимок), а не текущая
    `products.price`; `total` — снимок из orders. Позиции — по возрастанию
    id: у relationship нет order_by, и selectinload порядок не обещает.
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
    """Свои заказы, новые первыми (ORDER BY id DESC), страница limit/offset.

    `WHERE orders.user_id = :user_id` — единственный источник «чьи заказы»;
    user_id приходит из проверенного токена. Пустая история — пустой список.
    """
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
    """Один свой заказ: id И user_id в одном WHERE (как _get_owned_item).

    Чужой и несуществующий заказ неотличимы — оба OrderNotFound (404).
    Администратор здесь не исключение: это личный маршрут, роль не
    проверяется вовсе, чужие заказы он через него не видит.
    """
    with transaction() as session:
        order = session.scalars(
            select(Order)
            .options(selectinload(Order.items))
            .where(Order.id == order_id, Order.user_id == user_id)
        ).one_or_none()
        if order is None:
            raise OrderNotFound()
        return _order_read(order)


# --- смена статуса заказа (M5): PATCH /orders/{order_id}/status ---------------
#
# УЧЕБНОЕ правило (уточнение контракта, а не требование любого магазина):
# только вперёд и только на один шаг — created -> processing -> completed.
# Повтор текущего статуса — 200 без записи (запрос можно безопасно
# повторить); пропуск шага и обратный ход — 409.
#
# Порядок блокировок:
#   1. users FOR SHARE (строка администратора) — роль перечитывается В ЭТОЙ
#      транзакции. get_current_admin_id проверил её раньше, в auth-сессии; между
#      той проверкой и нашей записью роль могли снять. FOR SHARE не мешает
#      другим читателям, но не даёт UPDATE users (снятию роли) пройти до
#      нашего commit — решение «можно» остаётся верным до фиксации;
#   2. orders FOR UPDATE — два одновременных PATCH одного заказа встают в
#      очередь, и второй проверяет переход уже от зафиксированного статуса
#      первого (READ COMMITTED: новый оператор — новый снимок).
#
# Остаток, order_items и корзина здесь не читаются и не меняются: заказ уже
# оформлен, списание сделал checkout. Строка products/product_sizes не
# блокируется, поэтому смена статуса не ждёт покупателей.

ORDER_STATUS_NEXT = {"created": "processing", "processing": "completed"}


def _lock_admin_for_share(session: Session, admin_id: int) -> None:
    """Шаг 1: перечитать роль под FOR SHARE; не администратор → 403.

    Строки нет (аккаунт удалили после проверки токена) — тоже 403: прав на
    операцию у этого запроса нет. Сравнение строго `is True`, как в
    require_admin: NULL в колонке доступа не даёт.
    """
    is_admin = session.execute(
        select(User.is_admin)
        .where(User.id == admin_id)
        .with_for_update(of=User, read=True)
    ).scalar_one_or_none()
    if is_admin is not True:
        raise ShopForbidden()


def update_order_status(admin_id: int, order_id: int, new_status: str) -> OrderRead:
    """Сменить статус любого заказа (администратор), своя транзакция.

    Возвращает заказ целиком (как GET /orders/{id}); DTO собирается в
    активной сессии, до commit на выходе из `transaction()`.
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
        # Повтор текущего статуса: ни UPDATE, ни flush — ответ 200 «как есть».
        return _order_read(order)
