"""Local Compose initialization and entrypoint; never log generated credentials."""

import asyncio
import os
import pwd
import secrets
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SECRETS = Path("/run/crawler-secrets")


def seed() -> None:
    """Runs as root before PostgreSQL, once per Compose invocation."""
    SECRETS.mkdir(mode=0o755, parents=True, exist_ok=True)
    owner = pwd.getpwnam("crawler")
    os.chown(SECRETS, owner.pw_uid, owner.pw_gid)
    password = SECRETS / "postgres-password"
    if not password.exists():
        fd = os.open(password, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        with os.fdopen(fd, "w") as stream:
            stream.write(secrets.token_hex(32))
    if len(password.read_text().strip()) != 64:
        raise RuntimeError("Invalid persisted database password; refusing to replace it")
    print("Persistent database secret ready")


def configure() -> None:
    password = (SECRETS / "postgres-password").read_text().strip()
    os.environ["DATABASE_URL"] = (
        f"postgresql+asyncpg://crawler:{quote(password, safe='')}@postgres:5432/crawler"
    )


async def bootstrap(existing: str | None) -> str:
    """Reuse a valid local administrator key; do not bypass explicit revocation."""
    from sqlalchemy import select

    from app.core.db import AsyncSessionLocal, engine
    from app.core.security import verify_api_key_hash
    from app.models.api_key import ApiKey
    from app.models.application import Application
    from app.models.tenant import Tenant
    from app.services.key_service import create_api_key

    try:
        async with AsyncSessionLocal() as db:
            if existing:
                row = await db.scalar(select(ApiKey).where(ApiKey.prefix == existing[:8]))
                if (
                    row is None
                    or not row.is_active
                    or row.revoked_at is not None
                    or (row.expires_at is not None and row.expires_at <= datetime.now(UTC))
                    or not {"admin", "keys", "fetch", "archive"}.issubset(row.scopes)
                    or not verify_api_key_hash(existing, row.hashed_key)
                ):
                    raise RuntimeError(
                        "Persisted bootstrap key is invalid; explicit operator recovery required"
                    )
                return existing
            tenant = await db.scalar(select(Tenant).where(Tenant.name == "local-admin"))
            if tenant is None:
                tenant = Tenant(name="local-admin")
                db.add(tenant)
                await db.commit()
                await db.refresh(tenant)
            app = await db.scalar(
                select(Application).where(
                    Application.tenant_id == tenant.id, Application.name == "local-admin"
                )
            )
            if app is None:
                app = Application(tenant_id=tenant.id, name="local-admin")
                db.add(app)
                await db.commit()
                await db.refresh(app)
            _, raw_key = await create_api_key(
                db,
                application_id=app.id,
                scopes=["fetch", "archive", "admin", "keys"],
                mode="live",
            )
            return raw_key
    finally:
        await engine.dispose()


def initialize() -> None:
    configure()
    subprocess.run(["alembic", "upgrade", "head"], check=True)  # noqa: S603, S607
    destination = SECRETS / "admin.key"
    existing = destination.read_text().strip() if destination.exists() else None
    raw_key = asyncio.run(bootstrap(existing))
    if not existing:
        temporary = destination.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(raw_key + "\n")
        os.replace(temporary, destination)
    print("Migrations and administrator credentials ready; key omitted from logs")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Expected: secrets | initialize | key | <application command>")
    action = sys.argv[1]
    if action == "secrets":
        seed()
    elif action == "initialize":
        initialize()
    elif action == "key":
        print((SECRETS / "admin.key").read_text().strip())
    else:
        configure()
        os.execvp(action, sys.argv[1:])  # noqa: S606, S607


if __name__ == "__main__":
    main()
