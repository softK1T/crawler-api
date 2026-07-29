"""Structured JSON logging with credential redaction and bound context.

Uses structlog with JSONRenderer for consistent single-line JSON output.
Standard library logging is captured and rendered as JSON via a bridge that
writes directly to stderr — NOT back through stdlib logging — to avoid the
infinite recursion described in ADR-013.
"""

import json
import logging
import re
import sys
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
    """Bridge: format stdlib log records as JSON and write directly to stderr.

    Writes JSON directly to stderr rather than routing through structlog's
    LoggerFactory, which would feed back into the stdlib logging system and
    cause infinite recursion (ADR-013).
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            from datetime import UTC, datetime

            msg = redact(record.getMessage())
            payload: dict[str, Any] = {
                "event": msg,
                "logger": record.name,
                "level": record.levelname.lower(),
                "timestamp": datetime.now(UTC).isoformat(),
            }
            if record.exc_info:
                import traceback

                payload["exc_info"] = traceback.format_exception(*record.exc_info)
            # Merge any bound context.
            if _shared_context:
                payload.update(_shared_context)
            json_str = json.dumps(payload, default=str)
            sys.stderr.write(json_str + "\n")
            sys.stderr.flush()
        except Exception:
            self.handleError(record)


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
