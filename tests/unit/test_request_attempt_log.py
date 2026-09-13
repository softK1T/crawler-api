"""Unit tests for per-attempt persistence service — no database required."""

import asyncio
import logging
import uuid
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.request_attempt_log import (
    MAX_ERROR_LENGTH,
    ProxySnapshot,
    RequestAttempt,
    error_class_name,
    persist_request_attempt,
    proxy_snapshot,
    sanitize_error_message,
)


def _attempt(**overrides) -> RequestAttempt:
    fields: dict[str, Any] = {
        "job_id": "job-1",
        "api_key_id": None,
        "application_id": None,
        "trace_id": "job-1",
        "url": "https://example.com/page",
        "domain": "example.com",
        "method": "GET",
        "attempt_number": 1,
        "tier_attempt_number": 1,
        "escalation_tier": 0,
        "proxy_id": None,
        "proxy_pool_id": None,
        "proxy_provider": None,
        "proxy_type": None,
        "proxy_country": None,
        "proxy_city": None,
        "engine": "httpx",
        "outcome": "success",
        "status_code": 200,
        "duration_ms": 42,
        "bytes_received": 123,
        "blocked": False,
        "block_reason": None,
        "error_type": None,
        "error": None,
        "requested_at": datetime.now(UTC),
        "completed_at": datetime.now(UTC),
    }
    fields.update(overrides)
    return RequestAttempt(**fields)


# ── proxy_snapshot: safe metadata, never the URL ─────────────────────────────


def test_proxy_snapshot_omits_proxy_url_and_credentials():
    """The snapshot must not copy proxy.url — credentials live in the URL."""
    from app.models.proxy import Proxy, ProxyType

    proxy = Proxy(
        id=uuid.uuid4(),
        pool_id=uuid.uuid4(),
        provider="webshare",
        url="http://user:supersecret@10.0.0.7:8080",
        country="PL",
        city="Warsaw",
        proxy_type=ProxyType.datacenter,
    )

    snap = proxy_snapshot(proxy)

    assert isinstance(snap, ProxySnapshot)
    assert snap.proxy_id == proxy.id
    assert snap.proxy_pool_id == proxy.pool_id
    assert snap.provider == "webshare"
    assert snap.proxy_type == "datacenter"
    assert snap.country == "PL"
    assert snap.city == "Warsaw"
    assert not hasattr(snap, "url")
    assert "supersecret" not in repr(snap)
    assert "10.0.0.7" not in repr(snap)


def test_proxy_snapshot_none_proxy():
    snap = proxy_snapshot(None)
    assert snap == ProxySnapshot(None, None, None, None, None, None)


def test_proxy_snapshot_plain_string_proxy_type():
    proxy = SimpleNamespace(
        id=uuid.uuid4(),
        pool_id=uuid.uuid4(),
        provider="webshare",
        url="http://u:s@10.0.0.7:8080",
        country=None,
        city=None,
        proxy_type="residential",
    )
    snap = proxy_snapshot(proxy)
    assert snap.proxy_type == "residential"


# ── Sanitization ─────────────────────────────────────────────────────────────


def test_sanitize_error_message_truncates_long_messages():
    long_message = "x" * (MAX_ERROR_LENGTH + 500)
    out = sanitize_error_message(long_message)
    assert len(out) == MAX_ERROR_LENGTH
    assert out.endswith("...")


def test_sanitize_error_message_keeps_short_messages():
    assert sanitize_error_message("boom") == "boom"


def test_error_class_name_is_truncated_to_column_width():
    class ThisNameIsFarTooLongForTheErrorTypeColumnForSure(Exception):
        pass

    out = error_class_name(ThisNameIsFarTooLongForTheErrorTypeColumnForSure())
    assert len(out) <= 128
    assert out.startswith("ThisNameIsFarTooLong")


# ── Immutability ─────────────────────────────────────────────────────────────


def test_request_attempt_is_frozen():
    attempt = _attempt()
    with pytest.raises(FrozenInstanceError):
        attempt.outcome = "blocked"  # type: ignore[misc]


# ── Persistence failure must never raise ─────────────────────────────────────


@pytest.mark.asyncio
async def test_persist_request_attempt_returns_none_on_db_failure(caplog):
    """A broken DB factory → None, failure metric, structured log, no raise."""
    from prometheus_client import REGISTRY

    def _broken_factory():
        raise RuntimeError("connection refused host=10.9.9.9 port=5432")

    before = REGISTRY.get_sample_value("crawler_request_log_write_failures_total") or 0
    with caplog.at_level(logging.ERROR, logger="app.services.request_attempt_log"):
        result = await persist_request_attempt(
            _broken_factory,
            _attempt(url="https://example.com/?token=supersecret", outcome="network_error"),
        )
    after = REGISTRY.get_sample_value("crawler_request_log_write_failures_total") or 0

    assert result is None
    assert after - before == 1
    assert "request_attempt_persist_failed" in caplog.text
    # The attempt URL and its query secret must never reach the logs.
    assert "supersecret" not in caplog.text
    assert "token=" not in caplog.text


# ── Outcome classification helpers (base.py) ─────────────────────────────────


def test_classify_fetch_error_and_exception():
    from app.services.fetchers.base import (
        FetchError,
        classify_exception,
        classify_fetch_error,
        proxy_failure_reason,
    )

    assert classify_fetch_error(FetchError("ssrf")) == "fetch_error"
    assert classify_fetch_error(FetchError("blocked", blocked=True)) == "blocked"
    assert classify_exception(TimeoutError()) == "network_error"
    assert classify_exception(ConnectionError("refused")) == "network_error"
    assert classify_exception(RuntimeError("boom")) == "internal_error"
    assert proxy_failure_reason(TimeoutError()) == "timeout"
    assert proxy_failure_reason(asyncio.CancelledError()) == "timeout"
    assert proxy_failure_reason(RuntimeError("boom")) == "http_error"
