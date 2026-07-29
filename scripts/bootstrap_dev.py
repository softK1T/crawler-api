#!/usr/bin/env python3
"""Bootstrap script — creates a tenant, application, and admin API key for local dev.

Usage: python scripts/bootstrap_dev.py
Prints the raw API key to stdout — capture it and use as X-API-Key.
Idempotent: skips tenant/app creation if they already exist.
"""

import asyncio
import os
import sys

# Ensure the project root is on sys.path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def _bootstrap() -> str:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.core.config import settings
    from app.core.security import generate_api_key
    from app.models.api_key import ApiKey
    from app.models.application import Application
    from app.models.tenant import Tenant

    engine = create_async_engine(str(settings.database_url), echo=False)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as db:
        # Tenant — idempotent.
        result = await db.execute(select(Tenant).where(Tenant.name == "dev-tenant"))
        tenant = result.scalar_one_or_none()
        if tenant is None:
            tenant = Tenant(name="dev-tenant")
            db.add(tenant)
            await db.commit()
            await db.refresh(tenant)

        # Application — idempotent.
        result = await db.execute(
            select(Application).where(Application.tenant_id == tenant.id, Application.name == "dev-app")
        )
        app = result.scalar_one_or_none()
        if app is None:
            app = Application(tenant_id=tenant.id, name="dev-app")
            db.add(app)
            await db.commit()
            await db.refresh(app)

        # API key with admin + fetch scopes — always create a new one.
        raw_key, hashed_key = generate_api_key(mode="live")
        api_key = ApiKey(
            application_id=app.id,
            prefix=raw_key[:8],
            hashed_key=hashed_key,
            scopes=["fetch", "archive", "admin", "keys"],
            mode="live",
        )
        db.add(api_key)
        await db.commit()

        print(raw_key)
        return raw_key

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_bootstrap())
