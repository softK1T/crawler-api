"""Unit tests for detect_vendor() in app.services.block_detector.

One fixture per vendor + negative fixture (normal page with 'captcha' in body).
"""

from __future__ import annotations

from app.services.block_detector import detect_vendor

# ---------------------------------------------------------------------------
# Positive fixtures
# ---------------------------------------------------------------------------


def test_cloudflare_cf_ray():
    assert detect_vendor(200, {"cf-ray": "abc123-WAW"}, {}, b"") == "cloudflare"


def test_cloudflare_cf_bm_cookie():
    assert detect_vendor(200, {}, {"__cf_bm": "xyz"}, b"") == "cloudflare"


def test_akamai_abck_cookie():
    assert detect_vendor(200, {}, {"_abck": "abc"}, b"") == "akamai"


def test_akamai_bm_sz_cookie():
    assert detect_vendor(200, {}, {"bm_sz": "abc"}, b"") == "akamai"


def test_akamai_header():
    assert detect_vendor(200, {"x-akamai-transformed": "9 -"}, {}, b"") == "akamai"


def test_datadome_cookie():
    assert detect_vendor(200, {}, {"datadome": "abc"}, b"") == "datadome"


def test_datadome_body():
    body = b'<html><script src="https://geo.captcha-delivery.com/captcha/"></script></html>'
    assert detect_vendor(200, {}, {}, body) == "datadome"


def test_kasada_header():
    assert detect_vendor(200, {"x-kpsdk-ct": "abc"}, {}, b"") == "kasada"


def test_perimeterx_cookie():
    assert detect_vendor(200, {}, {"_px2": "abc"}, b"") == "perimeterx"


def test_perimeterx_header():
    assert detect_vendor(200, {"x-px-block": "1"}, {}, b"") == "perimeterx"


def test_incapsula_cookie():
    assert detect_vendor(200, {}, {"incap_ses_123_456": "abc"}, b"") == "incapsula"


def test_incapsula_header():
    assert detect_vendor(200, {"x-iinfo": "3-abc"}, {}, b"") == "incapsula"


def test_aws_waf_cookie():
    assert detect_vendor(200, {}, {"aws-waf-token": "abc"}, b"") == "aws_waf"


def test_aws_waf_header():
    assert detect_vendor(200, {"x-amzn-waf-action": "BLOCK"}, {}, b"") == "aws_waf"


# ---------------------------------------------------------------------------
# Negative fixture: normal page with "captcha" in body must NOT be flagged
# ---------------------------------------------------------------------------


def test_normal_page_with_captcha_word_not_flagged():
    """A checkout page with reCAPTCHA widget but no vendor cookies/headers."""
    body = b"""
    <html><body>
    <h1>Complete your purchase</h1>
    <div class="g-recaptcha" data-sitekey="6Le..."></div>
    <p>Please complete the captcha to continue.</p>
    </body></html>
    """
    result = detect_vendor(200, {}, {}, body)
    assert result is None


def test_no_vendor_signals_returns_none():
    assert detect_vendor(200, {"content-type": "text/html"}, {}, b"<html>Hello</html>") is None
