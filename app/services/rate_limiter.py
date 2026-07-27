"""4-layer sliding-window rate limiter using Redis Lua scripts for atomicity.

Layers (checked in order, returns on first denial):
    L1 — per-API key       (window=60s)     per-key RPS
    L2 — per-application   (window=30 days)  monthly quota
    L3 — per-domain        (window=1s)      global domain politeness
    L4 — per-proxy         (window=1s)      per-proxy cooldown

Each layer uses the same sliding-window Lua script.  Redis down → fail-open
(allowed=True) with a warning log — never reject a request because the
rate-limiter is unavailable.
"""

import asyncio
import logging
import time
from typing import TypedDict
from uuid import UUID

from app.services.policy_resolver import normalize_domain

logger = logging.getLogger(__name__)

# ── Lua sliding-window script ────────────────────────────────────────────────
# KEYS[1] — sorted-set key
# ARGV[1] — now (epoch ms)
# ARGV[2] — window (seconds)
# ARGV[3] — limit (max members in window)
# ARGV[4] — cost (default 1)
# Returns: {allowed (0|1), current_count, reset_at_ms}
#
# Each member is a unique string (now + random suffix) to avoid collisions
# when two requests land at the same millisecond.

LUA_SLIDING_WINDOW = r"""
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])
local cutoff = now - window * 1000

redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff)
local count = redis.call('ZCARD', key)

if count + cost > limit then
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local reset_at = 0
    if #oldest > 0 then
        reset_at = tonumber(oldest[2]) + window * 1000
    end
    return {0, count, reset_at}
end

for i = 1, cost do
    redis.call('ZADD', key, now, now .. '-' .. i .. '-' .. math.random(1000000))
end
redis.call('EXPIRE', key, window + 1)
return {1, count + cost, now + window * 1000}
"""


class RateLimitResult(TypedDict):
    allowed: bool
    limit: int
    remaining: int
    reset_at_ms: int
    layer: str
    retry_after_s: float


_PERMISSIVE_RESULT: RateLimitResult = {
    "allowed": True,
    "limit": 0,
    "remaining": 0,
    "reset_at_ms": 0,
    "layer": "none",
    "retry_after_s": 0.0,
}


def _denied(limit: int, count: int, reset_at_ms: int, layer: str) -> RateLimitResult:
    now_ms = time.time() * 1000
    wait_s = max(0.1, (reset_at_ms - now_ms) / 1000.0)
    return RateLimitResult(
        allowed=False,
        limit=limit,
        remaining=max(0, limit - count),
        reset_at_ms=reset_at_ms,
        layer=layer,
        retry_after_s=wait_s,
    )


def _allowed(limit: int, count: int, reset_at_ms: int, layer: str) -> RateLimitResult:
    return RateLimitResult(
        allowed=True,
        limit=limit,
        remaining=max(0, limit - count),
        reset_at_ms=reset_at_ms,
        layer=layer,
        retry_after_s=0.0,
    )


async def _eval_lua(redis_client, key: str, window_s: float, limit: int, cost: int = 1):
    """Execute the sliding-window Lua script.  Returns ``(allowed, count, reset_at_ms)``."""
    now_ms = int(time.time() * 1000)
    args = [now_ms, int(window_s), limit, cost]
    result = await redis_client.eval(LUA_SLIDING_WINDOW, 1, key, *args)
    return int(result[0]), int(result[1]), int(result[2])


class RateLimiter:
    """4-layer sliding-window rate limiter.

    Instantiated once at startup and stored on ``app.state.rate_limiter``.
    Endpoints access it via ``request.app.state.rate_limiter``.
    """

    def __init__(self, redis_client) -> None:
        self._redis = redis_client

    async def _check(
        self, key: str, window_s: float, limit: int, layer: str, cost: int = 1
    ) -> RateLimitResult:
        try:
            allowed_int, count, reset_at = await _eval_lua(self._redis, key, window_s, limit, cost)
            if allowed_int == 0:
                return _denied(limit, count, reset_at, layer)
            return _allowed(limit, count, reset_at, layer)
        except Exception:
            logger.warning(
                "rate_limiter Redis error on layer=%s key=%s — failing open",
                layer,
                key,
                exc_info=True,
            )
            return _PERMISSIVE_RESULT

    # ── Per-layer helpers ────────────────────────────────────────────────────

    async def check_key(self, api_key_prefix: str, limit: int) -> RateLimitResult:
        return await self._check(
            key=f"rl:key:{api_key_prefix}", window_s=60, limit=limit, layer="key"
        )

    async def check_application(self, application_id: UUID, monthly_quota: int) -> RateLimitResult:
        return await self._check(
            key=f"rl:app:{application_id}",
            window_s=30 * 24 * 3600,  # 30-day rolling window
            limit=monthly_quota,
            layer="app",
        )

    async def check_domain(self, domain: str, rps: float) -> RateLimitResult:
        domain_norm = normalize_domain(domain)
        return await self._check(
            key=f"rl:dom:{domain_norm}", window_s=1, limit=max(1, int(rps)), layer="domain"
        )

    async def check_proxy(self, proxy_id: UUID, rps: float = 2.0) -> RateLimitResult:
        return await self._check(
            key=f"rl:proxy:{proxy_id}",
            window_s=1,
            limit=max(1, int(rps)),
            layer="proxy",
        )

    async def check_all(
        self,
        *,
        api_key_prefix: str,
        application_id: UUID,
        domain: str,
        proxy_id: UUID | None,
        domain_rps: float,
        monthly_quota: int,
    ) -> RateLimitResult:
        """Run L1→L2→L3→L4 in order.  Return the first layer that denies.

        If all layers pass, return the L3 (domain) result for X-RateLimit headers.
        *proxy_id* is optional — if ``None``, L4 is skipped.
        """
        layers = [
            self.check_key(api_key_prefix, limit=60),  # per-key RPM from config
            self.check_application(application_id, monthly_quota),
        ]
        results = await asyncio.gather(*layers)

        for result in results:
            if not result["allowed"]:
                return result

        # L3: domain (charged inline)
        dom_result = await self.check_domain(domain, domain_rps)
        if not dom_result["allowed"]:
            return dom_result

        # L4: proxy (optional)
        if proxy_id is not None:
            proxy_result = await self.check_proxy(proxy_id)
            if not proxy_result["allowed"]:
                return proxy_result

        return dom_result
