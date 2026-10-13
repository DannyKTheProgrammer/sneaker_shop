# Публичный интерфейс пакета моделей.
#
# Импорт моделей здесь нужен не только для удобства: он гарантирует,
# что при `from app.models import Base` все семь классов уже зарегистрированы
# в Base.metadata (brands, products, product_sizes, users, cart_items,
# orders, order_items). Если импортировать только Base, таблиц в метаданных
# не будет вообще — Alembic autogenerate увидел бы пустую схему.

from app.models.base import Base
from app.models.entities import (
    Brand,
    CartItem,
    Order,
    OrderItem,
    Product,
    ProductSize,
    User,
)

__all__ = [
    "Base",
    "Brand",
    "CartItem",
    "Order",
    "OrderItem",
    "Product",
    "ProductSize",
    "User",
]
