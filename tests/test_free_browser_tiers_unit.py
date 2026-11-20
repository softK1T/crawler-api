"""Offline tests for browser tiers on free proxies and per-domain premium access."""

import asyncio
import os
import unittest
import uuid
from types import SimpleNamespace
from typing import Any
from unittest import mock

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/testdb")


class _Recorder:
    """Stands in for get_fetcher and the proxy manager; every response is a block."""

    def __init__(self, reasons: list[str] | None = None) -> None:
        self.reasons = reasons or []
        self.engines: list[str] = []
        self.proxy_types: list[str | None] = []
        self.excluded: list[set[uuid.UUID]] = []
        self.proxies: list[uuid.UUID] = []
        self.report_result = mock.AsyncMock()

    def get_fetcher(self, engine: str, **kwargs: Any) -> Any:
        from app.services.fetchers.base import FetchResult

        async def fetch(url: str, **kw: Any) -> FetchResult:
            self.engines.append(engine)
            index = len(self.engines) - 1
            reason = self.reasons[index] if index < len(self.reasons) else "cloudflare"
            return FetchResult(
                url=url, status_code=403, engine=engine, blocked=True, block_reason=reason
            )

        return SimpleNamespace(_engine_name=engine, fetch=fetch)

    async def get_proxy(self, **kwargs: Any) -> Any:
        self.proxy_types.append(kwargs["proxy_type"])
        self.excluded.append(set(kwargs["exclude_ids"]))
        self.proxies.append(uuid.uuid4())
        return SimpleNamespace(id=self.proxies[-1], country="PL")


def _run(recorder: _Recorder, *, premium_domains: str = "") -> Any:
    from app.core.config import settings
    from app.services.fetchers.base import fetch_with_retry

    with (
        mock.patch("app.services.fetchers.get_fetcher", recorder.get_fetcher),
        mock.patch("asyncio.sleep", mock.AsyncMock()),
        mock.patch.object(settings, "enable_premium_proxy_tiers", False),
        mock.patch.dict(os.environ, {"PREMIUM_PROXY_DOMAINS": premium_domains}),
    ):
        return asyncio.run(
            fetch_with_retry(
                recorder.get_fetcher("curl_cffi"),
                "https://www.example.pl/",
                proxy_manager=recorder,  # type: ignore[arg-type]
                requested_engine="curl_cffi",
            )
        )


class LadderHelperTests(unittest.TestCase):
    def test_premium_tiers_fall_back_to_datacenter_proxies(self) -> None:
        from app.services.escalation import LADDER, proxy_type_for

        for tier in LADDER:
            free = proxy_type_for(tier, enable_premium=False)
            self.assertNotIn(free, {"residential", "mobile"})
            self.assertEqual(proxy_type_for(tier, enable_premium=True), tier.proxy_type)
        self.assertEqual(proxy_type_for(LADDER[5], enable_premium=False), "datacenter")
        self.assertIsNone(proxy_type_for(LADDER[0], enable_premium=False))

    def test_free_ladder_skips_tiers_that_add_nothing(self) -> None:
        from app.services.escalation import LADDER, next_distinct_tier, next_tier

        order = [0]
        while (following := next_distinct_tier(order[-1], enable_premium=False)) is not None:
            order.append(following)
        self.assertEqual(order, [0, 1, 2, 4, 5])
        self.assertEqual(
            [LADDER[i].engine for i in order[2:]], ["curl_cffi", "playwright", "camoufox"]
        )
        for index in range(len(LADDER)):
            self.assertEqual(next_distinct_tier(index, enable_premium=True), next_tier(index))

    def test_premium_can_be_enabled_per_domain(self) -> None:
        from app.services.escalation import premium_enabled_for

        allowed = " Example.pl, other.com ,"
        self.assertTrue(premium_enabled_for("example.pl", global_flag=False, allowed=allowed))
        self.assertFalse(premium_enabled_for("third.pl", global_flag=False, allowed=allowed))
        self.assertFalse(premium_enabled_for("example.pl", global_flag=False, allowed=""))
        self.assertTrue(premium_enabled_for("third.pl", global_flag=True, allowed=""))


class RetryLoopTests(unittest.TestCase):
    def test_browsers_are_reached_on_datacenter_proxies(self) -> None:
        recorder = _Recorder()
        result = _run(recorder)
        self.assertEqual(
            recorder.engines,
            ["curl_cffi", "curl_cffi", "playwright", "playwright", "camoufox", "camoufox"],
        )
        self.assertEqual(set(recorder.proxy_types), {"datacenter"})
        self.assertTrue(result.blocked)
        self.assertEqual(result.engine, "camoufox")

    def test_listed_domain_uses_premium_proxies(self) -> None:
        recorder = _Recorder()
        _run(recorder, premium_domains="example.pl")
        self.assertEqual(recorder.engines[:6], ["curl_cffi"] * 4 + ["playwright"] * 2)
        self.assertEqual(recorder.proxy_types[:4], ["datacenter"] * 2 + ["residential"] * 2)
        self.assertEqual(recorder.proxy_types[-2:], ["mobile", "mobile"])

    def test_ip_banned_proxy_stays_excluded_on_the_next_tier(self) -> None:
        recorder = _Recorder(reasons=["ip_ban", "cloudflare"])
        _run(recorder)
        banned, challenged = recorder.proxies[0], recorder.proxies[1]
        self.assertEqual(recorder.engines[2], "playwright")
        self.assertIn(banned, recorder.excluded[2])
        self.assertNotIn(challenged, recorder.excluded[2])


if __name__ == "__main__":
    unittest.main()
