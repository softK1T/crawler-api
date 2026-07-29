"""Proxy manager: weighted selection, circuit breaker, sticky sessions.

Replaces the legacy SmartProxyPool / GeoProxyPool for all Stage 6+ code paths.
Legacy pools were removed in Stage 14.
backward compatibility with existing Celery worker tasks.
"""

import logging
import random
from uuid import UUID

from sqlalchemy import select

logger = logging.getLogger(__name__)

# Weight floor so 0.0-health proxies still get occasional traffic (canary testing).
_WEIGHT_FLOOR = 0.01


class ProxyManager:
    """Canonical proxy service — weighted picker, circuit breaker, sticky sessions.

    Instantiated once at startup and stored on ``app.state.proxy_manager``.
    """

    # Circuit breaker constants.
    CIRCUIT_BREAKER_THRESHOLD = 5
    CIRCUIT_BREAKER_TIMEOUT_S = 300

    def __init__(self, db_session_factory, redis_client) -> None:
        self._db_factory = db_session_factory
        self._redis = redis_client

    # ── Circuit breaker (Redis) ───────────────────────────────────────────────

    async def _increment_circuit_breaker(self, domain: str) -> None:
        """Increment the failure counter for *domain*.  Trip if threshold reached."""
        try:
            key = f"cb:domain:{domain}"
            count = await self._redis.incr(key)
            await self._redis.expire(key, self.CIRCUIT_BREAKER_TIMEOUT_S * 2)
            if count >= self.CIRCUIT_BREAKER_THRESHOLD:
                state_key = f"cb:domain:{domain}:state"
                await self._redis.set(state_key, "open")
                await self._redis.expire(state_key, self.CIRCUIT_BREAKER_TIMEOUT_S * 2)
                logger.warning("Circuit breaker OPEN for domain=%s (failures=%d)", domain, count)
        except Exception:
            logger.warning("Circuit breaker increment failed for domain=%s", domain, exc_info=True)

    async def _reset_circuit_breaker(self, domain: str) -> None:
        """Clear failure counter and open state for *domain*."""
        try:
            await self._redis.delete(f"cb:domain:{domain}")
            await self._redis.delete(f"cb:domain:{domain}:state")
        except Exception:
            logger.warning("Circuit breaker reset failed for domain=%s", domain, exc_info=True)

    async def _is_circuit_open(self, domain: str) -> bool:
        """Check if the circuit breaker is currently open for *domain*."""
        try:
            state = await self._redis.get(f"cb:domain:{domain}:state")
            return state == "open"
        except Exception:
            # Redis down → fail-open (bypass circuit breaker).
            return False

    # ── Sticky sessions (Redis) ────────────────────────────────────────────────

    async def _get_sticky(self, domain: str, sticky_key: str) -> UUID | None:
        """Return the sticky proxy_id for (domain, sticky_key), or None."""
        try:
            raw = await self._redis.get(f"sticky:{domain}:{sticky_key}")
            return UUID(raw) if raw else None
        except Exception:
            return None

    async def _set_sticky(self, domain: str, sticky_key: str, proxy_id: UUID, ttl: int) -> None:
        """Pin *proxy_id* for (domain, sticky_key) with *ttl* seconds."""
        try:
            await self._redis.set(f"sticky:{domain}:{sticky_key}", str(proxy_id), ex=ttl)
        except Exception:
            logger.warning("Sticky session set failed", exc_info=True)

    async def _delete_sticky(self, domain: str, sticky_key: str) -> None:
        try:
            await self._redis.delete(f"sticky:{domain}:{sticky_key}")
        except Exception:
            pass

    # ── Proxy selection ───────────────────────────────────────────────────────

    async def get_proxy(
        self,
        *,
        pool_id: UUID | None,
        domain: str,
        sticky_key: str | None,
        proxy_sticky_ttl_s: int = 1800,
    ) -> None:
        """Select a proxy by weighted health score.

        1. Check circuit breaker — return None if open for *domain*.
        2. Try sticky session — return pinned proxy if still healthy.
        3. Weighted random selection over all eligible proxies.
        4. Pin sticky session if *sticky_key* provided.
        """
        from app.models.proxy import Proxy
        from app.services.proxy_health import is_on_cooldown

        # 1. Circuit breaker.
        if await self._is_circuit_open(domain):
            logger.info("Circuit breaker open for domain=%s — no proxy assigned", domain)
            return None

        # 2. Sticky session.
        if sticky_key is not None:
            sticky_id = await self._get_sticky(domain, sticky_key)
            if sticky_id is not None:
                async with self._db_factory() as db:
                    sticky_proxy = await db.get(Proxy, sticky_id)
                    if sticky_proxy is not None and not is_on_cooldown(sticky_proxy):
                        return sticky_proxy
                # Dead proxy or on cooldown — clear sticky.
                await self._delete_sticky(domain, sticky_key)

        # 3. Load eligible proxies.
        async with self._db_factory() as db:
            stmt = select(Proxy)
            if pool_id is not None:
                stmt = stmt.where(Proxy.pool_id == pool_id)
            result = await db.execute(stmt)
            all_proxies = result.scalars().all()

            # Filter out proxies on cooldown.
            eligible = [p for p in all_proxies if not is_on_cooldown(p)]
            if not eligible:
                logger.warning("No eligible proxies for pool=%s domain=%s", pool_id, domain)
                return None

            # 4. Weighted random selection using health_score.
            # random.choices() is NOT cryptographically random — intentional.
            weights = [max(_WEIGHT_FLOOR, float(p.health_score)) for p in eligible]
            selected = random.choices(eligible, weights=weights, k=1)[0]

            # 5. Pin sticky session.
            if sticky_key is not None:
                await self._set_sticky(domain, sticky_key, selected.id, proxy_sticky_ttl_s)

            return selected

    # ── Reporting ──────────────────────────────────────────────────────────────

    async def report_result(
        self,
        *,
        proxy_id: UUID,
        domain: str,
        success: bool,
        reason: str | None,
        db,
    ) -> None:
        """Record a proxy request outcome and update circuit breaker state."""
        from app.services.proxy_health import record_failure, record_success

        if success:
            await record_success(proxy_id, db)
            await self._reset_circuit_breaker(domain)
        else:
            reason_literal = reason if reason is not None else "http_error"
            await record_failure(proxy_id, db, reason_literal)  # type: ignore[arg-type]
            await self._increment_circuit_breaker(domain)

    # ── Pool statistics ───────────────────────────────────────────────────────

    async def get_pool_stats(self, pool_id: UUID, db) -> dict:
        """Return aggregated health statistics for *pool_id*."""
        from app.models.proxy import Proxy
        from app.services.proxy_health import is_on_cooldown

        stmt = select(Proxy).where(Proxy.pool_id == pool_id)
        result = await db.execute(stmt)
        proxies = result.scalars().all()

        total = len(proxies)
        on_cooldown_count = sum(1 for p in proxies if is_on_cooldown(p))
        active_count = total - on_cooldown_count
        avg_health = (sum(float(p.health_score) for p in proxies) / total) if total > 0 else 0.0

        # Scan Redis for open circuit breakers (SCAN, not KEYS).
        open_circuits: list[str] = []
        try:
            cursor = 0
            while True:
                cursor, keys = await self._redis.scan(
                    cursor=cursor, match="cb:domain:*:state", count=100
                )
                for key in keys:
                    if await self._redis.get(key) == "open":
                        domain = key.decode().removeprefix("cb:domain:").removesuffix(":state")
                        open_circuits.append(domain)
                if cursor == 0:
                    break
        except Exception:
            logger.warning("Failed to scan circuit breaker keys", exc_info=True)

        return {
            "pool_id": str(pool_id),
            "total": total,
            "active": active_count,
            "on_cooldown": on_cooldown_count,
            "avg_health": round(avg_health, 3),
            "circuit_breakers_open": open_circuits,
        }
