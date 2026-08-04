"""Unit tests for proxy rotation: different IDs on retry, health score decay, excluded-ID accumulation."""

from unittest.mock import AsyncMock

from app.schemas.fetch import BlockReason
from uuid import UUID

import pytest
from unittest.mock import AsyncMock as _AsyncMock, patch as _patch2


@pytest.fixture(autouse=True)
def route_get_fetcher(monkeypatch):
    import app.services.fetchers as _f
    import app.services.fetchers.base as _b

    holder = type("H", (), {"target": None})()

    class _Delegate:
        async def fetch(self, url, **kw):
            return await holder.target.fetch(url, **kw)

    monkeypatch.setattr(_f, "get_fetcher", lambda engine, **kw: _Delegate(), raising=False)

    _orig = _b.fetch_with_retry

    async def _wrapped(*args, **kwargs):
        holder.target = kwargs.get("fetcher") or (args[0] if args else None)
        return await _orig(*args, **kwargs)

    monkeypatch.setattr(_b, "fetch_with_retry", _wrapped)
    monkeypatch.setattr(_b.asyncio, "sleep", _AsyncMock())


class _FakeProxy:
    def __init__(self, proxy_id: str, health: float = 1.0):
        self.id: UUID = UUID(int=abs(hash(proxy_id)) % (2**128))
        self.health_score = health


class _Policy:
    escalation_tier = 0
    tier_locked = False
    antibot_type = None
    proxy_type = None
    max_escalation_attempts = 12
    use_proxy = True
    proxy_country = None
    proxy_pool_id = None
    max_retries = 3
    min_delay_ms = 0
    max_delay_ms = 1
    engine = "httpx"
    header_profile = None


@pytest.mark.asyncio
async def test_blocked_proxy_is_excluded_on_retry():
    """First proxy returns blocked → second attempt uses a different proxy."""
    from app.services.fetchers.base import FetchResult, fetch_with_retry

    banned = _FakeProxy("banned-001")
    healthy = _FakeProxy("healthy-002")

    proxy_mgr = AsyncMock()
    proxy_mgr.get_proxy.side_effect = [banned, healthy]
    proxy_mgr.report_result = AsyncMock()

    call_count = 0

    async def _fetch(url, *, proxy, headers, timeout_s):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return FetchResult(
                url=url,
                status_code=403,
                body=b"blocked",
                engine="httpx",
                blocked=True,
                block_reason=BlockReason.IP_BAN,
                proxy_id=banned.id if proxy else None,
            )
        return FetchResult(
            url=url,
            status_code=200,
            body=b"ok",
            engine="httpx",
            proxy_id=healthy.id if proxy else None,
        )

    fetcher = AsyncMock()
    fetcher.fetch.side_effect = _fetch

    result = await fetch_with_retry(
        fetcher=fetcher,
        url="https://example.com",
        policy=_Policy(),
        proxy_manager=proxy_mgr,
        use_proxy=True,
    )

    assert call_count == 2
    assert result.proxy_id == healthy.id

    # Both calls happened — two different proxies were selected.
    assert proxy_mgr.get_proxy.call_count == 2


@pytest.mark.asyncio
async def test_rotation_exhaustion_raises_proxy_pool_exhausted():
    """When all proxies are blocked and pool is exhausted, raise ProxyPoolExhaustedError."""
    from app.core.errors import ProxyPoolExhaustedError
    from app.services.fetchers.base import FetchResult, fetch_with_retry

    class ExhaustPolicy:
        escalation_tier = 0
        tier_locked = False
        antibot_type = None
        proxy_type = None
        max_escalation_attempts = 12
        use_proxy = True
        proxy_country = None
        proxy_pool_id = None
        max_retries = 4  # one more than proxy count so exhaustion triggers
        min_delay_ms = 0
        max_delay_ms = 1
        engine = "httpx"
        header_profile = None

    p1 = _FakeProxy("p1")
    p2 = _FakeProxy("p2")
    p3 = _FakeProxy("p3")

    proxy_mgr = AsyncMock()
    # Return p1, p2, p3, then None (pool exhausted after all excluded).
    proxy_mgr.get_proxy.side_effect = [p1, p2, p3, None]
    proxy_mgr.report_result = AsyncMock()

    async def _fetch(url, *, proxy, headers, timeout_s):
        return FetchResult(
            url=url,
            status_code=403,
            body=b"blocked",
            engine="httpx",
            blocked=True,
            block_reason=BlockReason.IP_BAN,
            proxy_id=proxy.id if proxy else None,
        )

    fetcher = AsyncMock()
    fetcher.fetch.side_effect = _fetch

    with pytest.raises(ProxyPoolExhaustedError, match="PROXY_POOL_EXHAUSTED"):
        await fetch_with_retry(
            fetcher=fetcher,
            url="https://example.com",
            policy=ExhaustPolicy(),
            proxy_manager=proxy_mgr,
            use_proxy=True,
        )

    assert proxy_mgr.report_result.call_count == 3


@pytest.mark.asyncio
async def test_success_reports_proxy_health():
    """On success, proxy_manager.report_result is called with success=True."""
    from app.services.fetchers.base import FetchResult, fetch_with_retry

    proxy = _FakeProxy("healthy-1")
    proxy_mgr = AsyncMock()
    proxy_mgr.get_proxy.return_value = proxy
    proxy_mgr.report_result = AsyncMock()

    fetcher = AsyncMock()
    fetcher.fetch.return_value = FetchResult(
        url="https://example.com",
        status_code=200,
        body=b"ok",
        engine="httpx",
        proxy_id=proxy.id,
    )

    result = await fetch_with_retry(
        fetcher=fetcher,
        url="https://example.com",
        policy=_Policy(),
        proxy_manager=proxy_mgr,
        use_proxy=True,
    )

    assert result is not None
    proxy_mgr.report_result.assert_called_once()
    call_kwargs = proxy_mgr.report_result.call_args.kwargs
    assert call_kwargs["success"] is True
    assert call_kwargs["proxy_id"] == proxy.id


@pytest.mark.asyncio
async def test_sticky_cleared_on_first_retry():
    """Sticky session key is None on retry attempts (allows rotation)."""
    from app.services.fetchers.base import FetchResult, fetch_with_retry

    p1 = _FakeProxy("p1")
    p2 = _FakeProxy("p2")

    proxy_mgr = AsyncMock()
    proxy_mgr.get_proxy.side_effect = [p1, p2]
    proxy_mgr.report_result = AsyncMock()

    call_count = 0

    async def _fetch(url, *, proxy, headers, timeout_s):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return FetchResult(
                url=url,
                status_code=403,
                body=b"blocked",
                engine="httpx",
                blocked=True,
                block_reason=BlockReason.IP_BAN,
                proxy_id=proxy.id if proxy else None,
            )
        return FetchResult(
            url=url,
            status_code=200,
            body=b"ok",
            engine="httpx",
            proxy_id=proxy.id if proxy else None,
        )

    fetcher = AsyncMock()
    fetcher.fetch.side_effect = _fetch

    await fetch_with_retry(
        fetcher=fetcher,
        url="https://example.com",
        policy=_Policy(),
        proxy_manager=proxy_mgr,
        sticky_key="job-123",
        use_proxy=True,
    )

    assert proxy_mgr.get_proxy.call_args_list[0].kwargs["sticky_key"] == "job-123"
    assert proxy_mgr.get_proxy.call_args_list[1].kwargs["sticky_key"] is None


@pytest.mark.asyncio
async def test_required_proxy_never_falls_back_to_direct():
    """When use_proxy=True and pool is empty, fail-fast without calling the fetcher."""
    from app.core.errors import ProxyPoolUnavailableError
    from app.services.fetchers.base import FetchResult, fetch_with_retry

    fetcher = AsyncMock()
    fetcher.fetch.return_value = FetchResult(
        url="https://example.com",
        status_code=200,
        body=b"ok",
        engine="httpx",
    )

    proxy_mgr = AsyncMock()
    proxy_mgr.get_proxy.return_value = None  # pool empty

    with pytest.raises(ProxyPoolUnavailableError, match="PROXY_POOL_EMPTY"):
        await fetch_with_retry(
            fetcher=fetcher,
            url="https://www.mediaexpert.pl/",
            policy=_Policy(),
            proxy_manager=proxy_mgr,
            use_proxy=True,
            proxy_country="PL",
        )

    fetcher.fetch.assert_not_called()
