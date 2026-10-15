# Чтение каталога из БД (M2 + фильтры M3). Только SELECT: ни вставок,
# ни изменений.
#
# Здесь сознательно нет ни FastAPI, ни HTTP-ошибок: сервис отвечает на
# вопрос «что лежит в БД», а решение «404/422/409» принимает роутер.
# Так сервис можно переиспользовать и тестировать без HTTP.

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Select, and_, select
from sqlalchemy.orm import Session, selectinload

from app.models import Brand, Product, ProductSize


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
