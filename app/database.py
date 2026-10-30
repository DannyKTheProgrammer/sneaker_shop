# Один Engine на процесс (пул ленивый) и фабрика синхронных сессий.
# Три таймаута (connect, pool, statement) ограничивают разные ожидания
# и не образуют единого дедлайна запроса.

from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_timeout=3,
    connect_args={
        "connect_timeout": 3,
        # statement_timeout в миллисекундах.
        "options": "-c statement_timeout=3000",
    },
)

SessionLocal = sessionmaker(
    bind=engine,
    class_=Session,
    autoflush=False,
    expire_on_commit=False,
)


def get_session() -> Iterator[Session]:
    """Зависимость FastAPI: сессия на запрос, закрывается даже при ошибке."""
    with SessionLocal() as session:
        yield session


def ping() -> None:
    """Выполнить SELECT 1; исключения не глушатся."""
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
