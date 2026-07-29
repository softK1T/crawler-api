"""Single source of key minting and rotation.

Both the HTTP endpoint (POST /v1/keys, POST /v1/keys/{key_id}/rotate) and
scripts/bootstrap_dev.py call these functions.  No second key-minting path
may exist after this module is introduced.

Authorization (scope checks, tenancy) lives in the endpoint layer.  The
service trusts its caller and performs only mechanical validation.
"""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import ALL_SCOPES
from app.core.config import settings
from app.core.errors import AuthorizationError, ConflictError, NotFoundError
from app.core.security import generate_api_key
from app.models.api_key import ApiKey

logger = logging.getLogger(__name__)


async def create_api_key(
    db: AsyncSession,
    *,
    application_id: UUID,
    scopes: list[str],
    mode: str,
    issuer_key_id: UUID | None = None,
    expires_at: datetime | None = None,
) -> tuple[ApiKey, str]:
    """Mint a new API key row and return (row, raw_key).

    The raw key is returned exactly once — caller must never log it.
    Validates that every scope is a known scope string; privilege checks
    (D2) are the endpoint's responsibility.
    """
    # Validate every scope is a known scope (data integrity, not auth).
    for scope in scopes:
        if scope not in ALL_SCOPES:
            raise AuthorizationError(detail=f"Invalid scope: {scope}")

    # Prefix collision retry — exactly two attempts (preserves existing semantics).
    for _attempt in range(2):
        raw_key, hashed_key = generate_api_key(mode)
        prefix = raw_key[:8]

        existing = await db.execute(select(ApiKey).where(ApiKey.prefix == prefix))
        if existing.scalar_one_or_none() is not None:
            if _attempt == 1:
                raise ConflictError(detail="Key prefix collision — retry")
            continue

        row = ApiKey(
            application_id=application_id,
            prefix=prefix,
            hashed_key=hashed_key,
            scopes=scopes,
            mode=mode,
            expires_at=expires_at,
            issuer_key_id=issuer_key_id,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row, raw_key

    raise ConflictError(detail="Key prefix collision — retry exhausted")


async def rotate_api_key(
    db: AsyncSession,
    *,
    key_id: UUID,
    issuer_key_id: UUID,
    overlap_hours: int | None = None,
) -> tuple[ApiKey, str]:
    """Rotate an existing key: mint a successor and set the old key's expiry.

    The old key is NOT revoked — it remains valid during the overlap window.
    The successor inherits the same application_id, scopes, and mode.

    Returns (successor_row, raw_key).  The raw key is never logged here.
    """
    if overlap_hours is None:
        overlap_hours = settings.key_rotation_overlap_hours

    # Load the target key.
    result = await db.execute(select(ApiKey).where(ApiKey.id == key_id))
    old_key = result.scalar_one_or_none()
    if old_key is None:
        raise NotFoundError(detail="API key not found")

    if old_key.revoked_at is not None:
        raise ConflictError(detail="Cannot rotate a revoked key")

    # Mint the successor with identical application, scopes, and mode.
    raw_key, hashed_key = generate_api_key(mode=old_key.mode)
    prefix = raw_key[:8]

    # Check for prefix collision on the new key.
    existing = await db.execute(select(ApiKey).where(ApiKey.prefix == prefix))
    if existing.scalar_one_or_none() is not None:
        # One retry.
        raw_key, hashed_key = generate_api_key(mode=old_key.mode)
        prefix = raw_key[:8]
        existing2 = await db.execute(select(ApiKey).where(ApiKey.prefix == prefix))
        if existing2.scalar_one_or_none() is not None:
            raise ConflictError(detail="Key prefix collision — retry")

    successor = ApiKey(
        application_id=old_key.application_id,
        prefix=prefix,
        hashed_key=hashed_key,
        scopes=list(old_key.scopes),
        mode=old_key.mode,
        issuer_key_id=issuer_key_id,
    )
    db.add(successor)

    # Set the old key's expiry — single transaction with successor insert.
    old_key.expires_at = datetime.now(UTC) + timedelta(hours=overlap_hours)

    await db.commit()
    await db.refresh(successor)
    await db.refresh(old_key)

    logger.info(
        "Key rotated: issuer_key_id=%s rotated_key_id=%s new_key_prefix=%s "
        "old_key_expires_at=%s",
        issuer_key_id,
        key_id,
        prefix,
        old_key.expires_at.isoformat(),
    )

    return successor, raw_key
