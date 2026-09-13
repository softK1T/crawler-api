"""Logging context must be contextvars-based — concurrent jobs must not leak."""

import asyncio
import importlib
from typing import Any

import pytest
import structlog


def _logging_config_source() -> str:
    module = importlib.import_module("app.core.logging_config")
    assert module.__file__ is not None
    with open(module.__file__, encoding="utf-8") as f:
        return f.read()


def test_merge_contextvars_processor_is_configured():
    """Without merge_contextvars, bind_contextvars never reaches log lines."""
    assert "structlog.contextvars.merge_contextvars" in _logging_config_source()


def test_no_process_global_shared_context():
    """The old process-global _shared_context leaked job_id/application_id
    between concurrent arq jobs through the stdlib bridge."""
    assert "_shared_context" not in _logging_config_source()


@pytest.mark.asyncio
async def test_concurrent_jobs_do_not_leak_bound_context():
    from app.core.logging_config import bind_context, clear_context

    async def _job(job_id: str) -> dict:
        clear_context()
        bind_context(job_id=job_id, application_id=f"app-{job_id}", trace_id=f"t-{job_id}")
        await asyncio.sleep(0.02)  # interleave the two jobs
        return dict(structlog.contextvars.get_contextvars())

    results = await asyncio.gather(_job("job-a"), _job("job-b"))

    assert results[0]["job_id"] == "job-a"
    assert results[0]["application_id"] == "app-job-a"
    assert results[0]["trace_id"] == "t-job-a"
    assert results[1]["job_id"] == "job-b"
    assert results[1]["application_id"] == "app-job-b"
    assert results[1]["trace_id"] == "t-job-b"


@pytest.mark.asyncio
async def test_clear_context_removes_bound_values():
    from app.core.logging_config import bind_context, clear_context

    clear_context()
    bind_context(job_id="job-x", application_id="app-x")
    assert structlog.contextvars.get_contextvars().get("job_id") == "job-x"

    clear_context()
    assert structlog.contextvars.get_contextvars().get("job_id") is None
    assert structlog.contextvars.get_contextvars().get("application_id") is None


def test_stdlib_bridge_merges_extra_fields_and_redacts(monkeypatch):
    """extra={...} fields must survive the stdlib bridge (Defect 2 regression).

    Previously emit() built the payload only from getMessage()/name/level and
    contextvars — extra fields like job_id/proxy_id/outcome were silently
    dropped from ``request_attempt_persist_failed`` diagnostics.
    """
    import io
    import json
    import logging
    import sys

    from app.core.logging_config import _StructlogHandler

    record = logging.LogRecord(
        name="app.services.request_attempt_log",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="request_attempt_persist_failed",
        args=(),
        exc_info=None,
    )
    record.__dict__["job_id"] = "job-extra-1"
    record.__dict__["outcome"] = "network_error"
    record.__dict__["proxy_line"] = "1.2.3.4:8080:user:supersecret"

    stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stream)
    handler = _StructlogHandler()
    handler.emit(record)

    payload = json.loads(stream.getvalue())
    assert payload["event"] == "request_attempt_persist_failed"
    assert payload["job_id"] == "job-extra-1"
    assert payload["outcome"] == "network_error"
    # Credentials in extra fields are redacted like the message.
    assert "supersecret" not in payload["proxy_line"]
    assert "1.2.3.4:8080:***:***" in payload["proxy_line"]


def test_stdlib_bridge_redacts_credentials_in_nested_extra(monkeypatch):
    """Credentials inside dict/list extra values must be redacted too.

    Regression guard: redacting only string-valued extra fields left nested
    secrets exposed — json.dumps(default=str) renders dicts/lists verbatim.
    """
    import io
    import json
    import logging
    import sys

    from app.core.logging_config import _StructlogHandler

    record = logging.LogRecord(
        name="probe",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="nested_leak_check",
        args=(),
        exc_info=None,
    )
    record.__dict__["detail"] = {"proxy_url": "http://user:secret@1.2.3.4:6754"}
    record.__dict__["items"] = ["http://u:p@5.6.7.8:8080"]

    stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stream)
    _StructlogHandler().emit(record)

    raw = stream.getvalue()
    payload = json.loads(raw)
    assert "secret" not in raw
    assert "u:p@5.6.7.8" not in raw
    assert payload["detail"]["proxy_url"] == "http://***:***@1.2.3.4:6754"
    assert payload["items"] == ["http://***:***@5.6.7.8:8080"]


def test_stdlib_bridge_extra_fields_win_over_bound_context(monkeypatch):
    """Explicitly logged fields override bound contextvars."""
    import io
    import json
    import logging
    import sys

    from app.core.logging_config import _StructlogHandler, bind_context, clear_context

    bind_context(job_id="job-from-context")
    try:
        record = logging.LogRecord(
            name="probe",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="probe",
            args=(),
            exc_info=None,
        )
        record.__dict__["job_id"] = "job-from-extra"

        stream = io.StringIO()
        monkeypatch.setattr(sys, "stderr", stream)
        _StructlogHandler().emit(record)
        payload = json.loads(stream.getvalue())
        assert payload["job_id"] == "job-from-extra"
    finally:
        clear_context()


def _emit_payload(monkeypatch, **extra: object) -> dict[str, Any]:
    """Emit one record through the stdlib bridge and return the parsed JSON."""
    import io
    import json
    import logging
    import sys

    from app.core.logging_config import _StructlogHandler

    record = logging.LogRecord(
        name="probe",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="scalar_probe",
        args=(),
        exc_info=None,
    )
    record.__dict__.update(extra)
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stream)
    _StructlogHandler().emit(record)
    payload = json.loads(stream.getvalue())
    assert isinstance(payload, dict)
    return payload


def test_stdlib_bridge_preserves_json_native_scalar_types(monkeypatch):
    """JSON-native scalars in extra={...} must not be stringified.

    Regression: _redact_value fell through to str() for every non-str/
    non-dict/non-list value, so status_code=200 was logged as the STRING
    "200" — breaking jq selects, Loki metric queries and alerting on
    numeric fields.  Types are asserted explicitly because equality is too
    weak in Python: True == 1 is True, so a bool/int mix-up passes a naive
    equality assertion.
    """
    payload = _emit_payload(
        monkeypatch,
        status_code=200,
        blocked=False,
        duration_ms=617,
        bytes_received=0,
        proxy_id=None,
    )
    assert isinstance(payload["status_code"], int)
    assert payload["status_code"] == 200
    assert payload["blocked"] is False
    assert isinstance(payload["duration_ms"], int)
    assert payload["duration_ms"] == 617
    assert isinstance(payload["bytes_received"], int)
    assert payload["bytes_received"] == 0
    assert payload["proxy_id"] is None


def test_stdlib_bridge_keeps_bool_not_int(monkeypatch):
    """Booleans must stay booleans — bool subclasses int, so `is` is the check."""
    payload = _emit_payload(monkeypatch, blocked=False, retriable=True)
    assert payload["blocked"] is False
    assert payload["retriable"] is True


def test_stdlib_bridge_nested_redaction_preserves_types(monkeypatch):
    """Credentials inside nested dicts are redacted without type coercion."""
    payload = _emit_payload(
        monkeypatch,
        detail={"proxy_url": "http://user:secret@1.2.3.4:6754", "attempt": 2, "ok": True},
    )
    assert payload["detail"]["proxy_url"] == "http://***:***@1.2.3.4:6754"
    assert isinstance(payload["detail"]["attempt"], int)
    assert payload["detail"]["attempt"] == 2
    assert payload["detail"]["ok"] is True


def test_stdlib_bridge_redacts_credentials_in_dict_keys(monkeypatch):
    """A credential carried in a dict KEY must be redacted like a value."""
    payload = _emit_payload(
        monkeypatch,
        headers={"http://user:secret@1.2.3.4:6754": "value"},
    )
    assert "http://user:secret@1.2.3.4:6754" not in payload["headers"]
    assert "http://***:***@1.2.3.4:6754" in payload["headers"]


def test_stdlib_bridge_uuid_round_trips_as_string(monkeypatch):
    """Non-JSON-native scalars (UUID) are stringified, never dropped."""
    import uuid

    job_uuid = uuid.uuid4()
    payload = _emit_payload(monkeypatch, job_uuid=job_uuid)
    assert isinstance(payload["job_uuid"], str)
    assert payload["job_uuid"] == str(job_uuid)


def test_stdlib_bridge_depth_guard_on_self_referential_dict(monkeypatch):
    """Self-referential structures hit the depth limit, not RecursionError."""
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    payload = _emit_payload(monkeypatch, cyclic=cyclic)
    # The depth limit stringifies at _MAX_REDACT_DEPTH — walk to the
    # innermost "self" node and require it to be a string.  Without the
    # guard, emit() raises RecursionError and json.loads fails here.
    node = payload["cyclic"]
    while isinstance(node, dict):
        node = node["self"]
    assert isinstance(node, str)
