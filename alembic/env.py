# Окружение Alembic: URL берётся из app.core.config (DATABASE_URL), не из alembic.ini.
# Свой Engine с NullPool; импорт app.models обязателен, иначе метаданные пусты.

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.core.config import settings
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# URL, заданный программно (тесты, скрипты), главнее .env: иначе миграция уйдёт в рабочую БД.
def _migration_url() -> str:
    configured = config.get_main_option("sqlalchemy.url", None)
    if configured:
        return configured
    config.set_main_option("sqlalchemy.url", settings.database_url)
    return settings.database_url


MIGRATION_URL = _migration_url()

target_metadata = Base.metadata

COMPARE_OPTIONS = {
    "compare_type": True,
    "compare_server_default": False,
}


def run_migrations_offline() -> None:
    """Сгенерировать SQL без подключения к БД."""
    context.configure(
        url=MIGRATION_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **COMPARE_OPTIONS,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Применить миграции к БД."""
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
