"""Unit tests for detect_block_reason and detect_vendor."""

from __future__ import annotations

from app.schemas.fetch import BlockReason
from app.services.block_detector import detect_block_reason, detect_vendor


def test_429_is_rate_limited():
    assert detect_block_reason(429, {}, b"") == BlockReason.RATE_LIMITED


def test_cloudflare_ray_header():
    assert detect_block_reason(403, {"cf-ray": "abc123"}, b"") == BlockReason.CLOUDFLARE


def test_cloudflare_body_pattern():
    body = b"<title>Attention Required! | Cloudflare</title>"
    assert detect_block_reason(200, {}, body) == BlockReason.CLOUDFLARE


def test_captcha_hcaptcha():
    body = b'<div class="h-captcha" data-sitekey="xxx"></div>'
    assert detect_block_reason(200, {}, body) == BlockReason.CAPTCHA


def test_captcha_recaptcha():
    body = b'<div class="g-recaptcha"></div>'
    assert detect_block_reason(200, {}, body) == BlockReason.CAPTCHA


def test_ip_ban_403():
    assert detect_block_reason(403, {}, b"some page") == BlockReason.IP_BAN


def test_ip_ban_explicit_body():
    body = b"Your IP address has been blocked."
    assert detect_block_reason(200, {}, body) == BlockReason.IP_BAN


def test_200_clean_page_returns_none():
    assert detect_block_reason(200, {}, b"<html><body>Hello world</body></html>") is None


def test_generic_bot_word_not_enough():
    body = b"<p>We use robot vacuum cleaners. Buy our bot today!</p>"
    assert detect_block_reason(200, {}, body) is None


def test_cloudflare_via_cf_ray():
    assert detect_vendor(200, {"cf-ray": "xyz"}, {}, b"") == "cloudflare"


def test_cloudflare_via_cookie():
    assert detect_vendor(200, {}, {"__cf_bm": "val"}, b"") == "cloudflare"


def test_cloudflare_server_header():
    assert detect_vendor(200, {"server": "cloudflare"}, {}, b"") == "cloudflare"


def test_akamai_via_abck_cookie():
    assert detect_vendor(200, {}, {"_abck": "val"}, b"") == "akamai"


def test_akamai_via_bm_sz_cookie():
    assert detect_vendor(200, {}, {"bm_sz": "val"}, b"") == "akamai"


def test_datadome_via_cookie():
    assert detect_vendor(200, {}, {"datadome": "val"}, b"") == "datadome"


def test_datadome_via_body():
    body = b'<script src="https://geo.captcha-delivery.com/captcha.js"></script>'
    assert detect_vendor(200, {}, {}, body) == "datadome"


def test_kasada_via_header():
    assert detect_vendor(200, {"x-kpsdk-ct": "val"}, {}, b"") == "kasada"


def test_perimeterx_via_cookie():
    assert detect_vendor(200, {}, {"_px2": "val"}, b"") == "perimeterx"


def test_perimeterx_via_body():
    body = b'<div id="px-captcha"></div>'
    assert detect_vendor(200, {}, {}, body) == "perimeterx"


def test_incapsula_via_cookie():
    assert detect_vendor(200, {}, {"incap_ses_123_456": "val"}, b"") == "incapsula"


def test_incapsula_via_header():
    assert detect_vendor(200, {"x-iinfo": "12-3456-7"}, {}, b"") == "incapsula"


def test_aws_waf_via_cookie():
    assert detect_vendor(200, {}, {"aws-waf-token": "val"}, b"") == "aws_waf"


def test_no_vendor_clean_response():
    assert detect_vendor(200, {"server": "nginx"}, {}, b"<html>clean</html>") is None


def test_cloudflare_wins_over_akamai():
    headers = {"cf-ray": "x", "x-akamai-session-id": "y"}
    assert detect_vendor(200, headers, {}, b"") == "cloudflare"
