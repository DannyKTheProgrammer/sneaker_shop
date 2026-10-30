# Импорт моделей регистрирует все таблицы в Base.metadata (нужно Alembic autogenerate).

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
