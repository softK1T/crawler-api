"""Offline tests: a fetch that outlives the job limit fails as a timeout, not as a shutdown."""

import asyncio
import json
import os
import unittest
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest import mock

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/testdb")

JOB_ID = "job-1"


async def _never_answers(**_kwargs: Any) -> None:
    await asyncio.sleep(3600)


class JobTimeoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store: dict[str, Any] = {}
        self.redis = mock.AsyncMock()
        self.redis.get.side_effect = self.store.get
        self.redis.set.side_effect = lambda key, value, ex=None: self.store.update({key: value})
        self.db = mock.AsyncMock()

    def _job(self, limit_s: int) -> Any:
        from app.worker.tasks.fetch_task import fetch_task

        @asynccontextmanager
        async def db_factory() -> Any:
            yield self.db

        settings = SimpleNamespace(
            worker_job_timeout_s=limit_s,
            job_result_ttl_s=60,
            enable_callbacks=True,
            callback_hmac_secret="secret",
        )
        return fetch_task(
            {"settings": settings, "redis": self.redis, "db_factory": db_factory},
            job_id=JOB_ID,
            url="https://www.example.com/",
            mode="static",
            api_key_prefix="ck_test",
            application_id=str(uuid.uuid4()),
            domain="www.example.com",
            proxy_pool_id=None,
            callback_url="https://hooks.example.com/done",
            options={"use_policy": False},
            api_key_id=str(uuid.uuid4()),
        )

    def _failed_writes(self) -> list[str]:
        return [
            call.args[1]
            for call in self.redis.set.await_args_list
            if call.args[0] == f"job:{JOB_ID}:status"
            and json.loads(call.args[1])["status"] == "failed"
        ]

    def test_fetch_past_the_limit_fails_as_a_job_timeout(self) -> None:
        async def under_the_arq_limit() -> str:
            # arq runs a job the same way: wait_for(task, job_timeout).
            try:
                await asyncio.wait_for(self._job(limit_s=1), 1)
            except TimeoutError:
                return "cancelled by arq"
            return "ended by itself"

        with (
            mock.patch("app.services.fetchers.base.fetch_with_retry", _never_answers),
            mock.patch("app.worker.tasks.fetch_task._record_latency") as latency,
            mock.patch("app.worker.tasks.fetch_task._schedule_callback") as callback,
        ):
            ending = asyncio.run(under_the_arq_limit())

        self.assertEqual(self.store.get(f"job:{JOB_ID}:error"), "Job timed out (limit 1 s)")
        self.assertEqual(ending, "ended by itself")
        self.assertEqual(len(self._failed_writes()), 1)
        self.assertNotIn("Worker shutdown", str(self.redis.set.await_args_list))
        self.db.execute.assert_awaited_once()
        self.assertEqual(self.db.execute.await_args.args[1]["br"], 0)
        self.assertEqual([call.args[0] for call in latency.call_args_list], ["failed"])
        callback.assert_called_once()
        self.assertEqual(
            (callback.call_args.kwargs["status"], callback.call_args.kwargs["error"]),
            ("failed", "Job timed out (limit 1 s)"),
        )

    def test_engine_timeout_at_the_smallest_limit_keeps_its_own_error(self) -> None:
        async def engine_times_out(**_kwargs: Any) -> None:
            await asyncio.sleep(0)
            raise TimeoutError("engine gave up")

        with (
            mock.patch("app.services.fetchers.base.fetch_with_retry", engine_times_out),
            mock.patch("app.worker.tasks.fetch_task._schedule_callback"),
        ):
            asyncio.run(self._job(limit_s=10))

        self.assertEqual(self.store.get(f"job:{JOB_ID}:error"), "engine gave up")
        self.assertEqual(len(self._failed_writes()), 1)

    def test_cancelled_job_still_says_worker_shutdown(self) -> None:
        async def cancel_while_fetching() -> None:
            task = asyncio.ensure_future(self._job(limit_s=120))
            await asyncio.sleep(0.05)
            task.cancel()
            await task

        with (
            mock.patch("app.services.fetchers.base.fetch_with_retry", _never_answers),
            mock.patch("app.worker.tasks.fetch_task._schedule_callback") as callback,
            self.assertRaises(asyncio.CancelledError),
        ):
            asyncio.run(cancel_while_fetching())

        self.assertEqual(self.store.get(f"job:{JOB_ID}:error"), "Worker shutdown")
        self.assertEqual(len(self._failed_writes()), 1)
        self.assertEqual(callback.call_args.kwargs["error"], "Worker shutdown")


if __name__ == "__main__":
    unittest.main()
