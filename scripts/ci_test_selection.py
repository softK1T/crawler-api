"""CI-only selection of tests that do not require live services."""

from pathlib import Path

import pytest

RESOURCE_FIXTURES = frozenset(
    {
        "_postgres_dsn",
        "_redis_url",
        "db_session",
        "db_session_factory",
        "redis_client",
        "app",
        "tenant_factory",
        "application_factory",
        "api_key_factory",
    }
)


def requires_services(item) -> bool:
    parts = Path(str(item.path)).parts
    return bool(
        "integration" in parts
        or "e2e" in parts
        or item.get_closest_marker("integration") is not None
        or RESOURCE_FIXTURES.intersection(getattr(item, "fixturenames", ()))
    )


def pytest_addoption(parser) -> None:
    parser.addoption("--ci-unit-only", action="store_true", default=False)


def pytest_collection_modifyitems(config, items) -> None:
    if not config.getoption("--ci-unit-only"):
        return
    selected = [item for item in items if not requires_services(item)]
    deselected = [item for item in items if requires_services(item)]
    config.hook.pytest_deselected(items=deselected)
    items[:] = selected


@pytest.hookimpl(tryfirst=True)
def pytest_fixture_setup(fixturedef, request) -> None:
    if request.config.getoption("--ci-unit-only") and fixturedef.argname in RESOURCE_FIXTURES:
        raise pytest.UsageError(
            "Live-service fixture requested in fast CI; mark the test as integration"
        )
