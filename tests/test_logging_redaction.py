"""Logging redaction tests — verify structlog + redact still works."""

from app.core.logging_config import redact


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
