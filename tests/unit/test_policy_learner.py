"""Unit tests for app.services.policy_learner.

All DB and time calls are mocked — no real DB required.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.policy_learner import learn_from_result

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_policy(**kwargs):
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


def _make_result(*, blocked=False, tier=0, vendor=None, block_reason=None):
    return SimpleNamespace(
        blocked=blocked,
        engine=["httpx", "httpx", "curl_cffi", "curl_cffi", "playwright", "camoufox", "camoufox"][
            tier
        ],
        block_reason=block_reason,
        _tier=tier,
        _vendor=vendor,
    )


# ---------------------------------------------------------------------------
# Tests: tier persisted on success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_success_at_higher_tier_persists_tier():
    """On success at tier 3, escalation_tier must be updated to 3."""
    policy = _make_policy(escalation_tier=0)
    db = AsyncMock()

    with patch("app.services.policy_learner._detect_vendor_from_result", return_value=None):
        await learn_from_result(
            policy=policy, fetch_tier=3, result=_make_result(blocked=False, tier=3), db=db
        )

    assert policy.escalation_tier == 3
    assert policy.consecutive_blocks == 0
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_success_does_not_lower_tier():
    """Success at tier 1 must not lower a policy already at tier 3."""
    policy = _make_policy(escalation_tier=3)
    db = AsyncMock()

    with patch("app.services.policy_learner._detect_vendor_from_result", return_value=None):
        await learn_from_result(
            policy=policy, fetch_tier=1, result=_make_result(blocked=False, tier=1), db=db
        )

    assert policy.escalation_tier == 3  # unchanged
    db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests: tier_locked respected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tier_locked_prevents_tier_change():
    """When tier_locked=True, escalation_tier must not be modified."""
    policy = _make_policy(escalation_tier=2, tier_locked=True)
    db = AsyncMock()

    with patch("app.services.policy_learner._detect_vendor_from_result", return_value=None):
        await learn_from_result(
            policy=policy, fetch_tier=5, result=_make_result(blocked=False, tier=5), db=db
        )

    assert policy.escalation_tier == 2  # locked — not updated
    db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests: block increments consecutive_blocks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_block_increments_consecutive_blocks():
    policy = _make_policy(escalation_tier=2, consecutive_blocks=1)
    db = AsyncMock()

    with patch("app.services.policy_learner._detect_vendor_from_result", return_value=None):
        await learn_from_result(
            policy=policy,
            fetch_tier=2,
            result=_make_result(blocked=True, tier=2, block_reason="WAF_BLOCK"),
            db=db,
        )

    assert policy.consecutive_blocks == 2
    assert policy.last_block_reason == "WAF_BLOCK"
    db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests: vendor detection persists antibot_type
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vendor_detected_persists_antibot_type():
    policy = _make_policy(antibot_type=None)
    db = AsyncMock()

    with patch("app.services.policy_learner._detect_vendor_from_result", return_value="cloudflare"):
        await learn_from_result(
            policy=policy,
            fetch_tier=0,
            result=_make_result(blocked=False, tier=0),
            db=db,
        )

    assert policy.antibot_type == "cloudflare"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_existing_antibot_type_not_overwritten_by_none():
    """If vendor detection returns None, existing antibot_type must be kept."""
    policy = _make_policy(antibot_type="akamai")
    db = AsyncMock()

    with patch("app.services.policy_learner._detect_vendor_from_result", return_value=None):
        await learn_from_result(
            policy=policy,
            fetch_tier=0,
            result=_make_result(blocked=False, tier=0),
            db=db,
        )

    assert policy.antibot_type == "akamai"  # unchanged
