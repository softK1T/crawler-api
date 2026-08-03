"""Unit tests for fetchers — block detection, redirect validation, headers."""

from app.services.block_detector import detect_block_reason


def test_detect_captcha():
    reason = detect_block_reason(200, {}, b"<html><div class='g-recaptcha'></div></html>")
    assert reason is not None
    assert reason == "captcha"


def test_detect_cloudflare_by_header():
    blocked = detect_block_reason(403, {"cf-ray": "abc-WAW"}, b"")
    assert blocked is not None
    assert blocked == "cloudflare"


def test_detect_ip_ban():
    blocked = detect_block_reason(403, {}, b"")
    assert blocked is not None
    assert blocked == "ip_ban"


def test_detect_rate_limited():
    blocked = detect_block_reason(429, {}, b"")
    assert blocked is not None
    assert blocked == "rate_limited"


def test_detect_non_blocked():
    blocked = detect_block_reason(200, {}, b"<html>normal page</html>")
    assert blocked is None


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
