"""Offline tests for the wait_for request option."""

import asyncio
import os
import unittest
import uuid
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/testdb")

LATE = "<html><div class='product'>ready</div></html>"


def _page(failure: Exception | None = None) -> mock.Mock:
    page = mock.Mock()
    page.wait_for_selector = mock.AsyncMock(side_effect=failure)
    page.content = mock.AsyncMock(return_value=LATE)
    return page


class OptionTests(unittest.TestCase):
    def test_selector_alone_keeps_the_options_for_the_worker(self) -> None:
        from app.services.fetchers.request_options import RequestOptions

        options = RequestOptions.from_options({"wait_for": ".product"})
        self.assertEqual(options.wait_for, ".product")
        self.assertTrue(options)

    def test_invalid_selector_values_are_rejected(self) -> None:
        from app.services.fetchers.request_options import RequestOptions

        for value in (" ", 5, "x" * 513):
            with self.subTest(value=str(value)[:8]), self.assertRaises(ValueError):
                RequestOptions.from_options({"wait_for": value})

    def test_job_schema_accepts_wait_for(self) -> None:
        from app.schemas.job import JobCreate

        job = JobCreate.model_validate({"url": "https://www.example.com/", "wait_for": ".product"})
        self.assertEqual(job.wait_for, ".product")


class SelectorWaitTests(unittest.TestCase):
    def test_page_is_read_once_the_selector_is_attached(self) -> None:
        from app.services.fetchers.challenge import wait_for_selector

        page = _page()
        body = asyncio.run(wait_for_selector(page, ".product", budget_s=12.5))
        self.assertEqual(body, LATE.encode())
        page.wait_for_selector.assert_awaited_once_with(".product", state="attached", timeout=12500)

    def test_spent_budget_still_gets_a_bounded_wait(self) -> None:
        from app.services.fetchers.challenge import wait_for_selector

        page = _page()
        asyncio.run(wait_for_selector(page, ".product", budget_s=-3.0))
        self.assertEqual(page.wait_for_selector.await_args.kwargs["timeout"], 1000)

    def test_failed_wait_carries_the_first_line_of_the_browser_error(self) -> None:
        from app.services.fetchers.base import WaitForError
        from app.services.fetchers.challenge import wait_for_selector

        page = _page(TimeoutError("Timeout 12500ms exceeded.\nCall log:\n  - waiting"))
        with self.assertRaises(WaitForError) as caught:
            asyncio.run(wait_for_selector(page, ".product", budget_s=12.5))
        self.assertEqual(str(caught.exception), "wait_for failed: Timeout 12500ms exceeded.")
        page.content.assert_not_awaited()


class RetryLoopTests(unittest.TestCase):
    def test_failed_wait_ends_the_fetch_without_retry_or_proxy_penalty(self) -> None:
        from app.services.fetchers.base import WaitForError, fetch_with_retry

        fetch = mock.AsyncMock(side_effect=WaitForError("wait_for failed: Timeout"))
        fetcher = SimpleNamespace(_engine_name="playwright", fetch=fetch)
        manager = mock.Mock()
        manager.get_proxy = mock.AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4()))
        manager.report_result = mock.AsyncMock()
        with mock.patch("asyncio.sleep", mock.AsyncMock()), self.assertRaises(WaitForError):
            asyncio.run(
                fetch_with_retry(
                    fetcher,
                    "https://www.example.pl/",
                    proxy_manager=manager,
                    requested_engine="playwright",
                )
            )
        fetch.assert_awaited_once()
        manager.report_result.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
