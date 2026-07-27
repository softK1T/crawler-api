"""Structured JSON logging with credential redaction and bound context.

Uses structlog with JSONRenderer for consistent single-line JSON output.
Standard library logging is captured and rendered as JSON too.
"""

import json
import logging
import re
from typing import Any

import structlog

# ── Credential redaction ─────────────────────────────────────────────────────
_PROXY_LINE = re.compile(r"\b((?:\d{1,3}\.){3}\d{1,3}|[\w.-]+):(\d{2,5}):[^:\s]+:[^:\s]+")
_PROXY_URL = re.compile(r"(https?|socks5)://[^:/@\s]+:[^:/@\s]+@")
_API_KEY = re.compile(r"\b(crw_(?:live|test)_[A-Za-z0-9]{8})_[A-Za-z0-9_-]+")


def redact(text: str) -> str:
    text = _PROXY_LINE.sub(r"\1:\2:***:***", text)
    text = _PROXY_URL.sub(r"\1://***:***@", text)
    return _API_KEY.sub(r"\1_***", text)


# ── Structlog configuration ──────────────────────────────────────────────────
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer(serializer=json.dumps),
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

_shared_context: dict[str, str] = {}


def bind_context(
    *,
    trace_id: str | None = None,
    job_id: str | None = None,
    application_id: str | None = None,
) -> None:
    """Set thread-local context values for structured logging."""
    ctx: dict[str, str] = {}
    if trace_id:
        ctx["trace_id"] = trace_id
    if job_id:
        ctx["job_id"] = job_id
    if application_id:
        ctx["application_id"] = application_id
    _shared_context.update(ctx)
    structlog.contextvars.bind_contextvars(**ctx)


def get_logger(name: str = "crawler-api"):
    return structlog.get_logger(name)


# ── Stdlib bridge ────────────────────────────────────────────────────────────


class _StructlogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        msg = redact(record.getMessage())
        logger = structlog.get_logger(record.name)
        kw: dict[str, Any] = {**_shared_context}
        if record.exc_info:
            kw["exc_info"] = record.exc_info
        logger.log(record.levelno, msg, **kw)


def configure_logging(level: str = "INFO") -> None:
    """Idempotent root-logger setup for structlog and stdlib bridge."""
    handler = _StructlogHandler()
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        uv = logging.getLogger(name)
        uv.handlers.clear()
        uv.propagate = True
