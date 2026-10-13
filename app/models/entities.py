# ORM-модели магазина (стиль SQLAlchemy 2.0: Mapped + mapped_column).
#
# Соответствие рисунку M1:
#   brands 1─N products 1─N product_sizes
#   users  1─N cart_items  ──N─1── product_sizes
#   users  1─N orders     1─N order_items
# Здесь все семь таблиц из metadata.
#
# Снимок цены против ссылки на цену:
#   order_items.price_at_purchase — цена ОДНОЙ пары, зафиксированная на
#   момент оформления. Это не ссылка на products.price и не сумма позиции:
#   если товар подорожает, старая покупка не изменится в отчётах.
#   orders.total — сумма всего заказа, тоже снимок; БД проверяет лишь,
#   что он не отрицательный, а равенство сумме позиций обеспечивает код.
#
# Связи описаны на трёх уровнях, и это разные вещи:
#   1) ForeignKey("brands.id") — ограничение В БД: колонка brand_id может
#      содержать только существующий brands.id; вставка мусора отклоняется
#      сервером.
#   2) CheckConstraint / UniqueConstraint — ограничения целостности БД.
#   3) relationship(...) — НЕ колонка и НЕ ограничение БД: это способ
#      SQLAlchemy навигировать по объектам в Python (brand.products,
#      product.brand) и сам решает, какие JOIN/UPDATE выполнить.
#      relationship ничего не запрещает на уровне сервера: без ForeignKey
#      связи в Python работали бы, но БД допускала бы битые ссылки.
#
# Каскадного удаления истории здесь нет намеренно: ondelete/cascade
# не заданы, то есть удаление бренда с товарами или товара с размерами
# БД не разрешит (ForeignKeyError), а не тихо снесёт данные.

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Brand(Base):
    """Бренд (производитель) кроссовок."""

    __tablename__ = "brands"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)

    products: Mapped[list["Product"]] = relationship(
        back_populates="brand",
    )


class Product(Base):
    """Модель кроссовок: одна карточка товара, размеры — в product_sizes."""

    __tablename__ = "products"
    __table_args__ = (
        # Цена строго положительная: 0 и отрицательные значения — ошибка данных.
        CheckConstraint("price > 0", name="ck_products_price"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # ondelete не задан: история заказов/размеров не должна исчезать
    # вместе с брендом или товаром.
    brand_id: Mapped[int] = mapped_column(
        ForeignKey("brands.id"), nullable=False
    )
    # server_default на стороне БД: пустая строка, а не NULL,
    # чтобы описание всегда было строкой и код не разбирал два случая.
    description: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=""
    )
    # Деньги — только Decimal: float даёт ошибки округления (0.1 + 0.2).
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # timestamptz: время с зоной; func.now() считает время сервер БД.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    brand: Mapped["Brand"] = relationship(
        back_populates="products",
    )
    sizes: Mapped[list["ProductSize"]] = relationship(
        back_populates="product",
    )


class ProductSize(Base):
    """Размер конкретного товара и его остаток на складе.

    Остаток живёт здесь, а не в products: у одного товара размеров много,
    и склад считается по паре (товар, размер).
    """

    __tablename__ = "product_sizes"
    __table_args__ = (
        CheckConstraint("size > 0", name="ck_size_positive"),
        CheckConstraint("stock >= 0", name="ck_stock"),
        # Один и тот же размер у товара не дублируется: остаток не размазан
        # по двум строкам, а склад обновляет одну конкретную.
        UniqueConstraint("product_id", "size", name="uq_product_size"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id"), nullable=False
    )
    # Размер дробный (41.5), поэтому Numeric(4,1), а не Integer.
    size: Mapped[Decimal] = mapped_column(Numeric(4, 1), nullable=False)
    stock: Mapped[int] = mapped_column(Integer, nullable=False)

    product: Mapped["Product"] = relationship(
        back_populates="sizes",
    )


class User(Base):
    """Покупатель.

    В таблице лежит только хэш пароля (hashed_password), не сам пароль:
    по БД нельзя восстановить исходный пароль пользователя.
    Регистрации, выдачи токенов и проверки прав в этом модуле нет —
    это только структура таблицы.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    # UNIQUE на уровне БД: два аккаунта с одним email невозможны,
    # даже если проверку в коде случайно обойдут.
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    hashed_password: Mapped[str] = mapped_column(Text, nullable=False)
    # server_default false: обычный покупатель по умолчанию, админ —
    # исключение, которое выставляется явно.
    is_admin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    cart_items: Mapped[list["CartItem"]] = relationship(
        back_populates="user",
    )
    orders: Mapped[list["Order"]] = relationship(
        back_populates="user",
    )


class CartItem(Base):
    """Позиция корзины: сколько единиц конкретного размера хочет пользователь.

    Ссылаемся на product_sizes, а не на products: заказ и корзина всегда
    про конкретный размер, складской остаток тоже хранится по размеру.
    """

    __tablename__ = "cart_items"
    __table_args__ = (
        # Ноль и отрицательное количество — не «пустая позиция», а ошибка:
        # чтобы убрать товар, строку удаляют.
        CheckConstraint("quantity > 0", name="ck_cart_quantity"),
        # Одна строка на пару (пользователь, размер). Составная уникальность
        # НЕ суммирует quantity: повторная вставка такой пары будет отклонена
        # базой, а не превратится в quantity = 1 + 1. Увеличение количества —
        # это UPDATE существующей строки, отдельное действие в коде.
        UniqueConstraint("user_id", "product_size_id", name="uq_cart_size"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    # Ссылка на владельца корзины: чьи это строки. Кто именно делает
    # HTTP-запрос, из этой колонки не следует — её подставляет код
    # приложения по аутентификации.
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    product_size_id: Mapped[int] = mapped_column(
        ForeignKey("product_sizes.id"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    user: Mapped["User"] = relationship(
        back_populates="cart_items",
    )
    # Связь односторонняя: обратной коллекции у ProductSize нет.
    # Это не мешает позже добавить её без миграции — relationship
    # живёт только в Python и схему БД не меняет.
    product_size: Mapped["ProductSize"] = relationship()


class Order(Base):
    """Заказ: «шапка» документа, позиции — в order_items.

    total хранится в таблице, а не вычисляется из позиций при чтении:
    это снимок суммы на момент оформления. Составных ограничений на связь
    total и позиций в БД нет — их согласованность обеспечивает код
    оформления заказа (следующие модули).
    """

    __tablename__ = "orders"
    __table_args__ = (
        # Статус — только из закрытого списка: опечатка вроде 'complete'
        # не станет новым статусом, а будет отклонена.
        CheckConstraint(
            "status IN ('created', 'processing', 'completed')",
            name="ck_order_status",
        ),
        # Отрицательная сумма — ошибка данных (скидки/возвраты
        # в модели не выражены).
        CheckConstraint("total >= 0", name="ck_order_total"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    # server_default на стороне БД: заказ, созданный без явного статуса,
    # получает 'created' от PostgreSQL, независимо от того, какой код
    # его вставил.
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'created'")
    )
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped["User"] = relationship(
        back_populates="orders",
    )
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order",
    )


class OrderItem(Base):
    """Позиция заказа: сколько пар какого размера куплено и по какой цене.

    price_at_purchase — цена ОДНОЙ пары на момент покупки (снимок),
    а не текущая цена товара и не стоимость всей позиции.
    Стоимость позиции = quantity * price_at_purchase, её считает код.
    """

    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_item_quantity"),
        CheckConstraint("price_at_purchase > 0", name="ck_item_price"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    product_size_id: Mapped[int] = mapped_column(
        ForeignKey("product_sizes.id"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    # Деньги — Decimal: снимок цены обязан быть точным, иначе суммы
    # заказов будут расходиться на копейки.
    price_at_purchase: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False
    )

    order: Mapped["Order"] = relationship(
        back_populates="items",
    )
    # Односторонняя связь, как и у CartItem: обратной коллекции
    # у ProductSize нет.
    product_size: Mapped["ProductSize"] = relationship()
