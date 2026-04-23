"""Alembic migration ortam konfigürasyonu.

Settings'ten veritabanı URL'sini alır ve migration'ları
sync modda çalıştırır.
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from custos.shared.config import settings

config = context.config

# Settings'ten veritabanı URL'sini inject et
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def run_migrations_offline() -> None:
    """Offline modda migration çalıştırır (bağlantı olmadan)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Online modda migration çalıştırır (gerçek bağlantı ile)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
