# Окружение Alembic для учебного проекта.
#
# Ключевые решения:
#   1) Пароля и URL в alembic.ini нет. Строка подключения берётся из того же
#      конфига, что и у приложения: app.core.config -> .env (DATABASE_URL).
#      Один источник правды: миграции и API ходят в одну и ту же БД.
#   2) Engine для миграций создаётся свой (poolclass=NullPool), а не берётся
#      app.database.engine: миграционный процесс — отдельный запуск, и ему
#      не нужен пул приложения. Настройки подключения при этом те же.
#   3) target_metadata = Base.metadata: сюда попадают таблицы из app.models,
#      поэтому импорт моделей обязателен — без него метаданные пусты.
#   4) compare_type=True: autogenerate сравнивает и типы колонок, а не только
#      их наличие (иначе смена Numeric(10,2) -> Numeric(12,2) осталась бы
#      незамеченной).

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.core.config import settings
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Подставляем реальный URL из окружения/.env вместо значения из ini.
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata

# Общие для online/offline настройки сравнения схемы с моделями.
COMPARE_OPTIONS = {
    "compare_type": True,
    "compare_server_default": False,
}


def run_migrations_offline() -> None:
    """Offline-режим: SQL генерируется без подключения к БД."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **COMPARE_OPTIONS,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Online-режим: миграции применяются к реальной БД."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            **COMPARE_OPTIONS,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
