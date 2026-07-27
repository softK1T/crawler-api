"""Health scoring and cooldown logic for proxy endpoints.

Persists health updates to the ``proxies`` table via the injected
AsyncSession.  Callers must commit the session after calling these
functions (they do NOT commit internally to allow batching).
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
HEALTH_DECAY = 0.15
HEALTH_RECOVER = 0.05
COOLDOWN_BASE_S = 60
COOLDOWN_MAX_S = 3600
MIN_HEALTH_SCORE = 0.0
MAX_HEALTH_SCORE = 1.0


async def record_success(proxy_id: UUID, db: AsyncSession) -> None:
    """Record a successful proxied request — bump health, clear cooldown."""
    await db.execute(
        text("""
            UPDATE proxies SET
                health_score = LEAST(health_score + :recover, 1.0),
                consecutive_failures = 0,
                cooldown_until = NULL,
                total_requests = total_requests + 1,
                updated_at = now()
            WHERE id = :pid
        """),
        {"recover": HEALTH_RECOVER, "pid": proxy_id},
    )
    logger.debug("Proxy %s: recorded success", proxy_id)


async def record_failure(
    proxy_id: UUID,
    db: AsyncSession,
    reason: Literal["http_error", "timeout", "blocked", "captcha"],
) -> None:
    """Record a failed request — decay health, compute exponential-backoff cooldown.

    Cooldown formula: ``min(COOLDOWN_BASE_S * 2^(failures-1), COOLDOWN_MAX_S)``.
    """
    # Fetch current consecutive_failures.
    result = await db.execute(
        text("SELECT consecutive_failures FROM proxies WHERE id = :pid"),
        {"pid": proxy_id},
    )
    row = result.fetchone()
    current_failures = int(row[0]) if row else 0
    new_failures = current_failures + 1

    # Exponential backoff with cap.
    cooldown_s = min(COOLDOWN_BASE_S * (2 ** (new_failures - 1)), COOLDOWN_MAX_S)
    cooldown_until = datetime.now(UTC) + timedelta(seconds=cooldown_s)

    await db.execute(
        text("""
            UPDATE proxies SET
                health_score = GREATEST(health_score - :decay, 0.0),
                consecutive_failures = :nfail,
                cooldown_until = :cd,
                total_errors = total_errors + 1,
                total_requests = total_requests + 1,
                updated_at = now()
            WHERE id = :pid
        """),
        {
            "decay": HEALTH_DECAY,
            "nfail": new_failures,
            "cd": cooldown_until,
            "pid": proxy_id,
        },
    )
    logger.info(
        "Proxy %s: recorded failure (reason=%s, failures=%d, cooldown=%ds, until=%s)",
        proxy_id,
        reason,
        new_failures,
        cooldown_s,
        cooldown_until.isoformat(),
    )


def is_on_cooldown(proxy) -> bool:
    """Return True if the proxy's ``cooldown_until`` is set and in the future."""
    if proxy.cooldown_until is None:
        return False
    return proxy.cooldown_until > datetime.now(UTC)
