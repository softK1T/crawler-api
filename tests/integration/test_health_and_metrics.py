"""Integration tests for health and metrics endpoints."""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.integration
async def test_healthz_always_200(app):
    """/healthz always returns 200."""
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.integration
async def test_readyz_checks_dependencies(app):
    """/readyz returns 200 or 503 depending on dependency health."""
    client = TestClient(app)
    response = client.get("/readyz")
    data = response.json()
    assert "checks" in data
    assert "db" in data["checks"]
    assert "redis" in data["checks"]


@pytest.mark.integration
async def test_metrics_endpoint_returns_200(app):
    """/metrics endpoint is reachable."""
    from app.core.config import settings

    client = TestClient(app)
    response = client.get(settings.metrics_path)
    assert response.status_code == 200


@pytest.mark.integration
async def test_metrics_names_exposed(app):
    """crawler_block_rate_total and crawler_queue_depth appear in metrics."""
    from app.core.observability import BLOCK_RATE_TOTAL, QUEUE_DEPTH

    # Increment once so metric appears in output.
    BLOCK_RATE_TOTAL.labels(domain="test", engine="httpx", reason="captcha").inc()
    QUEUE_DEPTH.labels(queue_name="arq:crawler").set(5)

    from prometheus_client import generate_latest

    body = generate_latest().decode()
    assert "crawler_block_rate_total" in body
    assert "crawler_queue_depth" in body
