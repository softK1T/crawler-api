"""Offline tests for native browser transport and the challenge wait."""

import asyncio
import os
import unittest
from types import SimpleNamespace
from typing import Any
from unittest import mock

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/testdb")

CHALLENGE = "<html><script>window._cf_chl_opt={}</script></html>"
REAL = "<html>normal page</html>"


class _FakeContext:
    def __init__(self) -> None:
        self.handlers: list[Any] = []

    async def route(self, pattern: str, handler: Any) -> None:
        self.handlers.append(handler)

    async def route_web_socket(self, pattern: str, handler: Any) -> None:
        return None


class _FakePage:
    def __init__(self, bodies: list[str | Exception]) -> None:
        self.bodies = bodies
        self.waits = 0
        self.main_frame = object()
        self.listeners: dict[str, Any] = {}

    def on(self, event: str, handler: Any) -> None:
        self.listeners[event] = handler

    async def content(self) -> str:
        item = self.bodies.pop(0) if len(self.bodies) > 1 else self.bodies[0]
        if isinstance(item, Exception):
            raise item
        return item

    async def wait_for_timeout(self, delay_ms: float) -> None:
        self.waits += 1


def _route() -> Any:
    route = mock.Mock()
    route.request.url = "https://www.example.pl/"
    route.fetch = mock.AsyncMock()
    route.continue_ = mock.AsyncMock()
    route.abort = mock.AsyncMock()
    return route


def _run_native(route: Any) -> None:
    from app.services.fetchers import browser_guard

    async def scenario() -> None:
        context = _FakeContext()
        with mock.patch.object(browser_guard, "validate_url_async", mock.AsyncMock()):
            await browser_guard.install_browser_guard(context, native_transport=True)
            await context.handlers[0](route)

    asyncio.run(scenario())


class NativeTransportTests(unittest.TestCase):
    def test_request_is_continued_by_the_browser_itself(self) -> None:
        route = _route()
        _run_native(route)
        route.continue_.assert_awaited_once_with()
        route.fetch.assert_not_awaited()
        route.abort.assert_not_awaited()

    def test_continue_failure_aborts_the_route(self) -> None:
        route = _route()
        route.continue_ = mock.AsyncMock(side_effect=RuntimeError("context closed"))
        _run_native(route)
        route.abort.assert_awaited_once_with("failed")

    def test_blocked_url_is_never_continued(self) -> None:
        from app.core.url_guard import URLGuardError
        from app.services.fetchers import browser_guard

        route = _route()

        async def scenario() -> list[str]:
            context = _FakeContext()
            reject = mock.AsyncMock(side_effect=URLGuardError("private range"))
            with mock.patch.object(browser_guard, "validate_url_async", reject):
                blocked = await browser_guard.install_browser_guard(context, native_transport=True)
                await context.handlers[0](route)
            return blocked

        self.assertEqual(asyncio.run(scenario()), ["https://www.example.pl/"])
        route.continue_.assert_not_awaited()
        route.abort.assert_awaited_once_with("blockedbyclient")

    def test_native_transport_needs_a_proxy_and_can_be_switched_off(self) -> None:
        from app.services.fetchers.browser_guard import native_transport_enabled

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BROWSER_NATIVE_TRANSPORT", None)
            self.assertTrue(native_transport_enabled(True))
            self.assertFalse(native_transport_enabled(False))
        with mock.patch.dict(os.environ, {"BROWSER_NATIVE_TRANSPORT": "0"}):
            self.assertFalse(native_transport_enabled(True))


class ChallengeWaitTests(unittest.TestCase):
    def test_only_self_resolving_challenges_count_as_pending(self) -> None:
        from app.services.fetchers.challenge import challenge_pending

        self.assertTrue(challenge_pending(CHALLENGE.encode()))
        self.assertFalse(challenge_pending(REAL.encode()))
        self.assertFalse(challenge_pending(b"<title>Attention Required! | Cloudflare</title>"))

    def test_page_is_read_after_the_challenge_clears(self) -> None:
        from app.services.fetchers.challenge import settle_page

        page = _FakePage([CHALLENGE, RuntimeError("page is navigating"), REAL])
        body = asyncio.run(settle_page(page, budget_s=30.0))
        self.assertEqual(body, REAL.encode())
        self.assertEqual(page.waits, 2)

    def test_normal_page_and_spent_budget_do_not_wait(self) -> None:
        from app.services.fetchers.challenge import settle_page

        normal = _FakePage([REAL])
        self.assertEqual(asyncio.run(settle_page(normal, budget_s=30.0)), REAL.encode())
        self.assertEqual(normal.waits, 0)
        stuck = _FakePage([CHALLENGE])
        self.assertEqual(asyncio.run(settle_page(stuck, budget_s=0.0)), CHALLENGE.encode())
        self.assertEqual(stuck.waits, 0)

    def test_tracker_keeps_the_latest_top_level_document(self) -> None:
        from app.services.fetchers.challenge import NavigationTracker

        page = _FakePage([REAL])
        tracker = NavigationTracker(page)
        emit = page.listeners["response"]

        def response(status: int, *, navigation: bool = True, top: bool = True) -> Any:
            request = SimpleNamespace(
                is_navigation_request=lambda: navigation,
                frame=page.main_frame if top else object(),
            )
            return SimpleNamespace(status=status, request=request)

        self.assertIsNone(tracker.response)
        emit(response(403))
        emit(response(200, navigation=False))
        emit(response(200, top=False))
        self.assertEqual(tracker.response.status, 403)
        emit(response(200))
        self.assertEqual(tracker.response.status, 200)
        emit(SimpleNamespace())
        self.assertEqual(tracker.response.status, 200)


if __name__ == "__main__":
    unittest.main()
