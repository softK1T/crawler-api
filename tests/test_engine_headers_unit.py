"""Offline tests for engine-specific headers and the curl_cffi identity."""

import asyncio
import os
import unittest
from types import SimpleNamespace
from typing import Any, Self
from unittest import mock

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/testdb")

POLICY = SimpleNamespace(
    header_profile={
        "User-Agent": "Custom/1.0",
        "Accept": "text/plain",
        "Accept-Language": "de-DE",
        "X-Extra": "yes",
    }
)


class _FakeResponse:
    def __init__(self, status_code: int, headers: dict[str, str]) -> None:
        self.status_code = status_code
        self.headers = headers


class _FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def get(self, url: str, content_callback: Any = None, **kwargs: Any) -> _FakeResponse:
        self.calls.append((url, kwargs))
        if len(self.calls) == 1:
            return _FakeResponse(302, {"location": "/next"})
        content_callback(b"<html>normal page</html>")
        return _FakeResponse(200, {"content-type": "text/html"})


class EngineHeaderTests(unittest.TestCase):
    def test_fingerprinted_engines_get_no_default_headers(self) -> None:
        from app.services.fetchers.headers import headers_for_engine

        for engine in ("curl_cffi", "playwright", "camoufox"):
            with self.subTest(engine=engine):
                self.assertEqual(headers_for_engine(None, engine), {})

    def test_curl_language_follows_the_proxy_country(self) -> None:
        from app.services.fetchers.headers import headers_for_engine

        headers = headers_for_engine(None, "curl_cffi", " pl ")
        self.assertEqual(list(headers), ["Accept-Language"])
        self.assertTrue(headers["Accept-Language"].startswith("pl-PL"))
        self.assertEqual(headers_for_engine(None, "curl_cffi", "ZZ"), {})
        self.assertEqual(headers_for_engine(None, "camoufox", "PL"), {})

    def test_policy_identity_headers_do_not_reach_fingerprinted_engines(self) -> None:
        from app.services.fetchers.headers import headers_for_engine

        self.assertEqual(
            headers_for_engine(POLICY, "curl_cffi", "PL"),
            {"X-Extra": "yes", "Accept-Language": "de-DE"},
        )
        self.assertEqual(headers_for_engine(POLICY, "playwright", "PL"), {"X-Extra": "yes"})
        self.assertEqual(headers_for_engine(POLICY, "camoufox", "PL"), {"X-Extra": "yes"})

    def test_httpx_keeps_the_generic_header_set(self) -> None:
        from app.services.fetchers.headers import headers_for_engine

        headers = headers_for_engine(POLICY, "httpx", "PL")
        self.assertEqual(headers["User-Agent"], "Custom/1.0")
        self.assertEqual(headers["X-Extra"], "yes")
        self.assertIn("Accept-Encoding", headers)


class CurlIdentityTests(unittest.TestCase):
    def test_default_profile_pins_no_user_agent(self) -> None:
        from app.services.fetchers.curl_fetcher import IMPERSONATION
        from app.services.fetchers.request_options import curl_identity

        self.assertEqual(IMPERSONATION, "chrome")
        self.assertEqual(curl_identity(None, IMPERSONATION, None), ("chrome", None))

    def test_one_session_spans_the_redirect_chain(self) -> None:
        from app.services.fetchers.curl_fetcher import IMPERSONATION, CurlFetcher

        session = _FakeSession()
        with (
            mock.patch("curl_cffi.requests.Session", return_value=session) as factory,
            mock.patch("app.core.url_guard.validate_url_sync"),
        ):
            result = asyncio.run(CurlFetcher().fetch("https://example.pl/start", headers={}))
        factory.assert_called_once_with()
        self.assertEqual(
            [url for url, _ in session.calls],
            ["https://example.pl/start", "https://example.pl/next"],
        )
        for _, kwargs in session.calls:
            self.assertEqual(kwargs["impersonate"], IMPERSONATION)
            self.assertNotIn("headers", kwargs)
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.blocked)


if __name__ == "__main__":
    unittest.main()
