import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_example_adapter_login_persists_session(monkeypatch):
    saved = {}

    def _save(session_key: str, cookies: dict[str, str]) -> None:
        saved[session_key] = cookies

    monkeypatch.setattr("app.services.session_manager.save_session", _save)

    from app.services.adapters.example_site import ExampleSiteAdapter

    adapter = ExampleSiteAdapter("https://example.com/login")
    cookies = await adapter.login("alice", "secret")

    assert cookies is not None
    assert saved["example.com"] == cookies
    assert cookies["auth"] == "1"


@pytest.mark.asyncio
async def test_auth_session_domain_session_key_invalid_url():
    with pytest.raises(HTTPException):
        from app.api.v1.endpoints.auth import _domain_session_key

        _domain_session_key("not-a-url")
