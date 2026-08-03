"""Tests for high-confidence block detector."""

import pytest

from app.schemas.fetch import BlockReason
from app.services.block_detector import detect_block_reason


def test_normal_large_html_is_not_blocked() -> None:
    body = (
        b"<!DOCTYPE html><html><body>"
        + b"<p>A normal product catalogue.</p>" * 500
        + b"</body></html>"
    )
    assert detect_block_reason(200, {"content-type": "text/html"}, body) is None


def test_generic_robot_word_is_not_enough() -> None:
    body = b"<html><a href='/robots.txt'>robots</a></html>"
    assert detect_block_reason(200, {}, body) is None


def test_specific_captcha_markup() -> None:
    body = b"<html><div class='g-recaptcha'></div></html>"
    assert detect_block_reason(200, {}, body) is BlockReason.CAPTCHA


def test_cloudflare_header() -> None:
    assert detect_block_reason(403, {"cf-ray": "abc-WAW"}, b"") is BlockReason.CLOUDFLARE


def test_rate_limit() -> None:
    assert detect_block_reason(429, {}, b"") is BlockReason.RATE_LIMITED


def test_plain_403() -> None:
    assert detect_block_reason(403, {}, b"Forbidden") is BlockReason.IP_BAN


@pytest.mark.parametrize("status", [400, 404, 500, 502])
def test_other_http_errors(status: int) -> None:
    assert detect_block_reason(status, {}, b"") is BlockReason.OTHER
