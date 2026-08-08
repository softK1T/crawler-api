from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.db import get_db
from app.models.proxy import Proxy
from app.models.proxy_event import ProxyEvent
from app.models.proxy_pool import ProxyPool


@pytest.mark.integration
async def test_get_proxy_events_requires_admin_scope(
    app, db_session, application_factory, api_key_factory
):
    app_obj = await application_factory()
    admin_key, _ = await api_key_factory(application=app_obj, scopes=["admin", "fetch"])
    fetch_key, _ = await api_key_factory(application=app_obj, scopes=["fetch"])

    pool = ProxyPool(name="events-pool", provider="webshare", is_active=True)
    db_session.add(pool)
    await db_session.flush()

    proxy = Proxy(
        pool_id=pool.id,
        provider="webshare",
        url="http://events-proxy:8080",
        country="PL",
        proxy_type="datacenter",
    )
    db_session.add(proxy)
    await db_session.flush()

    db_session.add_all(
        [
            ProxyEvent(
                proxy_id=proxy.id, event_type="deactivated", detail={"domain": "example.com"}
            ),
            ProxyEvent(proxy_id=proxy.id, event_type="activated", detail={"domain": "example.com"}),
        ]
    )
    await db_session.commit()

    app.dependency_overrides[get_db] = lambda: db_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            forbidden = await client.get(
                f"/proxy/proxies/{proxy.id}/events",
                headers={"X-API-Key": fetch_key},
            )
            assert forbidden.status_code == 403

            ok = await client.get(
                f"/proxy/proxies/{proxy.id}/events?limit=1",
                headers={"X-API-Key": admin_key},
            )
            assert ok.status_code == 200
            payload = ok.json()
            assert len(payload) == 1
            assert payload[0]["event_type"] == "activated"
    finally:
        app.dependency_overrides.clear()
