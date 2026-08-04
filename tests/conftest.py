
import pytest
from unittest.mock import AsyncMock, patch as _patch


@pytest.fixture(autouse=True)
def mock_url_guard():
    """Bypass SSRF/URL guard DNS lookup in all tests."""
    with _patch(
        "app.core.url_guard.validate_url_async",
        new=AsyncMock(return_value=None),
    ):
        yield

"""Shared fixtures: testcontainers for Postgres/Redis, FastAPI app, factories."""

import os
from collections.abc import AsyncGenerator, Iterator
from uuid import uuid4

import pytest

# Ensure test settings override production before any app imports.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/testdb")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("API_KEYS_RAW", "")
os.environ.setdefault("S3_ACCESS_KEY", "test")


@pytest.fixture(scope="session")
def _postgres_dsn() -> Iterator[str]:
    """Session-scoped Postgres testcontainer."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine") as pg:
        raw = pg.get_connection_url()
        yield raw.replace("postgresql+psycopg2://", "postgresql+asyncpg://")


@pytest.fixture(scope="session")
def _redis_url() -> Iterator[str]:
    """Session-scoped Redis testcontainer."""
    from testcontainers.redis import RedisContainer

    with RedisContainer("redis:7-alpine") as rc:
        host = rc.get_container_host_ip()
        port = rc.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"


@pytest.fixture
async def db_session(_postgres_dsn: str) -> AsyncGenerator:
    """Per-test async DB session bound to Postgres testcontainer."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine(_postgres_dsn, echo=False)
    async with engine.begin() as conn:
        # Import all models so create_all discovers them.
        import app.models.api_key
        import app.models.application
        import app.models.domain_policy
        import app.models.legacy_crawl_result
        import app.models.legacy_project
        import app.models.proxy
        import app.models.proxy_pool
        import app.models.request_log
        import app.models.tenant
        import app.models.usage_counter
        import app.models.warc_index  # noqa: F401
        from app.core.db import Base

        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session: AsyncSession = session_factory()
    try:
        yield session
    finally:
        import asyncio

        # Let fire-and-forget tasks (update_last_used) settle before closing.
        await asyncio.sleep(0.05)
        try:
            await session.close()
        except Exception:  # noqa: S110
            pass
        await engine.dispose()


@pytest.fixture
async def redis_client(_redis_url: str) -> AsyncGenerator:
    """Per-test Redis client (flushed)."""
    import redis.asyncio as aioredis

    client = aioredis.from_url(_redis_url, decode_responses=False)
    await client.flushall()
    yield client
    await client.flushall()
    await client.aclose()


@pytest.fixture
async def app(_postgres_dsn: str, _redis_url: str) -> AsyncGenerator:
    """FastAPI app wired to test containers."""
    from app.core.config import settings

    # Override settings for tests.
    settings.database_url = _postgres_dsn  # type: ignore[assignment]
    settings.redis_url = _redis_url
    settings.api_keys_raw = "crw_live_testkey1234567890abcdefghij"
    settings.callback_hmac_secret = "test-secret"
    settings.enable_metrics = False
    settings.enable_tracing = False
    settings.s3_access_key = ""

    from app.main import app

    yield app


# ── Factory fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
async def tenant_factory(db_session):
    """Create a Tenant row."""
    from app.models.tenant import Tenant

    async def _make(name: str | None = None) -> Tenant:
        row = Tenant(name=name or f"test-tenant-{uuid4().hex[:8]}")
        db_session.add(row)
        await db_session.commit()
        await db_session.refresh(row)
        return row

    return _make


@pytest.fixture
async def application_factory(db_session, tenant_factory):
    """Create an Application row."""
    from app.models.application import Application

    async def _make(tenant=None, name: str | None = None) -> Application:
        t = tenant or await tenant_factory()
        row = Application(tenant_id=t.id, name=name or f"test-app-{uuid4().hex[:8]}")
        db_session.add(row)
        await db_session.commit()
        await db_session.refresh(row)
        return row

    return _make


@pytest.fixture
async def api_key_factory(db_session, application_factory):
    """Create an ApiKey row, returns (raw_key, ApiKey)."""
    from app.core.security import generate_api_key
    from app.models.api_key import ApiKey

    async def _make(application=None, scopes: list[str] | None = None) -> tuple[str, ApiKey]:
        raw, hashed = generate_api_key()
        app = application or await application_factory()
        row = ApiKey(
            application_id=app.id,
            prefix=raw[:8],
            hashed_key=hashed,
            scopes=scopes or ["fetch"],
            mode="live",
        )
        db_session.add(row)
        await db_session.commit()
        await db_session.refresh(row)
        return raw, row

    return _make


@pytest.fixture
def route_get_fetcher(monkeypatch):
    """Route app.services.fetchers.get_fetcher to the fetcher passed into fetch_with_retry."""
    import app.services.fetchers as _f
    import app.services.fetchers.base as _b

    holder = type("H", (), {"target": None})()

    class _Delegate:
        async def fetch(self, url, **kw):
            return await holder.target.fetch(url, **kw)

    monkeypatch.setattr(_f, "get_fetcher", lambda engine, **kw: _Delegate(), raising=False)

    _orig = _b.fetch_with_retry

    async def _wrapped(*args, **kwargs):
        holder.target = kwargs.get("fetcher") or (args[0] if args else None)
        return await _orig(*args, **kwargs)

    monkeypatch.setattr(_b, "fetch_with_retry", _wrapped)
    monkeypatch.setattr(_b.asyncio, "sleep", AsyncMock())
    yield
