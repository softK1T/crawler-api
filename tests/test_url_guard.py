import pytest

from app.core.url_guard import UrlNotAllowed, _check_static, validate_url_sync


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://example.com/",
        "ftp://example.com/x",
        "://broken",
    ],
)
def test_rejects_non_http_schemes(url: str) -> None:
    with pytest.raises(UrlNotAllowed):
        _check_static(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://0.0.0.0/",
        "http://100.64.0.1/",
    ],
)
def test_rejects_literal_private_ips(url: str) -> None:
    with pytest.raises(UrlNotAllowed):
        _check_static(url)


def test_rejects_metadata_hostname() -> None:
    with pytest.raises(UrlNotAllowed, match="metadata"):
        _check_static("http://metadata.google.internal/")


@pytest.mark.parametrize("url", ["http://example.com:6379/", "http://example.com:2375/"])
def test_rejects_non_web_ports(url: str) -> None:
    with pytest.raises(UrlNotAllowed, match="Port"):
        _check_static(url)


@pytest.mark.parametrize(
    "url",
    ["https://example.com/", "http://example.com:8080/x", "https://sub.example.pl:8443/a?b=1"],
)
def test_accepts_public_web_urls(url: str) -> None:
    host, port = _check_static(url)
    assert host
    assert port in {80, 443, 8080, 8443}


def test_unresolvable_host_is_rejected() -> None:
    with pytest.raises(UrlNotAllowed, match="DNS resolution failed"):
        validate_url_sync("http://this-host-does-not-exist-crawlerapi.invalid/")
