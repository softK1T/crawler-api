"""Unit tests for app/services/escalation.py — pure logic, no I/O."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.schemas.fetch import BlockReason
from app.services.escalation import (
    ANTIBOT_FLOOR,
    ESCALATABLE,
    LADDER,
    effective_max_tier,
    initial_tier,
    is_escalatable,
    next_tier,
    tier_for,
)


def test_ladder_tier0_is_direct():
    assert LADDER[0].use_proxy is False


def test_ladder_tiers_1plus_use_proxy():
    for t in LADDER[1:]:
        assert t.use_proxy is True


def test_ladder_engines_valid():
    valid = {"httpx", "curl_cffi", "playwright", "camoufox"}
    for tier in LADDER:
        assert tier.engine in valid


def test_ladder_premium_tiers_count():
    premium = [t for t in LADDER if t.proxy_type in {"residential", "mobile"}]
    assert len(premium) >= 3


def test_effective_max_tier_no_premium():
    max_t = effective_max_tier(enable_premium=False)
    for i in range(max_t + 1):
        assert LADDER[i].proxy_type not in {"residential", "mobile"}


def test_effective_max_tier_with_premium():
    assert effective_max_tier(enable_premium=True) == len(LADDER) - 1


def test_initial_tier_none_policy():
    assert initial_tier(None) == 0


def test_initial_tier_respects_learned_tier():
    policy = MagicMock()
    policy.escalation_tier = 2
    policy.antibot_type = None
    assert initial_tier(policy) == 2


def test_initial_tier_respects_vendor_floor():
    policy = MagicMock()
    policy.escalation_tier = 0
    policy.antibot_type = "kasada"
    assert initial_tier(policy) == ANTIBOT_FLOOR["kasada"]


def test_initial_tier_takes_max():
    policy = MagicMock()
    policy.escalation_tier = 5
    policy.antibot_type = "kasada"
    assert initial_tier(policy) == 5


def test_initial_tier_floor_wins():
    policy = MagicMock()
    policy.escalation_tier = 1
    policy.antibot_type = "cloudflare"
    assert initial_tier(policy) == ANTIBOT_FLOOR["cloudflare"]


def test_next_tier_increments():
    assert next_tier(0) == 1
    assert next_tier(2) == 3


def test_next_tier_at_top_returns_none():
    assert next_tier(len(LADDER) - 1) is None


@pytest.mark.parametrize("reason", ["cloudflare", "waf", "captcha"])
def test_escalatable_reasons(reason: str):
    assert is_escalatable(reason) is True


@pytest.mark.parametrize("reason", ["ip_ban", "rate_limited", "other"])
def test_non_escalatable_reasons(reason: str):
    assert is_escalatable(reason) is False


def test_is_escalatable_none():
    assert is_escalatable(None) is False


def test_unknown_reason_escalates_conservatively():
    assert is_escalatable("some_future_vendor_xyz") is True


def test_tier_for_none_policy_returns_tier0():
    t = tier_for(None, enable_premium=False)
    assert t == LADDER[0]


def test_tier_for_clamps_without_premium():
    policy = MagicMock()
    policy.escalation_tier = 6
    policy.antibot_type = None
    max_free = effective_max_tier(enable_premium=False)
    t = tier_for(policy, enable_premium=False)
    assert t == LADDER[max_free]


def test_tier_for_allows_premium():
    policy = MagicMock()
    policy.escalation_tier = 6
    policy.antibot_type = None
    t = tier_for(policy, enable_premium=True)
    assert t == LADDER[6]


def test_all_vendor_floors_valid():
    for vendor, floor in ANTIBOT_FLOOR.items():
        assert 0 <= floor < len(LADDER), f"{vendor}={floor} out of range"


def test_cloudflare_floor_skips_httpx():
    assert ANTIBOT_FLOOR["cloudflare"] >= 2


def test_kasada_floor_requires_browser():
    floor = ANTIBOT_FLOOR["kasada"]
    assert LADDER[floor].engine in {"playwright", "camoufox"}


def test_escalatable_contains_vendor_reasons():
    assert BlockReason.CLOUDFLARE in ESCALATABLE
    assert BlockReason.WAF in ESCALATABLE
    assert BlockReason.CAPTCHA in ESCALATABLE


def test_escalatable_excludes_ip_and_rate():
    assert BlockReason.IP_BAN not in ESCALATABLE
    assert BlockReason.RATE_LIMITED not in ESCALATABLE
