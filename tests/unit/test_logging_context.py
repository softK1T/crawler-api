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
