import pytest


def test_headers_for_domain_injects_cookie_from_session(monkeypatch):
    from app.services.fetchers.headers import headers_for_domain

    monkeypatch.setattr(
        "app.services.session_manager.load_session",
        lambda session_key: {"sid": "abc", "auth": "1"} if session_key == "example.com" else None,
    )

    headers = headers_for_domain(None, session_key="example.com")
    assert headers["Cookie"] in {"sid=abc; auth=1", "auth=1; sid=abc"}


def test_headers_for_domain_without_session_does_not_set_cookie(monkeypatch):
    from app.services.fetchers.headers import headers_for_domain

    monkeypatch.setattr("app.services.session_manager.load_session", lambda _session_key: None)
    headers = headers_for_domain(None, session_key="missing")
    assert "Cookie" not in headers


@pytest.mark.asyncio
async def test_get_adapter_returns_example_adapter():
    from app.services.adapters import get_adapter
    from app.services.adapters.example_site import ExampleSiteAdapter

    adapter = get_adapter("https://example.com/account")
    assert isinstance(adapter, ExampleSiteAdapter)
