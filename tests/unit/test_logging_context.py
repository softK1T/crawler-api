"""Logging context must be contextvars-based — concurrent jobs must not leak."""

import asyncio
import importlib

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
