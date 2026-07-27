"""Async SQLAlchemy engine, session factory, and connection validator."""

import logging
from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

logger = logging.getLogger(__name__)

engine = create_async_engine(
    str(settings.database_url),
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def validate_db_connection() -> None:
    """Verify the database is reachable by executing ``SELECT 1``.

    Called from the FastAPI lifespan hook at startup. Raises
    ``RuntimeError`` with a human-readable message if the connection
    cannot be established.
    """
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            await conn.commit()
    except Exception as exc:
        raise RuntimeError(f"Database connection failed. Check DATABASE_URL. Error: {exc}") from exc
