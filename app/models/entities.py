# ORM-модели магазина (стиль SQLAlchemy 2.0: Mapped + mapped_column).
#
# Соответствие рисунку M1:
#   brands 1─N products 1─N product_sizes
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
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
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
