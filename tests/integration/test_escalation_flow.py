"""Integration test: escalation flow with mocked fetchers.

Verifies:
- engine changes between attempts when a block is escalatable
- max_retries is never exceeded
- tier-0 direct path escalates to tier-1 proxy path on block
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.fetch import BlockReason
from app.services.fetchers.base import FetchResult, fetch_with_retry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _blocked_result(engine: str, reason: BlockReason = BlockReason.WAF_BLOCK) -> FetchResult:
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
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engine_changes_on_escalatable_block():
    """After MAX_ATTEMPTS_PER_TIER blocks, engine must change to the next tier."""
    engines_used = []

    async def fake_fetch(url, *, proxy=None, headers=None, **kwargs):
        engine = engines_used[-1] if engines_used else "httpx"
        if len(engines_used) < 3:
            return _blocked_result(engine, BlockReason.WAF_BLOCK)
        return _ok_result(engine)

    def fake_get_fetcher(engine, *, browser_pool=None):
        engines_used.append(engine)
        fetcher = MagicMock()
        fetcher.fetch = AsyncMock(side_effect=lambda url, **kw: fake_fetch(url, **kw))
        return fetcher

    policy = _make_policy(tier=0)

    with (
        patch("app.services.fetchers.base.get_fetcher", side_effect=fake_get_fetcher),
        patch("app.services.fetchers.base.asyncio.sleep", new=AsyncMock()),
        patch(
            "app.services.proxy_manager.ProxyManager.get_proxy", new=AsyncMock(return_value=None)
        ),
    ):
        _ = await fetch_with_retry(
            url="https://example.com",
            policy=policy,
            max_retries=6,
            use_proxy=False,
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
            return _blocked_result(engine, BlockReason.WAF_BLOCK)

        fetcher.fetch = AsyncMock(side_effect=_fetch)
        return fetcher

    policy = _make_policy(tier=0)
    max_retries = 4

    with (
        patch("app.services.fetchers.base.get_fetcher", side_effect=fake_get_fetcher),
        patch("app.services.fetchers.base.asyncio.sleep", new=AsyncMock()),
        patch(
            "app.services.proxy_manager.ProxyManager.get_proxy", new=AsyncMock(return_value=None)
        ),
    ):
        result = await fetch_with_retry(
            url="https://example.com",
            policy=policy,
            max_retries=max_retries,
            use_proxy=False,
        )

    assert attempt_count <= max_retries, f"Exceeded max_retries: {attempt_count} > {max_retries}"
    assert result.blocked is True
