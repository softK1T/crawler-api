"""Offline tests for session-pinned proxies, the challenge idle and the worker environment."""

import asyncio
import os
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/testdb")

CHALLENGE = "<html><script>window._cf_chl_opt={}</script></html>"
REAL = "<html>normal page</html>"


class _Proxies:
    def __init__(self) -> None:
        self.sticky: list[str | None] = []
        self.report_result = mock.AsyncMock()

    async def get_proxy(self, **kwargs: Any) -> Any:
        self.sticky.append(kwargs["sticky_key"])
        return SimpleNamespace(id=uuid.uuid4(), country="PL")


def _sticky_keys(session_key: str | None) -> list[str | None]:
    """Run one blocked attempt followed by a successful one; return the sticky keys used."""
    from app.services.fetchers.base import FetchResult, fetch_with_retry

    calls = 0

    async def fetch(url: str, **kwargs: Any) -> FetchResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            return FetchResult(url=url, status_code=403, blocked=True, block_reason="ip_ban")
        return FetchResult(url=url, status_code=200)

    fetcher: Any = SimpleNamespace(_engine_name="httpx", fetch=fetch)
    proxies: Any = _Proxies()
    with (
        mock.patch("app.services.fetchers.get_fetcher", lambda engine, **kwargs: fetcher),
        mock.patch("asyncio.sleep", mock.AsyncMock()),
    ):
        asyncio.run(
            fetch_with_retry(
                fetcher,
                "https://www.example.pl/",
                proxy_manager=proxies,
                use_proxy=True,
                sticky_key="job-1",
                session_key=session_key,
            )
        )
    sticky: list[str | None] = proxies.sticky
    return sticky


class SessionProxyTests(unittest.TestCase):
    def test_session_pins_the_proxy_on_every_attempt(self) -> None:
        self.assertEqual(_sticky_keys("app:crawl-1"), ["session:app:crawl-1"] * 2)

    def test_without_a_session_only_the_first_attempt_is_sticky(self) -> None:
        self.assertEqual(_sticky_keys(None), ["job-1", None])

    def test_session_key_is_scoped_to_the_application(self) -> None:
        from app.worker.tasks.fetch_task import _session_scope

        self.assertEqual(_session_scope("app-1", "crawl"), "app-1:crawl")
        self.assertNotEqual(_session_scope("app-1", "crawl"), _session_scope("app-2", "crawl"))
        self.assertIsNone(_session_scope("app-1", None))
        self.assertIsNone(_session_scope("app-1", ""))


class ChallengeIdleTests(unittest.TestCase):
    def test_pointer_moves_while_a_challenge_is_pending(self) -> None:
        from app.services.fetchers.challenge import settle_page

        bodies = [CHALLENGE, REAL]
        mouse = SimpleNamespace(move=mock.AsyncMock(), wheel=mock.AsyncMock())

        async def content() -> str:
            return bodies.pop(0) if len(bodies) > 1 else bodies[0]

        page: Any = SimpleNamespace(content=content, wait_for_timeout=mock.AsyncMock(), mouse=mouse)
        self.assertEqual(asyncio.run(settle_page(page, budget_s=30.0)), REAL.encode())
        mouse.move.assert_awaited_once()
        mouse.wheel.assert_awaited_once()

    def test_a_page_without_a_pointer_is_still_read(self) -> None:
        from app.services.fetchers.challenge import settle_page

        bodies = [CHALLENGE, REAL]

        async def content() -> str:
            return bodies.pop(0) if len(bodies) > 1 else bodies[0]

        page: Any = SimpleNamespace(content=content, wait_for_timeout=mock.AsyncMock())
        self.assertEqual(asyncio.run(settle_page(page, budget_s=30.0)), REAL.encode())


class WorkerEnvironmentTests(unittest.TestCase):
    def test_compose_passes_the_new_settings_to_the_worker(self) -> None:
        compose = (Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text()
        for name in (
            "WORKER_JOB_TIMEOUT_S",
            "PREMIUM_PROXY_DOMAINS",
            "BROWSER_NATIVE_TRANSPORT",
            "BROWSER_CHALLENGE_WAIT_S",
        ):
            with self.subTest(name=name):
                self.assertIn(f"  {name}: ", compose)


if __name__ == "__main__":
    unittest.main()
