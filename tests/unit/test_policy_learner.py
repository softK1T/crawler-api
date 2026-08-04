"""Unit tests for app.services.policy_learner.record_outcome.

All DB and time calls are mocked — no real DB required.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.policy_learner import record_outcome

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(**kwargs):
    """Simulate a live DomainPolicy ORM row returned by db.execute()."""
    defaults = {
        "id": "uuid-1",
        "domain": "example.com",
        "escalation_tier": 0,
        "tier_locked": False,
        "antibot_type": None,
        "consecutive_blocks": 0,
        "last_block_reason": None,
        "last_success_at": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_result(*, blocked=False, status_code=200, block_reason=None):
    return SimpleNamespace(
        blocked=blocked,
        status_code=status_code,
        headers={},
        body=b"<html>ok</html>",
        block_reason=block_reason,
        id="uuid-1",
    )


def _make_db(row):
    """Mock AsyncSession that returns *row* from execute()."""
    scalar_result = MagicMock()
    scalar_result.scalar_one_or_none.return_value = row
    db = AsyncMock()
    db.execute = AsyncMock(return_value=scalar_result)
    return db


# ---------------------------------------------------------------------------
# Tests: tier persisted on success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_success_keeps_tier_at_same_level():
    """Success at tier 3 when already at 3 — tier stays 3."""
    row = _make_row(escalation_tier=3)
    db = _make_db(row)

    with patch("app.services.block_detector.detect_vendor", return_value=None):
        await record_outcome(
            result=_make_result(blocked=False),
            policy=row,
            db=db,
            engine_used="curl_cffi",
            tier_used=3,
        )

    assert row.escalation_tier == 3
    assert row.consecutive_blocks == 0
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_success_at_lower_tier_de_escalates_one_step():
    """Success at tier 1 when policy is at tier 3 steps down to 2, not to 1.

    Regression guard: an immediate collapse to tier_used meant one lucky fetch
    reset a Cloudflare-protected domain to the bottom of the ladder.
    """
    row = _make_row(escalation_tier=3)
    db = _make_db(row)

    with patch("app.services.block_detector.detect_vendor", return_value=None):
        await record_outcome(
            result=_make_result(blocked=False),
            policy=row,
            db=db,
            engine_used="httpx",
            tier_used=1,
        )

    assert row.escalation_tier == 2  # max(1, 3-1) = 2 — one step down only
    db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests: tier_locked respected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tier_locked_prevents_tier_change():
    """When tier_locked=True, escalation_tier must not be modified."""
    row = _make_row(escalation_tier=2, tier_locked=True)
    db = _make_db(row)

    with patch("app.services.block_detector.detect_vendor", return_value=None):
        await record_outcome(
            result=_make_result(blocked=False),
            policy=row,
            db=db,
            engine_used="camoufox",
            tier_used=5,
        )

    assert row.escalation_tier == 2  # locked — not updated
    db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests: block increments consecutive_blocks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_block_increments_consecutive_blocks():
    row = _make_row(escalation_tier=2, consecutive_blocks=1)
    db = _make_db(row)

    with patch("app.services.block_detector.detect_vendor", return_value=None):
        await record_outcome(
            result=_make_result(blocked=True, status_code=403, block_reason="WAF_BLOCK"),
            policy=row,
            db=db,
            engine_used="curl_cffi",
            tier_used=2,
        )

    assert row.consecutive_blocks == 2
    assert row.last_block_reason == "WAF_BLOCK"
    db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests: vendor detection persists antibot_type
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vendor_detected_persists_antibot_type():
    row = _make_row(antibot_type=None)
    db = _make_db(row)

    with patch("app.services.block_detector.detect_vendor", return_value="cloudflare"):
        await record_outcome(
            result=_make_result(blocked=False),
            policy=row,
            db=db,
            engine_used="httpx",
            tier_used=0,
        )

    assert row.antibot_type == "cloudflare"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_existing_antibot_type_not_overwritten_by_none():
    """If vendor detection returns None, existing antibot_type must be kept."""
    row = _make_row(antibot_type="akamai")
    db = _make_db(row)

    with patch("app.services.block_detector.detect_vendor", return_value=None):
        await record_outcome(
            result=_make_result(blocked=False),
            policy=row,
            db=db,
            engine_used="httpx",
            tier_used=0,
        )

    assert row.antibot_type == "akamai"  # unchanged
    db.commit.assert_awaited_once()
