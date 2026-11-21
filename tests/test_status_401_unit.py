"""Offline tests: a bare 401 is the site's answer, not an anti-bot block."""

import asyncio
import os
import unittest
from types import SimpleNamespace
from typing import Any
from unittest import mock

import httpx

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/testdb")


def _fetch_login_page(use_proxy: bool | None) -> tuple[list[str], Any, Any]:
    """Run the real retry loop and httpx fetcher against a site that answers 401."""
    from app.services.fetchers.base import fetch_with_retry
    from app.services.fetchers.httpx_fetcher import HttpxFetcher

    engines: list[str] = []
    real_client = httpx.AsyncClient

    def respond(request: httpx.Request) -> httpx.Response:
        body = httpx.ByteStream(b"login required")
        return httpx.Response(401, headers={"www-authenticate": "Basic"}, stream=body)

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
        result = asyncio.run(
            fetch_with_retry(
                get_fetcher("httpx"),
                "https://shop.example/account",
                proxy_manager=proxies,  # type: ignore[arg-type]
                use_proxy=use_proxy,
            )
        )
    return engines, proxies, result


class Status401Tests(unittest.TestCase):
    def test_bare_401_is_not_a_block(self) -> None:
        from app.schemas.fetch import BlockReason
        from app.services.block_detector import detect_block_reason

        self.assertIsNone(detect_block_reason(401, {}, b""))
        self.assertIsNone(detect_block_reason(401, {"www-authenticate": "Basic"}, b"login"))
        for status in (403, 407, 451):
            with self.subTest(status=status):
                self.assertEqual(detect_block_reason(status, {}, b""), BlockReason.IP_BAN)

    def test_401_with_a_block_marker_keeps_its_reason(self) -> None:
        from app.schemas.fetch import BlockReason
        from app.services.block_detector import detect_block_reason

        markers = {
            b'<form class="cf-chl-form"></form>': BlockReason.CLOUDFLARE,
            b'<div class="g-recaptcha"></div>': BlockReason.CAPTCHA,
            b"Your IP has been blocked": BlockReason.IP_BAN,
            b"Request rejected by the web application firewall": BlockReason.WAF,
        }
        for body, reason in markers.items():
            with self.subTest(reason=reason):
                self.assertEqual(detect_block_reason(401, {}, body), reason)

    def test_default_job_returns_the_401_after_one_direct_attempt(self) -> None:
        engines, proxies, result = _fetch_login_page(use_proxy=None)

        self.assertEqual(engines, ["httpx"])
        proxies.get_proxy.assert_not_awaited()
        proxies.report_result.assert_not_awaited()
        self.assertEqual((result.status_code, result.blocked), (401, False))

    def test_proxied_job_returns_the_401_and_does_not_fail_the_proxy(self) -> None:
        engines, proxies, result = _fetch_login_page(use_proxy=True)

        self.assertEqual(engines, ["httpx"])
        reported = [call.kwargs["success"] for call in proxies.report_result.await_args_list]
        self.assertEqual(reported, [True])
        self.assertEqual((result.status_code, result.blocked), (401, False))


if __name__ == "__main__":
    unittest.main()
