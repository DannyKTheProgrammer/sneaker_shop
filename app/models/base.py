# Единственный Base на приложение: иначе Alembic увидит неполные метаданные.

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Базовый класс ORM-моделей."""

    pass
