"""Alembic environment configuration for async SQLAlchemy.

Reads DATABASE_URL from app.core.config (pydantic-settings). The URL is NOT
duplicated in alembic.ini to avoid secret sprawl.

Autogenerate caveat (by design, not a bug):
    - ``postgresql_partition_by`` on ``RequestLog`` is set as a table-arg
      attribute; Alembic does not introspect partition definitions, so
      ``alembic check`` will report "can't compare" on the partition clause.
      Partition DDL is maintained manually in upgrade() / downgrade().
"""

import asyncio
import os
import sys
from logging.config import fileConfig

# Ensure the project root is on sys.path so that `from app.*` imports work.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context

# Alembic Config object
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import models so Alembic can detect schema changes.
from app.core.db import Base  # noqa: E402
from app.models import *  # noqa: E402, F403

target_metadata = Base.metadata


def get_database_url() -> str:
    """Resolve DATABASE_URL from Settings, failing with a clear message."""
    from app.core.config import settings

    url = str(settings.database_url) if settings.database_url else ""
    if not url:
        raise RuntimeError(
            "DATABASE_URL environment variable is required for migrations. "
            "Set it in your .env file or environment before running alembic."
        )
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Configures the context with just a URL, not an Engine. Calls to
    ``context.execute()`` emit the SQL string to the script output.
    """
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    """Synchronous callback executed inside the async engine's connect context."""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migrations inside a connection context."""
    url = get_database_url()
    connectable = create_async_engine(url, poolclass=pool.NullPool)

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
