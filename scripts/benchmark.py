"""Compare fetch modes on known pages: success, latency, attempts, delivering engine.

A job is a success only when the target's marker text is in the body: a completed,
unblocked 200 can still be an empty JavaScript shell or an undecoded page.
"""

import argparse
import base64
import math
import sys
import time
from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

TARGETS = Path(__file__).resolve().parent / "data" / "benchmark_targets.txt"
# Above the stack's WORKER_JOB_TIMEOUT_S: past it, the job is stuck, not slow.
JOB_TIMEOUT_S = 420


def read_targets(path: Path) -> list[tuple[str, str]]:
    """Each line is `URL marker`; blank lines and `#` comments are skipped."""
    targets = []
    for line in path.read_text().splitlines():
        url, _, marker = line.strip().partition(" ")
        if not url or url.startswith("#"):
            continue
        if not marker:
            raise ValueError(f"Target without a marker: {url}")
        targets.append((url, marker.strip()))
    return targets


def outcome(job: dict[str, Any], marker: str) -> str:
    """`ok`, or the reason the expected page did not come back."""
    if job["status"] == "failed":
        return str(job["error"] or "failed").split(":")[0]
    result = job["result"]
    if result["blocked"]:
        return str(result["block_reason"])
    if result["status_code"] >= 400:
        return f"http_{result['status_code']}"
    return "ok" if marker.encode() in base64.b64decode(result["body_b64"]) else "marker_missing"


def run_job(client: httpx.Client, payload: dict[str, Any]) -> dict[str, Any]:
    """Submit one fetch, wait out rate limits, and poll until the job is terminal."""
    while True:
        response = client.post("/v1/fetch", json=payload)
        if response.status_code != 429:
            break
        time.sleep(float(response.headers["Retry-After"]))
    job_id = response.raise_for_status().json()["job_id"]
    deadline = time.monotonic() + JOB_TIMEOUT_S
    while time.monotonic() < deadline:
        job: dict[str, Any] = client.get(f"/v1/jobs/{job_id}").raise_for_status().json()
        if job["status"] in ("completed", "failed"):
            return job
        time.sleep(0.5)
    raise TimeoutError(f"Job {job_id} did not finish in {JOB_TIMEOUT_S} s; is the worker running?")


def percentile(values: list[float], q: float) -> float:
    """Nearest rank: with a few samples, an interpolated value was never measured."""
    return sorted(values)[math.ceil(q * len(values)) - 1]


def _counts(values: Iterable[str]) -> str:
    return ", ".join(f"{name} {count}" for name, count in sorted(Counter(values).items())) or "-"


def render(rows: list[dict[str, Any]]) -> str:
    """Markdown: one line per mode, then successes per target and mode."""
    modes = list(dict.fromkeys(row["mode"] for row in rows))
    lines = [
        "| Mode | OK | p50 s | p95 s | Attempts/job | Delivered by | Failures |",
        "|---|---|---|---|---|---|---|",
    ]
    for mode in modes:
        jobs = [row for row in rows if row["mode"] == mode]
        good = [row for row in jobs if row["outcome"] == "ok"]
        times = [row["seconds"] for row in good]
        latency = "- | -"
        if times:
            latency = f"{percentile(times, 0.5):.2f} | {percentile(times, 0.95):.2f}"
        attempts = sum(row["attempts"] for row in jobs) / len(jobs)
        engines = _counts(row["engine"] for row in good)
        failures = _counts(row["outcome"] for row in jobs if row["outcome"] != "ok")
        lines.append(
            f"| {mode} | {len(good)}/{len(jobs)} | {latency} | {attempts:.1f} "
            f"| {engines} | {failures} |"
        )
    lines += ["", "| Target | " + " | ".join(modes) + " |", "|---" * (len(modes) + 1) + "|"]
    for target in dict.fromkeys(row["target"] for row in rows):
        cells = []
        for mode in modes:
            hits = [
                row["outcome"] == "ok"
                for row in rows
                if row["target"] == target and row["mode"] == mode
            ]
            cells.append(f"{sum(hits)}/{len(hits)}")
        lines.append(f"| {target} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets", type=Path, default=TARGETS)
    parser.add_argument("--modes", default="static,stealth,browser,camoufox")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--no-proxy", action="store_true", help="send use_proxy=false")
    parser.add_argument("--key-file", type=Path, default=Path("/run/crawler-secrets/admin.key"))
    args = parser.parse_args()

    targets = read_targets(args.targets)
    headers = {"X-API-Key": args.key_file.read_text().strip()}
    rows = []
    with httpx.Client(base_url="http://127.0.0.1:8000", headers=headers, timeout=30) as client:
        for run in range(1, args.runs + 1):
            for url, marker in targets:
                for mode in args.modes.split(","):
                    # A learned domain policy would move this mode's starting tier.
                    payload = {"url": url, "mode": mode, "use_policy": False}
                    if args.no_proxy:
                        payload["use_proxy"] = False
                    job = run_job(client, payload)
                    history = client.get(f"/v1/fetches/{job['job_id']}").raise_for_status().json()
                    row = {
                        "target": url,
                        "mode": mode,
                        "outcome": outcome(job, marker),
                        "attempts": len(history["attempts"]),
                    }
                    if row["outcome"] == "ok":
                        created = datetime.fromisoformat(job["created_at"])
                        finished = datetime.fromisoformat(job["completed_at"])
                        row["seconds"] = (finished - created).total_seconds()
                        row["engine"] = job["result"]["engine"]
                    rows.append(row)
                    print(f"run {run}/{args.runs} {mode} {url} {row['outcome']}", file=sys.stderr)
    print(render(rows))


if __name__ == "__main__":
    main()
