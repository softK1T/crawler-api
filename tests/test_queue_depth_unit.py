"""Offline tests: crawler_queue_depth follows the sorted set arq keeps its queue in."""

import asyncio
import os
import unittest
from unittest import mock

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/testdb")

from prometheus_client import REGISTRY


class _Redis:
    """Holds what arq's enqueue_job writes: ZADD <queue name> {job id: score}."""

    def __init__(self, jobs: int) -> None:
        self.zsets = {"arq:crawler": {f"job-{n}": float(n) for n in range(jobs)}}

    async def llen(self, key: str) -> int:
        assert key not in self.zsets, "LLEN on a sorted set is a WRONGTYPE error"
        return 0

    async def zcard(self, key: str) -> int:
        return len(self.zsets.get(key, {}))


def _depth() -> float | None:
    return REGISTRY.get_sample_value("crawler_queue_depth", {"queue_name": "arq:crawler"})


class QueueDepthGaugeTests(unittest.TestCase):
    def test_gauge_counts_the_jobs_in_the_arq_queue(self) -> None:
        from app.core.observability import observe_queue_depth

        asyncio.run(observe_queue_depth(_Redis(jobs=3)))
        self.assertEqual(_depth(), 3.0)

    def test_metrics_endpoint_reads_the_queue_each_time_it_is_served(self) -> None:
        from app.main import app, metrics

        redis = _Redis(jobs=2)
        with mock.patch.object(app.state, "redis", redis, create=True):
            asyncio.run(metrics())
            self.assertEqual(_depth(), 2.0)
            redis.zsets["arq:crawler"].clear()
            asyncio.run(metrics())
            self.assertEqual(_depth(), 0.0)


if __name__ == "__main__":
    unittest.main()
