"""Unit tests for fetchers — block detection, redirect validation, headers."""

from app.services.fetchers.base import _detect_block


def test_detect_captcha():
    blocked, reason = _detect_block(200, b"<html>captcha</html>")
    assert blocked is True
    assert reason == "captcha"


def test_detect_bot_detection():
    blocked, reason = _detect_block(200, b"cf-challenge detected")
    assert blocked is True
    assert reason == "bot_detection"


def test_detect_ip_ban():
    blocked, reason = _detect_block(403, b"")
    assert blocked is True
    assert reason == "ip_ban"


def test_detect_rate_limited():
    blocked, reason = _detect_block(429, b"")
    assert blocked is True
    assert reason == "rate_limited"


def test_detect_non_blocked():
    blocked, _reason = _detect_block(200, b"<html>normal page</html>")
    assert blocked is False


def test_get_fetcher_camoufox_maps_to_playwright():
    from app.services.fetchers import get_fetcher
    from app.services.fetchers.playwright_fetcher import PlaywrightFetcher

    fetcher = get_fetcher("camoufox")
    assert isinstance(fetcher, PlaywrightFetcher)


def test_headers_for_domain_merges():
    from app.services.fetchers.headers import headers_for_domain

    class FakePolicy:
        header_profile: dict[str, str] = {"User-Agent": "CustomAgent/1.0", "X-Extra": "yes"}  # noqa: RUF012

    headers = headers_for_domain(FakePolicy())
    assert headers["User-Agent"] == "CustomAgent/1.0"
    assert headers["X-Extra"] == "yes"
    assert "Accept" in headers


def test_base_headers_present():
    from app.services.fetchers.headers import headers_for_domain

    headers = headers_for_domain(None)
    assert "User-Agent" in headers
    assert "Accept" in headers
    assert "Accept-Language" in headers
