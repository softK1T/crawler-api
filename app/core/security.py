import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.core.config import settings

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)

# Argon2id parameters (fixed per project convention).
_PH = PasswordHasher(
    time_cost=2,
    memory_cost=65536,
    parallelism=2,
    hash_len=32,
    salt_len=16,
)

# In-memory registry: prefix -> list of hashed keys.
# Populated lazily on first auth check.
_registry: dict[str, list[str]] = {}
_registry_built: bool = False


def hash_api_key(raw_key: str) -> str:
    """Hash a plaintext API key with argon2id. Returns the encoded hash string."""
    return _PH.hash(raw_key)


def verify_api_key_hash(raw_key: str, stored_hash: str) -> bool:
    """Constant-time verification of a raw key against an argon2id hash.

    Returns False immediately if *stored_hash* is empty or None (avoids
    argon2 library errors on malformed input).
    """
    if not stored_hash:
        return False
    try:
        return _PH.verify(stored_hash, raw_key)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def generate_api_key(mode: str = "live") -> tuple[str, str]:
    """Generate a new API key pair.

    The key starts with ``crwl`` (live) or ``crwt`` (test) followed by 4 random
    url-safe chars so the 8-char prefix is distinctive (~16M combinations).
    Collisions are handled by the caller (retry once, then 409).
    """
    tag = "l" if mode == "live" else "t"
    raw_key = f"crw{tag}{secrets.token_urlsafe(4)}{secrets.token_urlsafe(28)}"
    hashed_key = hash_api_key(raw_key)
    return raw_key, hashed_key


def _build_registry() -> dict[str, list[str]]:
    """Build the in-memory prefix-indexed key registry from Settings.

    Each raw key is hashed once; the first 8 characters of the raw key
    serve as the lookup prefix.
    """
    registry: dict[str, list[str]] = {}
    for raw_key in settings.api_keys:
        hashed = hash_api_key(raw_key)
        prefix = raw_key[:8]
        registry.setdefault(prefix, []).append(hashed)
    return registry


def _get_registry() -> dict[str, list[str]]:
    """Return the (lazily-built) prefix -> hashed-keys mapping."""
    global _registry, _registry_built
    if not _registry_built:
        _registry = _build_registry()
        _registry_built = True
    return _registry


def get_api_key(api_key: str | None = Security(API_KEY_HEADER)) -> str:
    """Validate the X-API-Key header via argon2id hash verification.

    - If no keys are configured, auth is disabled and ``"anonymous"`` is returned.
    - Prefix lookup (first 8 chars of the raw key) narrows the candidate set.
    - Each candidate hash is verified with constant-time argon2 comparison.
    - Returns the raw key on success; raises HTTP 401 on any failure.
    """
    if not settings.api_keys:
        # No keys configured → auth disabled (dev/local mode only).
        return "anonymous"

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )

    prefix = api_key[:8]
    candidates = _get_registry().get(prefix, [])

    for stored_hash in candidates:
        if verify_api_key_hash(api_key, stored_hash):
            return api_key

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing API key",
    )


# Backward-compatible alias — existing endpoint imports continue to work.
verify_api_key = get_api_key


async def update_last_used(key_id: str, db) -> None:
    """Fire-and-forget UPDATE of ``last_used_at`` on the ApiKey row.

    Must be called via :func:`asyncio.create_task` — never awaited directly,
    because it is non-critical and must not block the response.

    All exceptions are caught and logged; this function must never raise.
    """
    import logging
    from uuid import UUID

    from sqlalchemy import text

    logger = logging.getLogger(__name__)
    try:
        await db.execute(
            text("UPDATE api_keys SET last_used_at = now() WHERE id = :kid"),
            {"kid": UUID(key_id)},
        )
        await db.commit()
    except Exception:
        logger.warning("update_last_used failed for key_id=%s", key_id, exc_info=True)
