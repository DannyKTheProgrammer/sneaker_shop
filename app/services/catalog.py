# Чтение и запись каталога. Без FastAPI и HTTP-ошибок (их выбирает роутер).
# Наружу отдаются Pydantic DTO, не ORM-объекты.

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Select, and_, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models import Brand, CartItem, OrderItem, Product, ProductSize
from app.schemas.catalog import BrandRead, ProductRead, ProductWrite


class CatalogError(Exception):
    """Ошибка каталога, которую вызывающий код переводит в HTTP-ответ."""


class BrandNotFound(CatalogError):
    """Бренда с таким id нет."""


class ProductNotFound(CatalogError):
    """Товара с таким id нет."""


class CatalogConflict(CatalogError):
    """Конфликт целостности: гонка или нарушенное ограничение."""


class ProductDeletedConflict(CatalogConflict):
    """Товар нельзя удалить: на его размеры ссылаются корзина или заказы."""


@dataclass(slots=True)
class ProductFilters:
    """Фильтры списка товаров; необязательные, сочетаются через AND."""

    brand_id: int | None = None
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    size: Decimal | None = None
    search: str | None = None
    limit: int = 20
    offset: int = 0


def _product_filters(filters: ProductFilters) -> list:
    """Собрать условия WHERE по заданным фильтрам."""
    conditions = []

    if filters.brand_id is not None:
        conditions.append(Product.brand_id == filters.brand_id)

    # Границы включительные.
    if filters.min_price is not None:
        conditions.append(Product.price >= filters.min_price)
    if filters.max_price is not None:
        conditions.append(Product.price <= filters.max_price)

    if filters.size is not None:
        # EXISTS, а не JOIN: JOIN размножил бы товары и LIMIT резал бы дубликаты.
        # Размер считается доступным при stock > 0.
        conditions.append(
            Product.sizes.any(
                and_(
                    ProductSize.size == filters.size,
                    ProductSize.stock > 0,
                )
            )
        )

    if filters.search:
        # autoescape: % и _ в запросе трактуются буквально.
        conditions.append(Product.name.icontains(filters.search, autoescape=True))

    return conditions


def list_brands(session: Session) -> list[BrandRead]:
    """Вернуть все бренды, отсортированные по id."""
    brands = session.scalars(select(Brand).order_by(Brand.id))
    return [BrandRead.model_validate(brand) for brand in brands]


def list_products(session: Session, filters: ProductFilters | None = None) -> list[ProductRead]:
    """Вернуть товары с размерами по фильтрам, отсортированные по id, с LIMIT/OFFSET.

    Размеры грузятся через selectinload (без N+1). Offset-пагинация не стабильна
    при параллельных вставках и удалениях.
    """
    active = filters or ProductFilters()
    stmt: Select = select(Product).options(selectinload(Product.sizes))

    conditions = _product_filters(active)
    if conditions:
        stmt = stmt.where(and_(*conditions))

    stmt = stmt.order_by(Product.id).limit(active.limit).offset(active.offset)
    return [ProductRead.model_validate(product) for product in session.scalars(stmt)]


def _load_product(session: Session, product_id: int) -> Product | None:
    """Загрузить ORM-товар с размерами или None."""
    stmt = (
        select(Product)
        .options(selectinload(Product.sizes))
        .where(Product.id == product_id)
    )
    return session.scalars(stmt).first()


def get_product(session: Session, product_id: int) -> ProductRead | None:
    """Вернуть товар с размерами или None, если его нет."""
    product = _load_product(session, product_id)
    if product is None:
        return None
    return ProductRead.model_validate(product)


# Запись каталога: commit/rollback делает сервис, роутер только переводит исключения в HTTP.


def _brand_exists(session: Session, brand_id: int) -> bool:
    """Проверить, что бренд с таким id существует."""
    return session.get(Brand, brand_id) is not None


def _commit(session: Session, conflict_message: str) -> None:
    """Зафиксировать транзакцию; IntegrityError превращается в CatalogConflict."""
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise CatalogConflict(conflict_message) from exc


def create_product(session: Session, data: ProductWrite) -> ProductRead:
    """Создать товар; бренд проверяется до вставки (иначе будет ошибка FK).

    Размеры не создаются: у нового товара sizes пуст.
    """
    if not _brand_exists(session, data.brand_id):
        raise BrandNotFound(f"brand {data.brand_id} does not exist")

    values = data.writable_values()
    product = Product(
        name=values["name"],
        brand_id=values["brand_id"],
        price=values["price"],
        description=values["description"],
        image_url=values["image_url"],
    )
    session.add(product)
    _commit(session, "product could not be created: integrity conflict")

    # После commit объект просрочен: перечитываем вместе с размерами.
    created = _load_product(session, product.id)
    if created is None:  # pragma: no cover - сразу после commit строки быть не может
        raise ProductNotFound("product disappeared right after creation")
    return ProductRead.model_validate(created)


def update_product(session: Session, product_id: int, data: ProductWrite) -> ProductRead:
    """Полностью заменить редактируемые поля товара (PUT); размеры не меняются."""
    product = _load_product(session, product_id)
    if product is None:
        raise ProductNotFound(f"product {product_id} does not exist")

    if not _brand_exists(session, data.brand_id):
        raise BrandNotFound(f"brand {data.brand_id} does not exist")

    values = data.writable_values()
    product.name = values["name"]
    product.brand_id = values["brand_id"]
    product.price = values["price"]
    product.description = values["description"]
    product.image_url = values["image_url"]

    _commit(session, "product could not be updated: integrity conflict")

    updated = _load_product(session, product_id)
    if updated is None:  # pragma: no cover - строку никто не удалял
        raise ProductNotFound(f"product {product_id} disappeared during update")
    return ProductRead.model_validate(updated)


def _size_ids(session: Session, product_id: int) -> list[int]:
    """Вернуть id всех размеров товара."""
    return list(session.scalars(select(ProductSize.id).where(ProductSize.product_id == product_id)))


def _references(session: Session, size_ids: list[int]) -> list[str]:
    """Вернуть имена таблиц (cart_items, order_items), ссылающихся на размеры."""
    if not size_ids:
        return []
    referenced = []
    if session.scalar(select(CartItem.id).where(CartItem.product_size_id.in_(size_ids)).limit(1)) is not None:
        referenced.append("cart_items")
    if session.scalar(select(OrderItem.id).where(OrderItem.product_size_id.in_(size_ids)).limit(1)) is not None:
        referenced.append("order_items")
    return referenced


def delete_product(session: Session, product_id: int) -> None:
    """Удалить товар вместе с размерами одной транзакцией.

    Нет товара -> ProductNotFound; есть ссылки из корзины или заказов ->
    ProductDeletedConflict, ничего не удаляется. Каскада в схеме нет, поэтому
    размеры удаляются явно и раньше товара.
    """
    product = session.get(Product, product_id)
    if product is None:
        raise ProductNotFound(f"product {product_id} does not exist")

    size_ids = _size_ids(session, product_id)
    referenced = _references(session, size_ids)
    if referenced:
        raise ProductDeletedConflict(
            f"product {product_id} is referenced by {', '.join(referenced)}"
        )

    try:
        if size_ids:
            session.execute(delete(ProductSize).where(ProductSize.product_id == product_id))
        session.execute(delete(Product).where(Product.id == product_id))
        session.commit()
    except IntegrityError as exc:
        # Ссылку добавили параллельно после проверки.
        session.rollback()
        raise ProductDeletedConflict(
            f"product {product_id} is referenced by cart_items or order_items"
        ) from exc
