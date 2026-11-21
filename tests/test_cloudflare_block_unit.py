"""Offline tests: a site's own error behind Cloudflare is not an anti-bot block."""

import asyncio
import os
import unittest
from types import SimpleNamespace
from typing import Any
from unittest import mock

import httpx

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/testdb")

CLOUDFLARE_HEADERS = {"cf-ray": "8f1c2d3e4a5b6c7d-WAW", "server": "cloudflare"}


class CloudflareBlockTests(unittest.TestCase):
    def test_only_403_and_challenge_bodies_are_blocks(self) -> None:
        from app.schemas.fetch import BlockReason
        from app.services.block_detector import detect_block_reason

        for status in (404, 410, 500, 502, 503, 521, 522, 526):
            with self.subTest(status=status):
                self.assertIsNone(detect_block_reason(status, CLOUDFLARE_HEADERS, b""))
        for headers in ({"cf-ray": "abc"}, {"Server": "cloudflare"}):
            with self.subTest(headers=headers):
                reason = detect_block_reason(403, headers, b"")
                self.assertEqual(reason, BlockReason.CLOUDFLARE)
        challenge = b'<form class="cf-chl-form"></form>'
        self.assertEqual(
            detect_block_reason(404, CLOUDFLARE_HEADERS, challenge), BlockReason.CLOUDFLARE
        )

    def test_404_behind_cloudflare_is_returned_after_one_attempt(self) -> None:
        from app.services.fetchers.base import fetch_with_retry
        from app.services.fetchers.httpx_fetcher import HttpxFetcher

        engines: list[str] = []
        real_client = httpx.AsyncClient

        def respond(request: httpx.Request) -> httpx.Response:
            body = httpx.ByteStream(b"Not Found")
            return httpx.Response(404, headers=CLOUDFLARE_HEADERS, stream=body)

        def get_fetcher(engine: str, **kwargs: Any) -> Any:
            async def fetch(url: str, **kw: Any) -> Any:
                engines.append(engine)
                return await HttpxFetcher().fetch(url, **kw)

            return SimpleNamespace(_engine_name=engine, fetch=fetch)

        proxies = SimpleNamespace(
            get_proxy=mock.AsyncMock(return_value=SimpleNamespace(id=1, country="PL")),
            report_result=mock.AsyncMock(),
        )
        transport = httpx.MockTransport(respond)
        with (
            mock.patch("httpx.AsyncClient", lambda **kw: real_client(transport=transport, **kw)),
            mock.patch("app.core.url_guard.validate_url_async", mock.AsyncMock()),
            mock.patch("app.services.fetchers.get_fetcher", get_fetcher),
            mock.patch("asyncio.sleep", mock.AsyncMock()),
        ):
            url = "https://shop.example/missing"
            result = asyncio.run(
                fetch_with_retry(get_fetcher("httpx"), url, proxy_manager=proxies)  # type: ignore[arg-type]
            )
        self.assertEqual(engines, ["httpx"])
        proxies.get_proxy.assert_not_awaited()
        proxies.report_result.assert_not_awaited()
        self.assertEqual((result.status_code, result.blocked), (404, False))


if __name__ == "__main__":
    unittest.main()


class VendorFloorTests(unittest.TestCase):
    """Cloudflare marks every response it proxies; only a block teaches the vendor floor."""

    def _learn(self, *, status_code: int, blocked: bool, stored: str | None = None) -> Any:
        from app.models.domain_policy import DomainPolicy
        from app.services.fetchers.base import FetchResult
        from app.services.policy_learner import record_outcome

        row = DomainPolicy(
            domain="blog.example",
            escalation_tier=0,
            tier_locked=False,
            antibot_type=stored,
            consecutive_blocks=0,
        )
        found = mock.Mock()
        found.scalar_one_or_none.return_value = row
        db = mock.AsyncMock()
        db.execute.return_value = found
        result = FetchResult(
            url="https://blog.example/a",
            status_code=status_code,
            headers=CLOUDFLARE_HEADERS,
            blocked=blocked,
        )
        asyncio.run(
            record_outcome(result=result, policy=row, db=db, engine_used="httpx", tier_used=0)
        )
        return row

    def test_served_page_leaves_the_vendor_unset_and_the_start_tier_direct(self) -> None:
        from app.services.escalation import initial_tier

        row = self._learn(status_code=200, blocked=False)
        self.assertIsNone(row.antibot_type)
        self.assertEqual(initial_tier(row), 0)

    def test_block_stores_the_vendor(self) -> None:
        row = self._learn(status_code=403, blocked=True)
        self.assertEqual(row.antibot_type, "cloudflare")

    def test_served_page_keeps_a_stored_vendor(self) -> None:
        row = self._learn(status_code=200, blocked=False, stored="cloudflare")
        self.assertEqual(row.antibot_type, "cloudflare")
