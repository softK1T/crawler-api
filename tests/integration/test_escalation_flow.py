"""Integration test: escalation flow with mocked fetchers.

Verifies:
- engine changes between attempts when a block is escalatable
- max_retries is never exceeded
- tier-0 direct path escalates to tier-1 proxy path on block
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.schemas.fetch import BlockReason
from app.services.fetchers.base import FetchResult, fetch_with_retry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _blocked_result(engine: str, reason: BlockReason = BlockReason.WAF) -> FetchResult:
    return FetchResult(
        url="https://example.com",
        status_code=403,
        headers={},
        body=b"blocked",
        encoding="utf-8",
        elapsed_ms=100,
        proxy_id=None,
        engine=engine,
        blocked=True,
        block_reason=reason,
        retries_used=0,
        raw_body=b"blocked",
        raw_headers={},
    )


def _ok_result(engine: str) -> FetchResult:
    return FetchResult(
        url="https://example.com",
        status_code=200,
        headers={},
        body=b"<html>ok</html>",
        encoding="utf-8",
        elapsed_ms=200,
        proxy_id=None,
        engine=engine,
        blocked=False,
        block_reason=None,
        retries_used=0,
        raw_body=b"<html>ok</html>",
        raw_headers={},
    )


def _make_policy(tier=0):
    return SimpleNamespace(
        domain="example.com",
        escalation_tier=tier,
        tier_locked=False,
        antibot_type=None,
        proxy_type=None,
        max_escalation_attempts=12,
        max_retries=6,
        min_delay_ms=0,
        max_delay_ms=1,
        engine="httpx",
        header_profile=None,
        use_proxy=False,
        proxy_country=None,
        proxy_pool_id=None,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_changes_on_escalatable_block():
    """After MAX_ATTEMPTS_PER_TIER blocks, engine must change to the next tier."""
    engines_used = []

    fetch_calls = []

    async def fake_fetch(url, *, proxy=None, headers=None, **kwargs):
        engine = engines_used[-1] if engines_used else "httpx"
        fetch_calls.append(engine)
        if len(fetch_calls) < 6:
            return _blocked_result(engine, BlockReason.WAF)
        return _ok_result(engine)

    def fake_get_fetcher(engine, *, browser_pool=None):
        engines_used.append(engine)
        fetcher = MagicMock()
        fetcher.fetch = AsyncMock(side_effect=fake_fetch)
        return fetcher

    policy = _make_policy(tier=0)

    with (
        patch("app.services.fetchers.get_fetcher", side_effect=fake_get_fetcher),
        patch("app.services.fetchers.base.asyncio.sleep", new=AsyncMock()),
        patch(
            "app.services.proxy_manager.ProxyManager.get_proxy", new=AsyncMock(return_value=None)
        ),
    ):
        initial_fetcher = fake_get_fetcher(policy.engine)
        _ = await fetch_with_retry(
            initial_fetcher,
            url="https://example.com",
            policy=policy,
        )

    # At least two different engines must have been tried
    assert len(set(engines_used)) >= 2, f"Only one engine used: {engines_used}"


@pytest.mark.asyncio
async def test_max_retries_never_exceeded():
    """Total attempts must never exceed max_retries regardless of escalation."""
    attempt_count = 0

    def fake_get_fetcher(engine, *, browser_pool=None):
        nonlocal attempt_count
        fetcher = MagicMock()

        async def _fetch(url, **kwargs):
            nonlocal attempt_count
            attempt_count += 1
            return _blocked_result(engine, BlockReason.WAF)

        fetcher.fetch = AsyncMock(side_effect=_fetch)
        return fetcher

    policy = _make_policy(tier=0)
    max_retries = 4

    with (
        patch("app.services.fetchers.get_fetcher", side_effect=fake_get_fetcher),
        patch("app.services.fetchers.base.asyncio.sleep", new=AsyncMock()),
        patch(
            "app.services.proxy_manager.ProxyManager.get_proxy", new=AsyncMock(return_value=None)
        ),
    ):
        policy.max_escalation_attempts = max_retries
        initial_fetcher = fake_get_fetcher(policy.engine)
        result = await fetch_with_retry(
            initial_fetcher,
            url="https://example.com",
            policy=policy,
            use_proxy=False,
        )

    assert attempt_count <= max_retries, f"Exceeded max_retries: {attempt_count} > {max_retries}"
    assert result.blocked is True


@pytest.mark.asyncio
async def test_policy_max_retries_controls_per_tier_attempts():
    """policy.max_retries is the per-tier attempt cap before the tier is bumped.

    Regression guard: max_retries was documented as the per-tier cap but was
    never read, so the hardcoded MAX_ATTEMPTS_PER_TIER always won.
    """
    engines_used = []
    fetch_calls = []

    async def fake_fetch(url, *, proxy=None, headers=None, **kwargs):
        engine = engines_used[-1] if engines_used else "httpx"
        fetch_calls.append(engine)
        return _blocked_result(engine, BlockReason.WAF)

    def fake_get_fetcher(engine, *, browser_pool=None):
        engines_used.append(engine)
        fetcher = MagicMock()
        fetcher.fetch = AsyncMock(side_effect=fake_fetch)
        return fetcher

    policy = _make_policy(tier=0)
    policy.max_retries = 1  # bump the tier after a single block
    policy.max_escalation_attempts = 3

    # A real proxy_manager is required: without one, proxy is always None and
    # the tier-0 "direct blocked" branch bumps the tier unconditionally,
    # bypassing the attempts_at_tier / max_retries comparison entirely.
    proxy_mgr = AsyncMock()
    proxy_mgr.get_proxy = AsyncMock(return_value=SimpleNamespace(id=uuid4(), url=None))
    proxy_mgr.report_result = AsyncMock()

    with (
        patch("app.services.fetchers.get_fetcher", side_effect=fake_get_fetcher),
        patch("app.services.fetchers.base.asyncio.sleep", new=AsyncMock()),
    ):
        _ = await fetch_with_retry(
            fake_get_fetcher(policy.engine),
            url="https://example.com",
            policy=policy,
            proxy_manager=proxy_mgr,
            use_proxy=True,
        )

    # max_retries=1 means every attempt bumps the tier, so the ladder is walked
    # once per attempt rather than twice (the MAX_ATTEMPTS_PER_TIER default).
    assert len(fetch_calls) == 3, f"Expected 3 attempts, got {len(fetch_calls)}"
    assert len(set(engines_used)) >= 2, f"Tier never bumped: {engines_used}"
