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
from app.schemas.cart import CartItemAdd, CartItemRead, CartItemUpdate, CartRead

# SQLSTATE нарушения уникальности в PostgreSQL (как в services/auth.py).
UNIQUE_VIOLATION = "23505"

# Имя ограничения UNIQUE(user_id, product_size_id) из миграции M1. Нужно,
# чтобы отличить ожидаемый конфликт «эта пара уже в корзине» от любого
# другого нарушения целостности: второе маскировать нельзя.
CART_SIZE_UNIQUE = "uq_cart_size"

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


def update_cart_item(
    session: Session, user_id: int, item_id: int, data: CartItemUpdate
) -> CartItemRead:
    """Установить АБСОЛЮТНОЕ количество позиции (PATCH).

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
    5. записать quantity = data.quantity (ЗАМЕНА, не прибавка) и commit.

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

    try:
        session.commit()
    except IntegrityError as exc:  # pragma: no cover - схема уже проверила диапазон
        # Сюда можно попасть только при гонке с ограничением целостности
        # (например, CHECK quantity > 0, если схему когда-нибудь поменяют).
        session.rollback()
        raise

    # DTO собираем из объектов, которые уже держим: после commit с
    # expire_on_commit=False значения не просрочены, а связи (размер и
    # товар) прочитаны явно. Лишнего SELECT на ответ нет.
    return _item_read(item, product_size, product)


def delete_cart_item(session: Session, user_id: int, item_id: int) -> None:
    """Удалить свою позицию из корзины (DELETE → 204).

    Шаги: заблокировать владельца → найти позицию по (id, user_id) → удалить
    строку → commit.

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
    session.commit()


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


def get_cart(session: Session, user_id: int) -> CartRead:
    """Своя корзина: позиции по возрастанию id и ориентировочная сумма.

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

    DTO собирается ДО выхода из сессии (роутер закрывает её сразу после
    возврата). Это не косметика: после закрытия сессии любой ORM-объект
    отсоединён, и обращение к его незагруженным атрибутам дало бы
    DetachedInstanceError. CartItemRead/CartRead — обычные Pydantic-модели,
    в них только Decimal, int и str, поэтому они живут без сессии сколько
    угодно.
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
