"""Offline tests: the worker serves its own Prometheus endpoint only when a port is set."""

import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:1/testdb")


def _ports_served(**settings: int) -> list[Any]:
    from app.core.observability import start_worker_metrics_server

    with mock.patch("app.core.observability.start_http_server") as start:
        start_worker_metrics_server(SimpleNamespace(**settings))
    return start.call_args_list


class WorkerMetricsServerTests(unittest.TestCase):
    def test_a_port_starts_the_server_on_it(self) -> None:
        served = _ports_served(enable_metrics=True, worker_metrics_port=9464)
        self.assertEqual(served, [mock.call(9464)])

    def test_no_port_and_disabled_metrics_start_nothing(self) -> None:
        self.assertEqual(_ports_served(enable_metrics=True, worker_metrics_port=0), [])
        self.assertEqual(_ports_served(enable_metrics=False, worker_metrics_port=9464), [])

    def test_the_endpoint_is_off_unless_configured(self) -> None:
        from app.core.config import Settings

        self.assertEqual(Settings.model_fields["worker_metrics_port"].default, 0)

    def test_compose_publishes_the_port_the_worker_listens_on(self) -> None:
        compose = (Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text()
        self.assertIn('      WORKER_METRICS_PORT: "9464"\n', compose)
        self.assertIn('      - "127.0.0.1:${WORKER_METRICS_PORT:-9464}:9464"\n', compose)


if __name__ == "__main__":
    unittest.main()
