"""Guards the tooling itself: if imports or settings break, every later
integration test fails with a confusing error instead of this one."""


def test_app_imports() -> None:
    from app.main import app

    assert app.title == "Crawler API"


def test_settings_defaults() -> None:
    from app.core.config import settings

    assert settings.api_port == 8000
    assert settings.ssrf_enabled is True
    # No keys configured by default; STEP 6 replaces this with DB-backed keys.
    assert settings.api_keys == []
