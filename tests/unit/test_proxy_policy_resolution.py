"""Unit tests for three-level proxy policy resolution and fail-fast behaviour.

These tests exercise ``fetch_with_retry`` proxy-selection logic without
needing real Redis or a running worker.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.fetchers.base import FetchResult, _normalize_domain_from_url


@pytest.fixture(autouse=True)
def _auto_route_get_fetcher(route_get_fetcher):
    """Force every test in this module through the conftest get_fetcher router."""
    yield


def test_normalize_domain_strips_www():
    assert _normalize_domain_from_url("https://www.example.com/page") == "example.com"


def test_normalize_domain_handles_no_subdomain():
    assert _normalize_domain_from_url("https://example.com/page") == "example.com"


# ── Three-level use_proxy resolution ──────────────────────────────────────────


class FakePolicy:
    """Minimal stand-in for a DomainPolicy row."""

    def __init__(self, *, use_proxy=False, proxy_country=None, proxy_pool_id=None):
        self.use_proxy = use_proxy
        self.proxy_country = proxy_country
        self.proxy_pool_id = proxy_pool_id
        self.max_retries = 3
        self.min_delay_ms = 0
        self.max_delay_ms = 1
        self.engine = "httpx"
        self.header_profile = None
        self.escalation_tier = 0
        self.tier_locked = False
        self.antibot_type = None
        self.proxy_type = None
        self.max_escalation_attempts = 12
        self.domain = "example.com"


@pytest.mark.asyncio
async def test_request_override_true_wins_over_policy_false():
    """Request use_proxy=True must override policy use_proxy=False."""
    policy = FakePolicy(use_proxy=False)
    fetcher = AsyncMock()
    fetcher.fetch.return_value = FetchResult(
        url="https://example.com",
        status_code=200,
        body=b"ok",
        engine="httpx",
    )
    proxy_mgr = AsyncMock()
    proxy_mgr.get_proxy.return_value = MagicMock(id="proxy-1")

    from app.services.fetchers.base import fetch_with_retry

    result = await fetch_with_retry(
        fetcher=fetcher,
        url="https://example.com",
        policy=policy,
        proxy_manager=proxy_mgr,
        use_proxy=True,  # explicit override
    )
    assert result is not None
    proxy_mgr.get_proxy.assert_called_once()
    # Verify exclude_ids and country were passed.
    call_kwargs = proxy_mgr.get_proxy.call_args.kwargs
    assert call_kwargs["exclude_ids"] == set()
    assert call_kwargs["country"] is None


@pytest.mark.asyncio
async def test_request_override_false_suppresses_proxy():
    """Request use_proxy=False must suppress proxy even when policy says True."""
    policy = FakePolicy(use_proxy=True)
    fetcher = AsyncMock()
    fetcher.fetch.return_value = FetchResult(
        url="https://example.com",
        status_code=200,
        body=b"ok",
        engine="httpx",
    )
    proxy_mgr = AsyncMock()

    from app.services.fetchers.base import fetch_with_retry

    result = await fetch_with_retry(
        fetcher=fetcher,
        url="https://example.com",
        policy=policy,
        proxy_manager=proxy_mgr,
        use_proxy=False,  # explicit suppress
    )
    assert result is not None
    proxy_mgr.get_proxy.assert_not_called()


@pytest.mark.asyncio
async def test_policy_true_uses_proxy_when_request_none():
    """When request use_proxy is None, fall back to policy.use_proxy=True."""
    policy = FakePolicy(use_proxy=True)
    fetcher = AsyncMock()
    fetcher.fetch.return_value = FetchResult(
        url="https://example.com",
        status_code=200,
        body=b"ok",
        engine="httpx",
    )
    proxy_mgr = AsyncMock()
    proxy_mgr.get_proxy.return_value = MagicMock(id="proxy-1")

    from app.services.fetchers.base import fetch_with_retry

    result = await fetch_with_retry(
        fetcher=fetcher,
        url="https://example.com",
        policy=policy,
        proxy_manager=proxy_mgr,
        use_proxy=None,  # not specified
    )
    assert result is not None
    proxy_mgr.get_proxy.assert_called_once()


@pytest.mark.asyncio
async def test_default_no_policy_no_request_means_no_proxy():
    """No policy and no request override → use_proxy defaults to False."""
    fetcher = AsyncMock()
    fetcher.fetch.return_value = FetchResult(
        url="https://example.com",
        status_code=200,
        body=b"ok",
        engine="httpx",
    )
    proxy_mgr = AsyncMock()

    from app.services.fetchers.base import fetch_with_retry

    result = await fetch_with_retry(
        fetcher=fetcher,
        url="https://example.com",
        policy=None,
        proxy_manager=proxy_mgr,
        use_proxy=None,
    )
    assert result is not None
    proxy_mgr.get_proxy.assert_not_called()


# ── Fail-fast: ProxyPoolUnavailableError ──────────────────────────────────────


@pytest.mark.asyncio
async def test_fail_fast_when_proxy_requested_but_none_available():
    """When use_proxy=True and get_proxy returns None, raise immediately."""
    from app.core.errors import ProxyPoolUnavailableError

    policy = FakePolicy(use_proxy=True)
    fetcher = AsyncMock()
    proxy_mgr = AsyncMock()
    proxy_mgr.get_proxy.return_value = None  # pool empty

    from app.services.fetchers.base import fetch_with_retry

    with pytest.raises(ProxyPoolUnavailableError, match="PROXY_POOL_EMPTY"):
        await fetch_with_retry(
            fetcher=fetcher,
            url="https://example.com",
            policy=policy,
            proxy_manager=proxy_mgr,
            use_proxy=True,
        )


# ── Country override ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_request_country_overrides_policy_country():
    """Request proxy_country=PL must override policy proxy_country=DE."""
    policy = FakePolicy(use_proxy=True, proxy_country="DE")
    fetcher = AsyncMock()
    fetcher.fetch.return_value = FetchResult(
        url="https://example.com",
        status_code=200,
        body=b"ok",
        engine="httpx",
    )
    proxy_mgr = AsyncMock()
    proxy_mgr.get_proxy.return_value = MagicMock(id="proxy-pl")

    from app.services.fetchers.base import fetch_with_retry

    await fetch_with_retry(
        fetcher=fetcher,
        url="https://example.com",
        policy=policy,
        proxy_manager=proxy_mgr,
        use_proxy=True,
        proxy_country="PL",  # request override
    )
    call_kwargs = proxy_mgr.get_proxy.call_args.kwargs
    assert call_kwargs["country"] == "PL"


@pytest.mark.asyncio
async def test_policy_country_used_when_request_none():
    """When request proxy_country is None, fall back to policy.proxy_country."""
    policy = FakePolicy(use_proxy=True, proxy_country="DE")
    fetcher = AsyncMock()
    fetcher.fetch.return_value = FetchResult(
        url="https://example.com",
        status_code=200,
        body=b"ok",
        engine="httpx",
    )
    proxy_mgr = AsyncMock()
    proxy_mgr.get_proxy.return_value = MagicMock(id="proxy-de")

    from app.services.fetchers.base import fetch_with_retry

    await fetch_with_retry(
        fetcher=fetcher,
        url="https://example.com",
        policy=policy,
        proxy_manager=proxy_mgr,
        use_proxy=True,
        proxy_country=None,
    )
    call_kwargs = proxy_mgr.get_proxy.call_args.kwargs
    assert call_kwargs["country"] == "DE"


# ── Truthiness + country normalization ────────────────────────────────────────


@pytest.mark.asyncio
async def test_explicit_false_overrides_true_policy():
    """Explicit use_proxy=False must override policy.use_proxy=True (no truthiness trap)."""
    policy = FakePolicy(use_proxy=True, proxy_country="PL")
    fetcher = AsyncMock()
    fetcher.fetch.return_value = FetchResult(
        url="https://example.com",
        status_code=200,
        body=b"ok",
        engine="httpx",
    )
    proxy_mgr = AsyncMock()

    from app.services.fetchers.base import fetch_with_retry

    result = await fetch_with_retry(
        fetcher=fetcher,
        url="https://example.com",
        policy=policy,
        proxy_manager=proxy_mgr,
        use_proxy=False,
        proxy_country=None,
    )
    assert result is not None
    proxy_mgr.get_proxy.assert_not_called()


@pytest.mark.asyncio
async def test_explicit_true_overrides_false_policy():
    """Explicit use_proxy=True + lowercase country normalized to upper."""
    policy = FakePolicy(use_proxy=False, proxy_country=None)
    fetcher = AsyncMock()
    fetcher.fetch.return_value = FetchResult(
        url="https://example.com",
        status_code=200,
        body=b"ok",
        engine="httpx",
    )
    proxy_mgr = AsyncMock()
    proxy_mgr.get_proxy.return_value = MagicMock(id="proxy-pl")

    from app.services.fetchers.base import fetch_with_retry

    result = await fetch_with_retry(
        fetcher=fetcher,
        url="https://example.com",
        policy=policy,
        proxy_manager=proxy_mgr,
        use_proxy=True,
        proxy_country="pl",  # lowercase
    )
    assert result is not None
    call_kwargs = proxy_mgr.get_proxy.call_args.kwargs
    assert call_kwargs["country"] == "PL"
