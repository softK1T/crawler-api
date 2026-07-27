import json
import logging

from app.core.logging_config import JsonFormatter, RedactionFilter, redact


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


def test_filter_applies_to_lazy_formatting() -> None:
    record = logging.LogRecord(
        "t",
        logging.WARNING,
        __file__,
        1,
        "Proxy blocked: %s",
        ("1.2.3.4:3128:user:pass",),
        None,
    )
    RedactionFilter().filter(record)
    assert "pass" not in record.getMessage()


def test_filter_applies_to_extra_fields() -> None:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "ok", None, None)
    record.proxy = "1.2.3.4:3128:user:pass"  # type: ignore[attr-defined]
    RedactionFilter().filter(record)
    assert "pass" not in record.proxy  # type: ignore[attr-defined]


def test_formatter_emits_valid_json_with_extras() -> None:
    record = logging.LogRecord("t", logging.ERROR, __file__, 1, "boom", None, None)
    record.domain = "ceneo.pl"  # type: ignore[attr-defined]
    parsed = json.loads(JsonFormatter().format(record))
    assert parsed["level"] == "ERROR"
    assert parsed["msg"] == "boom"
    assert parsed["domain"] == "ceneo.pl"
    assert parsed["ts"].endswith("+00:00")
