"""JSON logging with credential redaction.

Proxy lines carry the shape host:port:user:password and were passed straight to
``logger.debug``/``logger.warning`` in the crawler and pool code. Redacting at
the handler level covers every current and future call site, which patching
individual log statements does not.

No new dependency: stdlib ``json`` plus a ``logging.Filter`` is enough, so
python-json-logger / structlog are not pulled in.
"""

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

# host:port:user:pass[:COUNTRY] — keep host:port, drop the credentials.
_PROXY_LINE = re.compile(r"\b((?:\d{1,3}\.){3}\d{1,3}|[\w.-]+):(\d{2,5}):[^:\s]+:[^:\s]+")
# scheme://user:pass@host
_PROXY_URL = re.compile(r"(https?|socks5)://[^:/@\s]+:[^:/@\s]+@")
# API keys: crw_live_<prefix>_<secret> (format introduced in STEP 6)
_API_KEY = re.compile(r"\b(crw_(?:live|test)_[A-Za-z0-9]{8})_[A-Za-z0-9_-]+")

_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__)


def redact(text: str) -> str:
    text = _PROXY_LINE.sub(r"\1:\2:***:***", text)
    text = _PROXY_URL.sub(r"\1://***:***@", text)
    return _API_KEY.sub(r"\1_***", text)


class RedactionFilter(logging.Filter):
    """Redacts the formatted message and any string values in `extra`."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
        except Exception:
            pass
        for key, value in list(record.__dict__.items()):
            if key not in _RESERVED and isinstance(value, str):
                record.__dict__[key] = redact(value)
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line, suitable for Loki/CloudWatch ingestion."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Idempotent root-logger setup. Safe to call from API and worker entrypoints."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn installs its own colourised handlers; drop them so output stays JSON.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        uv = logging.getLogger(name)
        uv.handlers.clear()
        uv.propagate = True
