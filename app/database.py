# Подключение к БД: один Engine на процесс + фабрика синхронных сессий.
#
# Почему Engine один: Engine — это фабрика соединений и владелец пула.
# Новый Engine на каждый запрос = новый пул и лишние TCP-подключения
# к PostgreSQL, поэтому он создаётся на уровне модуля и живёт весь процесс.
# Пул соединений при этом ленивый: реальные подключения появляются
# при первом обращении, а не при импорте.
#
# Три таймаута ниже ограничивают РАЗНЫЕ ожидания и не образуют единый
# дедлайн HTTP-запроса (подробное объяснение — в README ответа):
#   connect_timeout   = 3 c   — ожидание установления соединения с сервером;
#   pool_timeout      = 3 c   — ожидание свободного соединения из пула SQLAlchemy;
#   statement_timeout = 3000 мс — лимит выполнения SQL на стороне PostgreSQL.
# Худший случай их суммы (3 + 3 + 3) больше любого из них по отдельности,
# поэтому по /health нельзя обещать клиенту фиксированный дедлайн.

from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,  # проверять соединение перед выдачей из пула
    pool_timeout=3,  # сколько ждать свободное соединение из пула, секунд
    connect_args={
        "connect_timeout": 3,  # сколько ждать установку соединения, секунд
        # statement_timeout задаётся серверу в миллисекундах через options.
        # 3000 мс = 3 с; это предел выполнения одного SQL-запроса в PostgreSQL.
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
    """Зависимость FastAPI: сессия на один запрос.

    Ей пользуются каталог (чтение и запись), регистрация, вход и /auth/me.
    Корзина и заказы работают в своей сессии (app/services/shop.py), а
    пользователя для них читает короткая сессия get_current_user_id.
    Гарантирует закрытие сессии даже при ошибке в обработчике.
    """
    with SessionLocal() as session:
        yield session


def ping() -> None:
    """Проверка доступности БД: SELECT 1 в отдельном соединении.

    Ничего не возвращает: для /health важен сам факт, что запрос прошёл.
    Исключения не глушатся — их обрабатывает вызывающий код.
    """
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
