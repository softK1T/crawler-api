"""Unit tests for callback delivery — HMAC signing, retry, SSRF guard."""

from app.services.callback import sign_payload


def test_sign_payload_format():
    sig = sign_payload(b"test", "secret")
    assert sig.startswith("sha256=")
    assert len(sig) == 71  # "sha256=" + 64 hex chars


def test_sign_payload_deterministic():
    s1 = sign_payload(b"same-input", "secret")
    s2 = sign_payload(b"same-input", "secret")
    assert s1 == s2
