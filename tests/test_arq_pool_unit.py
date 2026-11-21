"""Offline tests: the API keeps one arq pool and both enqueue paths use it."""

import asyncio
import os
import unittest
import uuid
from types import SimpleNamespace
from typing import Any
from unittest import mock

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/testdb")


class SharedArqPoolTests(unittest.TestCase):
    def test_fetch_and_batch_enqueue_through_the_app_pool(self) -> None:
        from app.api.v1.endpoints.batches import create_crawl_batch
        from app.api.v1.endpoints.jobs import create_fetch
        from app.schemas.job import JobCreate
        from app.schemas.requests import BatchCrawlRequest

        pool = mock.AsyncMock()
        state = SimpleNamespace(
            rate_limiter=mock.AsyncMock(), redis=mock.AsyncMock(), arq_pool=pool
        )
        req: Any = SimpleNamespace(headers={}, app=SimpleNamespace(state=state))
        api_key: Any = SimpleNamespace(
            id=uuid.uuid4(), prefix="ck_test", application_id=uuid.uuid4()
        )
        fetch = JobCreate.model_validate({"url": "https://www.example.com/"})
        batch = BatchCrawlRequest.model_validate(
            {"urls": [f"https://www.example.com/{n}" for n in range(3)]}
        )

        async def two_fetches_and_a_batch() -> None:
            await create_fetch(fetch, req, api_key, mock.Mock())
            await create_fetch(fetch, req, api_key, mock.Mock())
            await create_crawl_batch(batch, req, api_key, mock.Mock())

        with (
            mock.patch(
                "app.services.policy_resolver.resolve_policy", mock.AsyncMock(return_value=None)
            ),
            mock.patch("app.services.batch_service.storage"),
            mock.patch("arq.create_pool", mock.AsyncMock()) as create_pool,
        ):
            asyncio.run(two_fetches_and_a_batch())

        create_pool.assert_not_called()
        self.assertEqual(pool.enqueue_job.await_count, 5)
        pool.aclose.assert_not_called()

    def test_app_opens_the_pool_lazily_on_the_arq_url_and_closes_it_at_shutdown(self) -> None:
        from arq.connections import ArqRedis
        from fastapi import FastAPI

        from app.core.config import settings
        from app.main import lifespan

        async def serve() -> Any:
            app = FastAPI()
            async with lifespan(app):
                closed.assert_not_called()
                return app.state.arq_pool

        with (
            mock.patch.object(settings, "enable_warc", False),
            mock.patch.object(settings, "arq_redis_url", "redis://127.0.0.1:1/7"),
            mock.patch.object(ArqRedis, "aclose", autospec=True) as closed,
        ):
            pool = asyncio.run(serve())

        connection = pool.connection_pool.connection_kwargs
        self.assertEqual((connection["db"], connection["socket_connect_timeout"]), (7, 1))
        closed.assert_awaited_once_with(pool)


if __name__ == "__main__":
    unittest.main()
