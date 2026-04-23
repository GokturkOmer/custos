"""Alembic migration ortam konfigürasyonu.

Settings'ten veritabanı URL'sini alır ve migration'ları
asyncpg driver'ı üzerinden çalıştırır.

v1.0.1 kalem 11b: Önceki sync implementation psycopg2 gerekiyordu;
pyproject.toml'a ek bağımlılık eklememek için async_engine_from_config +
asyncio.run pattern'ine geçildi (CLAUDE.md bağımlılık onay kuralı).
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from custos.shared.config import settings

config = context.config

# SQLAlchemy asyncpg dialect URL — database_url_async 'postgresql://' döndürür,
# alembic için 'postgresql+asyncpg://' prefix zorunlu (SQLAlchemy dialect registry).
_async_url = settings.database_url_async.replace(
    "postgresql://", "postgresql+asyncpg://", 1
)
config.set_main_option("sqlalchemy.url", _async_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def run_migrations_offline() -> None:
    """Offline modda migration çalıştırır (bağlantı olmadan).

    asyncpg driver'ı offline modda kullanılmaz; literal SQL üretir.
    URL'den '+asyncpg' prefix'ini geri çıkarırız ki literal_binds doğru render etsin.
    """
    url = config.get_main_option("sqlalchemy.url") or ""
    url = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Sync context içinde migration komutlarını çalıştırır."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Online modda migration çalıştırır (asyncpg bağlantı)."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
