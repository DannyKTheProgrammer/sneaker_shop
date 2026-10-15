# Чтение и запись каталога в БД (M2 + запись M3).
#
# Здесь сознательно нет ни FastAPI, ни HTTP-ошибок: сервис отвечает на
# вопрос «что лежит в БД», а решение «404/422/409» принимает роутер.
# Так сервис можно переиспользовать и тестировать без HTTP.

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Select, and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models import Brand, Product, ProductSize
from app.schemas.catalog import ProductWrite


class CatalogError(Exception):
    """Ошибка каталога, которую вызывающий код переводит в HTTP-ответ."""


class BrandNotFound(CatalogError):
    """Бренда с таким id нет: ссылаться не на что."""


class ProductNotFound(CatalogError):
    """Товара с таким id нет (или он исчез между проверкой и записью)."""


class CatalogConflict(CatalogError):
    """Конфликт целостности: гонка или ограничение, которое не прошло."""


@dataclass(slots=True)
class ProductFilters:
    """Разобранные фильтры списка товаров.

    Все условия необязательные и сочетаются через AND. Хранить их в одном
    объекте удобнее, чем тащить семь аргументов: сигнатура не растёт, а
    добавление фильтра не ломает вызовы.
    """

    brand_id: int | None = None
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    size: Decimal | None = None
    search: str | None = None
    limit: int = 20
    offset: int = 0


def _product_filters(filters: ProductFilters) -> list:
    """Список условий для WHERE (каждое — отдельный AND)."""
    conditions = []

    if filters.brand_id is not None:
        conditions.append(Product.brand_id == filters.brand_id)

    # Границы включительные: >= min и <= max.
    if filters.min_price is not None:
        conditions.append(Product.price >= filters.min_price)
    if filters.max_price is not None:
        conditions.append(Product.price <= filters.max_price)

    if filters.size is not None:
        # EXISTS, а не JOIN. Соотношение «товар к размерам» — 1:N, поэтому
        # JOIN с product_sizes размножил бы строки товара (по строке на
        # каждый размер), и LIMIT отрезал бы не товары, а эти дубликаты.
        # EXISTS проверяет факт наличия подходящей строки и строку товара
        # не дублирует.
        #
        # Правило наличия в этом шаге: существует размер с таким size И
        # stock > 0. Поэтому размер 43 с остатком 0 фильтр не проходит.
        conditions.append(
            Product.sizes.any(
                and_(
                    ProductSize.size == filters.size,
                    ProductSize.stock > 0,
                )
            )
        )

    if filters.search:
        # icontains — регистронезависимый поиск по подстроке (ILIKE '%...%').
        # autoescape=True делает символы % и _ буквальными: без него поиск
        # "100%" превратился бы в шаблон и нашёл бы лишнее. Пробелы по краям
        # убирает схема запроса, пустой search сюда не доходит как условие.
        conditions.append(Product.name.icontains(filters.search, autoescape=True))

    return conditions


def list_brands(session: Session) -> list[Brand]:
    """Все бренды, отсортированные по id.

    Сортировка задана явно: порядок строк в PostgreSQL без ORDER BY
    не определён, и ответ мог бы «прыгать» между запросами.
    """
    return list(session.scalars(select(Brand).order_by(Brand.id)))


def list_products(session: Session, filters: ProductFilters | None = None) -> list[Product]:
    """Товары с размерами: фильтры через AND, сортировка, затем LIMIT/OFFSET.

    selectinload(Product.sizes) — ключевая деталь. Без него обращение
    к product.sizes при сериализации сделало бы ОТДЕЛЬНЫЙ запрос на каждый
    товар (проблема N+1: 1 запрос за товарами + N за размерами). С
    selectinload размеры всех найденных товаров подгружаются одним
    дополнительным запросом с IN (...) — итого два запроса независимо от
    количества товаров на странице.

    Про пагинацию честно: offset/limit по номеру страницы не даёт
    неизменных страниц при параллельных вставках и удалениях. ORDER BY
    Product.id только фиксирует порядок, но если между запросами страниц
    товар удалили или добавили, состав страниц сместится (товар может
    «перескочить» на соседнюю страницу или пропасть из выдачи). Устойчивую
    постраничность даёт keyset-пагинация (WHERE id > last_seen_id), но это
    отдельная задача.
    """
    active = filters or ProductFilters()
    stmt: Select = select(Product).options(selectinload(Product.sizes))

    conditions = _product_filters(active)
    if conditions:
        stmt = stmt.where(and_(*conditions))

    # ORDER BY до LIMIT/OFFSET — иначе LIMIT отрезал бы произвольные строки.
    stmt = stmt.order_by(Product.id).limit(active.limit).offset(active.offset)
    return list(session.scalars(stmt))


def get_product(session: Session, product_id: int) -> Product | None:
    """Один товар с размерами или None, если такого id нет.

    None, а не исключение: сервис не знает, что для HTTP это 404.
    """
    stmt = (
        select(Product)
        .options(selectinload(Product.sizes))
        .where(Product.id == product_id)
    )
    return session.scalars(stmt).first()


# --- запись каталога (M3) -------------------------------------------------
#
# Владелец транзакции здесь один — этот сервис: он делает и commit, и
# rollback. Роутер (когда появится в M3 вместе с require_admin) только
# переводит исключения в HTTP-коды и не коммитит: два владельца коммита в
# одном запросе — верный способ закоммитить половину работы.
#
# Почему НЕ `with session.begin()`: сессия приходит из зависимости
# get_session, и к моменту вызова она уже может быть в транзакции —
# SQLAlchemy 2.0 начинает её автоматически (autobegin) при первом
# обращении к БД. Повторный session.begin() в этом состоянии даёт
# InvalidRequestError. Поэтому обычный порядок: проверили → изменили →
# flush/commit, а при ошибке — rollback.


def _brand_exists(session: Session, brand_id: int) -> bool:
    """Есть ли бренд с таким id (ссылаться можно только на существующий)."""
    return session.get(Brand, brand_id) is not None


def _commit(session: Session, conflict_message: str) -> None:
    """Зафиксировать транзакцию, а IntegrityError превратить в конфликт.

    Ловим именно IntegrityError: это ожидаемая категория (гонка с
    параллельным изменением, ограничение целостности). Любые другие
    исключения не маскируются — они означают ошибку в коде, и пусть
    всплывают как 500, а не превращаются в «конфликт».
    """
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise CatalogConflict(conflict_message) from exc


def create_product(session: Session, data: ProductWrite) -> Product:
    """Создать карточку товара по валидированным данным.

    Порядок: проверить бренд → вставить → commit → вернуть карточку с
    размерами. Бренд проверяется ДО вставки, чтобы получить понятное
    «нет бренда» (иначе получили бы ошибку внешнего ключа).

    Размеры здесь не создаются: их источник — seed, и этот контракт их не
    редактирует. Поэтому у нового товара sizes пустой список.
    """
    if not _brand_exists(session, data.brand_id):
        raise BrandNotFound(f"brand {data.brand_id} does not exist")

    values = data.writable_values()
    product = Product(
        name=values["name"],
        brand_id=values["brand_id"],
        price=values["price"],
        description=values["description"],
        # image_url в ProductWrite уже строка (HttpUrl -> str), в колонке Text.
        image_url=values["image_url"],
    )
    session.add(product)
    _commit(session, "product could not be created: integrity conflict")

    # Перечитываем с размерами: после commit объект просрочен, а sizes
    # нужны для ответа — ленивая загрузка здесь была бы лишним запросом
    # (или ошибкой, если сессия уже закрыта).
    created = get_product(session, product.id)
    if created is None:  # pragma: no cover - сразу после commit строки быть не может
        raise ProductNotFound("product disappeared right after creation")
    return created


def update_product(session: Session, product_id: int, data: ProductWrite) -> Product:
    """Заменить редактируемые поля карточки (полный контракт, не PATCH).

    Обязательные name/brand_id/price и defaults у description/image_url уже
    обеспечены ProductWrite: пропущенные description и image_url приходят
    как '' и null, то есть PUT действительно ЗАМЕНЯЕТ представление, а не
    обновляет часть полей.

    sizes не редактируются и не удаляются: строки product_sizes живут
    отдельно, здесь по ним не выполняется ни одного оператора.
    """
    product = get_product(session, product_id)
    if product is None:
        raise ProductNotFound(f"product {product_id} does not exist")

    if not _brand_exists(session, data.brand_id):
        # Полный PUT обязан оставить карточку валидной: с несуществующим
        # брендом её сохранять нельзя, поэтому отказ, а не частичная запись.
        raise BrandNotFound(f"brand {data.brand_id} does not exist")

    values = data.writable_values()
    product.name = values["name"]
    product.brand_id = values["brand_id"]
    product.price = values["price"]
    product.description = values["description"]
    product.image_url = values["image_url"]

    _commit(session, "product could not be updated: integrity conflict")

    updated = get_product(session, product_id)
    if updated is None:  # pragma: no cover - строку никто не удалял
        raise ProductNotFound(f"product {product_id} disappeared during update")
    return updated
