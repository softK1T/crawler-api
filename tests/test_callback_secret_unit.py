"""Offline tests for when a submission may carry a callback_url."""

import asyncio
import os
import unittest
import uuid
from types import SimpleNamespace
from typing import Any
from unittest import mock

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/testdb")

from fastapi import HTTPException

_HOOK = "https://client.example/hook"


class CallbackSecretTests(unittest.TestCase):
    def setUp(self) -> None:
        from app.services.batch_service import storage
        from app.services.rate_limiter import _allowed

        check = mock.AsyncMock(return_value=_allowed(limit=60, count=1, reset_at_ms=0, layer="key"))
        limiter = SimpleNamespace(
            check_all=check, check_key=check, check_domain=check, check_application=check
        )
        self.arq_pool = mock.AsyncMock()
        state = SimpleNamespace(
            rate_limiter=limiter, redis=mock.AsyncMock(), arq_pool=self.arq_pool
        )
        self.req: Any = SimpleNamespace(headers={}, app=SimpleNamespace(state=state))
        self.api_key: Any = SimpleNamespace(
            id=uuid.uuid4(), prefix="ck_test", application_id=uuid.uuid4()
        )
        self.enterContext(
            mock.patch(
                "app.services.policy_resolver.resolve_policy", mock.AsyncMock(return_value=None)
            )
        )
        self.enterContext(mock.patch.object(storage, "save_batch_info"))

    def _configure(self, *, enabled: bool, secret: str) -> None:
        from app.core.config import settings

        self.enterContext(mock.patch.object(settings, "enable_callbacks", enabled))
        self.enterContext(mock.patch.object(settings, "callback_hmac_secret", secret))

    def _fetch(self) -> None:
        from app.api.v1.endpoints.jobs import create_fetch
        from app.schemas.job import JobCreate

        body = JobCreate.model_validate({"url": "https://www.example.com/", "callback_url": _HOOK})
        asyncio.run(create_fetch(body, self.req, self.api_key, mock.Mock()))

    def _batch(self) -> None:
        from app.api.v1.endpoints.batches import create_crawl_batch
        from app.schemas.requests import BatchCrawlRequest

        body = BatchCrawlRequest.model_validate(
            {"urls": ["https://www.example.com/"], "callback_url": _HOOK}
        )
        asyncio.run(create_crawl_batch(body, self.req, self.api_key, mock.Mock()))

    def _assert_refused(self) -> None:
        for submit in (self._fetch, self._batch):
            with self.subTest(submit.__name__):
                with self.assertRaises(HTTPException) as caught:
                    submit()
                self.assertEqual(caught.exception.status_code, 422)
                self.assertEqual(caught.exception.detail, "Callbacks are disabled")
        self.arq_pool.enqueue_job.assert_not_awaited()

    def test_callback_url_is_refused_while_the_secret_is_empty(self) -> None:
        self._configure(enabled=True, secret="")
        self._assert_refused()

    def test_callback_url_is_refused_while_callbacks_are_switched_off(self) -> None:
        self._configure(enabled=False, secret="secret")
        self._assert_refused()

    def test_callback_url_is_accepted_with_callbacks_on_and_a_secret(self) -> None:
        self._configure(enabled=True, secret="secret")
        for submit in (self._fetch, self._batch):
            with self.subTest(submit.__name__):
                self.arq_pool.enqueue_job.reset_mock()
                submit()
                self.arq_pool.enqueue_job.assert_awaited_once()
                sent = self.arq_pool.enqueue_job.await_args.kwargs
                self.assertEqual(sent["callback_url"], _HOOK)


if __name__ == "__main__":
    unittest.main()
