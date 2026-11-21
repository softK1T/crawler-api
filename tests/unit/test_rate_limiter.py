"""Unit tests for 4-layer rate limiter — Lua semantics and concurrency."""

import asyncio
from uuid import uuid4

import pytest


@pytest.fixture
async def limiter(redis_client):
    from app.services.rate_limiter import RateLimiter

    return RateLimiter(redis_client)


async def test_key_layer_allows_within_limit(limiter):
    result = await limiter.check_key("test-prefix", limit=5)
    assert result["allowed"] is True
    assert result["layer"] == "key"


async def test_key_layer_denies_over_limit(limiter):
    for _ in range(3):
        await limiter.check_key("deny-prefix", limit=2)
    result = await limiter.check_key("deny-prefix", limit=2)
    assert result["allowed"] is False


async def test_domain_layer_429_has_retry_after(limiter):
    domain = "test-domain.example.com"
    for _ in range(4):
        await limiter.check_domain(domain, rps=3)
    result = await limiter.check_domain(domain, rps=3)
    assert result["allowed"] is False
    assert result["retry_after_s"] > 0
    assert result["layer"] == "domain"


async def test_proxy_layer_skipped_when_none(limiter):
    result = await limiter.check_all(
        api_key_prefix="test",
        application_id=__import__("uuid").uuid4(),
        domain="test.example.com",
        proxy_id=None,
        key_rpm=60,
        domain_rps=10.0,
        monthly_quota=100_000,
    )
    assert result["allowed"] is True


@pytest.mark.parametrize(
    ("layer", "key_rpm", "domain_rps"), [("key", 1, 100.0), ("domain", 60, 1.0)]
)
async def test_rejected_request_does_not_use_monthly_quota(
    limiter, redis_client, layer, key_rpm, domain_rps
):
    app_id = uuid4()

    async def request():
        return await limiter.check_all(
            api_key_prefix="quota-last",
            application_id=app_id,
            domain="quota-last.example.com",
            proxy_id=None,
            key_rpm=key_rpm,
            domain_rps=domain_rps,
            monthly_quota=100,
        )

    assert (await request())["allowed"] is True
    assert (await request())["layer"] == layer
    assert await redis_client.zcard(f"rl:app:{app_id}") == 1


async def test_redis_unavailable_fail_open():
    from app.services.rate_limiter import RateLimiter

    broken = RateLimiter(None)
    result = await broken.check_key("any", limit=1)
    assert result["allowed"] is True


@pytest.mark.slow
async def test_concurrency_exactly_limit(limiter):
    """20 concurrent calls with limit=10 → exactly 10 allowed, 10 denied."""
    limit = 10
    concurrent = 20

    async def one_call():
        return await limiter.check_key("concurrent-key", limit=limit)

    results = await asyncio.gather(*[one_call() for _ in range(concurrent)])
    allowed = sum(1 for r in results if r["allowed"])
    denied = sum(1 for r in results if not r["allowed"])
    assert allowed == limit
    assert denied == concurrent - limit
