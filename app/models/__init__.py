# Публичный интерфейс пакета моделей.
#
# Импорт моделей здесь нужен не только для удобства: он гарантирует,
# что при `from app.models import Base` все три класса уже зарегистрированы
# в Base.metadata. Если импортировать только Base, таблиц в метаданных
# не будет вообще — Alembic autogenerate увидел бы пустую схему.

from app.models.base import Base
from app.models.entities import Brand, Product, ProductSize

__all__ = ["Base", "Brand", "Product", "ProductSize"]
