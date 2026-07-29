"""Logging redaction tests — verify structlog + redact still works."""

import io
import logging

from app.core.logging_config import _StructlogHandler, redact
from app.core.security import generate_api_key


def test_redacts_proxy_line_credentials() -> None:
    out = redact("Acquired proxy: 45.12.33.8:8080:jdoe:s3cr3tpw")
    assert "s3cr3tpw" not in out
    assert "jdoe" not in out
    assert "45.12.33.8:8080" in out


def test_redacts_proxy_url_credentials() -> None:
    out = redact("proxy=http://jdoe:s3cr3tpw@45.12.33.8:8080")
    assert "s3cr3tpw" not in out
    assert "http://***:***@45.12.33.8:8080" in out


def test_redacts_api_key_secret_but_keeps_prefix() -> None:
    out = redact("auth failed for crw_live_ab12cd34_ZZZsupersecretZZZ")
    assert "ZZZsupersecretZZZ" not in out
    assert "crw_live_ab12cd34_***" in out


def test_log_redaction_covers_generated_key_format() -> None:
    """Redaction must cover the actual crwl/crwt format from generate_api_key()."""
    import sys

    stream = io.StringIO()
    handler = _StructlogHandler()

    for mode in ("live", "test"):
        raw_key, _hashed = generate_api_key(mode=mode)
        prefix = raw_key[:8]

        # Emit a log record containing the raw key — this is the worst case
        # that must be caught by redaction.
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=0,
            msg=f"key=%s issued",
            args=(raw_key,),
            exc_info=None,
        )

        # Redirect stderr to capture the handler's output.
        old_stderr = sys.stderr
        sys.stderr = stream
        try:
            handler.emit(record)
        finally:
            sys.stderr = old_stderr

        output = stream.getvalue()
        stream.truncate(0)
        stream.seek(0)

        # Assertions — order matters: check non-empty first.
        assert output.strip(), f"Log output is empty for mode={mode}"
        assert raw_key not in output, (
            f"Raw key leaked into log output for mode={mode}"
        )
        assert "***" in output, (
            f"Redaction marker absent — log may be suppressed, mode={mode}"
        )
        # The prefix (non-sensitive, returned in API responses) may appear.
        assert prefix in output, (
            f"Prefix {prefix!r} should be preserved in log output for mode={mode}"
        )
