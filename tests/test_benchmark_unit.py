"""Offline tests for how the mode benchmark classifies jobs and aggregates them."""

import base64
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scripts import benchmark


def _job(body: str, **result: Any) -> dict[str, Any]:
    fields = {"blocked": False, "block_reason": None, "status_code": 200, **result}
    fields["body_b64"] = base64.b64encode(body.encode()).decode()
    return {"status": "completed", "result": fields}


def _row(target: str, outcome: str, seconds: float, attempts: int, engine: str) -> dict[str, Any]:
    return {
        "target": target,
        "mode": "static",
        "outcome": outcome,
        "seconds": seconds,
        "attempts": attempts,
        "engine": engine,
    }


class OutcomeTests(unittest.TestCase):
    def test_clean_200_without_the_marker_is_not_a_success(self) -> None:
        self.assertEqual(benchmark.outcome(_job("<div class='quote'>"), "quote"), "ok")
        self.assertEqual(benchmark.outcome(_job("<div id='app'>"), "quote"), "marker_missing")

    def test_failure_is_named_by_its_cause(self) -> None:
        failed = {"status": "failed", "error": "PROXY_POOL_EMPTY: no healthy proxy", "result": None}
        self.assertEqual(benchmark.outcome(failed, "quote"), "PROXY_POOL_EMPTY")
        blocked = _job("quote", blocked=True, block_reason="cloudflare", status_code=403)
        self.assertEqual(benchmark.outcome(blocked, "quote"), "cloudflare")
        self.assertEqual(benchmark.outcome(_job("quote", status_code=404), "quote"), "http_404")


class ReportTests(unittest.TestCase):
    def test_latency_and_engines_cover_successes_only(self) -> None:
        report = benchmark.render(
            [
                _row("https://a.example/", "ok", 1.0, 1, "httpx"),
                _row("https://a.example/", "ok", 3.0, 3, "camoufox"),
                _row("https://b.example/", "cloudflare", 60.0, 5, "camoufox"),
            ]
        )
        self.assertIn(
            "| static | 2/3 | 1.00 | 3.00 | 3.0 | camoufox 1, httpx 1 | cloudflare 1 |", report
        )
        self.assertIn("| https://a.example/ | 2/2 |", report)
        self.assertIn("| https://b.example/ | 0/1 |", report)

    def test_mode_without_successes_has_no_latency(self) -> None:
        report = benchmark.render([_row("https://b.example/", "http_500", 2.0, 2, "httpx")])
        self.assertIn("| static | 0/1 | - | - | 2.0 | - | http_500 1 |", report)


class TargetsTests(unittest.TestCase):
    def test_line_splits_into_url_and_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "targets.txt"
            path.write_text("# comment\n\nhttps://example.org/ Example Domain\n")
            self.assertEqual(
                benchmark.read_targets(path), [("https://example.org/", "Example Domain")]
            )
            path.write_text("https://example.org/\n")
            with self.assertRaises(ValueError):
                benchmark.read_targets(path)

    def test_bundled_targets_parse(self) -> None:
        self.assertTrue(benchmark.read_targets(benchmark.TARGETS))


if __name__ == "__main__":
    unittest.main()
