"""Policy learner — writes escalation outcomes back to DomainPolicy.

Called after every fetch_with_retry attempt in fetch_task.  Fully async,
no side-effects on FetchResult.  Uses optimistic UPDATE with SELECT refresh
so concurrent workers converge correctly without advisory locks.

Design: see docs/decisions/ADR-019-escalation-ladder.md §Learning state.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.domain_policy import DomainPolicy
    from app.services.fetchers.base import FetchResult

logger = logging.getLogger(__name__)

# How many consecutive successes at a tier before we try de-escalating.
# Not implemented in Phase 5 — reserved for Phase 6 probe logic.
_DE_ESCALATE_AFTER_SUCCESSES = 20

# Minimum tier that can be de-escalated to (never below 0).
_MIN_TIER = 0


async def record_outcome(
    *,
    result: FetchResult,
    policy: DomainPolicy | None,
    db: AsyncSession,
    engine_used: str,
    tier_used: int,
) -> None:
    """Persist fetch outcome into DomainPolicy.

    Called once per fetch_task regardless of success/failure.
    Skips when policy is None (domain has no DomainPolicy row).

    On success
    ----------
    - escalation_tier   = min(tier_used, current) — never ratchet up on success
    - last_success_at   = now
    - consecutive_blocks = 0

    On block
    --------
    - escalation_tier    = max(tier_used, current) — ratchet up, never down
    - last_block_reason  = result.block_reason
    - consecutive_blocks += 1

    Vendor detection
    ----------------
    detect_vendor() runs on every response (200s included) to update
    antibot_type in the background.  Only overwrites if a vendor is detected
    (None does not overwrite a previously learned value).

    tier_locked
    -----------
    When tier_locked=True the learner updates last_success_at /
    last_block_reason / consecutive_blocks for observability but never
    changes escalation_tier.
    """
    if policy is None:
        return

    from app.services.block_detector import detect_vendor

    cookies: dict[str, str] = {}  # FetchResult has no cookie jar — use empty dict
    vendor = detect_vendor(
        status_code=result.status_code,
        headers=result.headers,
        cookies=cookies,
        body=result.body[:65_536],
    )

    try:
        # Re-fetch inside the same session to get a live row for UPDATE.
        # Using db.get() would return a stale cached instance.
        from app.models.domain_policy import DomainPolicy as _DP

        stmt = select(_DP).where(_DP.id == policy.id).with_for_update(skip_locked=True)
        row = (await db.execute(stmt)).scalar_one_or_none()
        if row is None:
            # Another worker deleted the policy — skip silently.
            return

        now = datetime.now(UTC)

        # ── Vendor detection ─────────────────────────────────────────────────
        if vendor is not None and row.antibot_type != vendor:
            logger.info(
                "policy_learner.vendor_detected",
                extra={"domain": row.domain, "vendor": vendor, "previous": row.antibot_type},
            )
            row.antibot_type = vendor

        # ── Tier learning ────────────────────────────────────────────────────
        if not row.tier_locked:
            if result.blocked:
                new_tier = max(tier_used, row.escalation_tier)
                if new_tier != row.escalation_tier:
                    logger.info(
                        "policy_learner.tier_ratchet_up",
                        extra={
                            "domain": row.domain,
                            "from": row.escalation_tier,
                            "to": new_tier,
                            "reason": result.block_reason,
                        },
                    )
                row.escalation_tier = new_tier
            else:
                # On success: don't ratchet up (tier_used <= current is a success
                # at a lower tier — lower is better/cheaper).
                new_tier = min(tier_used, row.escalation_tier)
                if new_tier != row.escalation_tier:
                    logger.info(
                        "policy_learner.tier_ratchet_down",
                        extra={
                            "domain": row.domain,
                            "from": row.escalation_tier,
                            "to": new_tier,
                        },
                    )
                row.escalation_tier = new_tier

        # ── Success / block counters ─────────────────────────────────────────
        if result.blocked:
            row.last_block_reason = result.block_reason
            row.consecutive_blocks = (row.consecutive_blocks or 0) + 1
            if row.consecutive_blocks >= 5:
                logger.warning(
                    "policy_learner.consecutive_blocks_high",
                    extra={
                        "domain": row.domain,
                        "consecutive_blocks": row.consecutive_blocks,
                        "tier": row.escalation_tier,
                        "reason": result.block_reason,
                        "action": "manual_review_recommended",
                    },
                )
        else:
            row.last_success_at = now
            row.consecutive_blocks = 0

        await db.commit()

        try:
            from app.core.observability import (
                CONSECUTIVE_BLOCKS_GAUGE,
                ESCALATION_TIER_CURRENT,
                VENDOR_DETECTED_TOTAL,
            )

            ESCALATION_TIER_CURRENT.labels(domain=row.domain).set(row.escalation_tier)
            CONSECUTIVE_BLOCKS_GAUGE.labels(domain=row.domain).set(row.consecutive_blocks)
            if vendor is not None:
                VENDOR_DETECTED_TOTAL.labels(vendor=vendor).inc()
        except Exception:  # noqa: S110
            pass

        logger.debug(
            "policy_learner.outcome_recorded",
            extra={
                "domain": row.domain,
                "blocked": result.blocked,
                "tier": row.escalation_tier,
                "vendor": row.antibot_type,
                "consecutive_blocks": row.consecutive_blocks,
            },
        )

    except Exception:
        logger.exception("policy_learner.record_outcome_failed", extra={"domain": policy.domain})
        await db.rollback()
