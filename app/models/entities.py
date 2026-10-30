# ORM-модели магазина (SQLAlchemy 2.0).
# Цены и суммы в заказах — снимки на момент оформления; каскадного удаления нет намеренно.
# relationship — только навигация в Python, ограничения целостности заданы в БД.

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
        CheckConstraint("price > 0", name="ck_products_price"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # ondelete не задан: история не должна исчезать вместе с брендом или товаром.
    brand_id: Mapped[int] = mapped_column(
        ForeignKey("brands.id"), nullable=False
    )
    # Пустая строка вместо NULL.
    description: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=""
    )
    # Деньги — Decimal, не float.
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    """Размер товара и его складской остаток."""

    __tablename__ = "product_sizes"
    __table_args__ = (
        CheckConstraint("size > 0", name="ck_size_positive"),
        CheckConstraint("stock >= 0", name="ck_stock"),
        UniqueConstraint("product_id", "size", name="uq_product_size"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id"), nullable=False
    )
    # Размер дробный (41.5).
    size: Mapped[Decimal] = mapped_column(Numeric(4, 1), nullable=False)
    stock: Mapped[int] = mapped_column(Integer, nullable=False)

    product: Mapped["Product"] = relationship(
        back_populates="sizes",
    )


class User(Base):
    """Пользователь; хранится только хэш пароля."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    hashed_password: Mapped[str] = mapped_column(Text, nullable=False)
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
    """Позиция корзины: количество единиц конкретного размера."""

    __tablename__ = "cart_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_cart_quantity"),
        # Повторная вставка пары отклоняется, а не суммирует quantity.
        UniqueConstraint("user_id", "product_size_id", name="uq_cart_size"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    product_size_id: Mapped[int] = mapped_column(
        ForeignKey("product_sizes.id"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    user: Mapped["User"] = relationship(
        back_populates="cart_items",
    )
    # Односторонняя связь.
    product_size: Mapped["ProductSize"] = relationship()


class Order(Base):
    """Заказ; total — снимок суммы, его согласованность с позициями обеспечивает код."""

    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('created', 'processing', 'completed')",
            name="ck_order_status",
        ),
        CheckConstraint("total >= 0", name="ck_order_total"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
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
    """Позиция заказа; price_at_purchase — цена одной пары на момент покупки."""

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
    price_at_purchase: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False
    )

    order: Mapped["Order"] = relationship(
        back_populates="items",
    )
    # Односторонняя связь.
    product_size: Mapped["ProductSize"] = relationship()
