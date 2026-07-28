"""FastAPI dependency functions for authentication and authorization."""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.errors import (
    AuthenticationError,
    KeyExpiredError,
    KeyRevokedError,
    ScopeError,
)
from app.core.security import update_last_used, verify_api_key_hash
from app.models.api_key import ApiKey

logger = logging.getLogger(__name__)

# ── Valid scope strings ──────────────────────────────────────────────────────
SCOPE_FETCH = "fetch"
SCOPE_ARCHIVE = "archive"
SCOPE_ADMIN = "admin"
SCOPE_KEYS = "keys"

ALL_SCOPES = frozenset({SCOPE_FETCH, SCOPE_ARCHIVE, SCOPE_ADMIN, SCOPE_KEYS})


async def resolve_api_key(
    x_api_key: str = Header(..., alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> ApiKey:
    """Resolve and validate an API key from the X-API-Key header.

    1. Extract prefix (first 8 chars of raw key).
    2. Look up matching ApiKey row(s) by prefix.
    3. Verify argon2id hash, check revocation and expiry.
    4. Fire-and-forget ``update_last_used``.
    5. Return the ORM row.
    """
    # Edge case: key too short for prefix extraction.
    if len(x_api_key) < 8:
        raise AuthenticationError

    prefix = x_api_key[:8]

    stmt = select(ApiKey).where(ApiKey.prefix == prefix, ApiKey.is_active.is_(True))
    result = await db.execute(stmt)
    rows = result.scalars().all()

    matched: ApiKey | None = None
    for row in rows:
        if verify_api_key_hash(x_api_key, row.hashed_key):
            matched = row
            break

    if matched is None:
        raise AuthenticationError

    row = matched

    if row.revoked_at is not None:
        raise KeyRevokedError

    if row.expires_at is not None and row.expires_at < datetime.now(UTC):
        raise KeyExpiredError

    # Hash already verified in the loop above — we only reach here if it passed.
    _task = asyncio.create_task(_update_last_used_safe(row.id, db))  # noqa: RUF006

    return row


async def _update_last_used_safe(key_id: UUID, db: AsyncSession) -> None:
    """Wrapper that ensures ``update_last_used`` never propagates exceptions."""
    try:
        await update_last_used(str(key_id), db)
    except Exception:
        logger.warning("update_last_used task failed for key_id=%s", key_id, exc_info=True)


def require_scope(scope: str) -> Callable[[ApiKey], ApiKey]:
    """Return a FastAPI dependency that checks *scope* is in the resolved key's scopes.

    Usage::

        @router.post("/keys")
        async def create_key(
            api_key: ApiKey = Depends(require_scope(SCOPE_KEYS)),
        ):
            ...
    """
    if scope not in ALL_SCOPES:
        raise ValueError(f"Invalid scope: {scope!r}")

    def _check(api_key: ApiKey = Depends(resolve_api_key)) -> ApiKey:
        if scope not in api_key.scopes:
            raise ScopeError(detail=f"Scope '{scope}' required")
        return api_key

    return _check
