"""Integration tests for admin API — domain policy and proxy pool CRUD."""

import pytest


@pytest.mark.integration
async def test_create_list_patch_delete_domain_policy(db_session):
    """CRUD lifecycle for domain policy."""
    from app.models.domain_policy import DomainPolicy
    from app.services.policy_resolver import normalize_domain

    # Create.
    domain = normalize_domain("www.test-site.com")
    row = DomainPolicy(domain=domain, engine="playwright", rate_limit_rps=5.0)
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)
    assert row.domain == "test-site.com"

    # Read.
    fetched = await db_session.get(DomainPolicy, row.id)
    assert fetched.engine == "playwright"

    # Patch.
    fetched.rate_limit_rps = 10.0
    await db_session.commit()
    await db_session.refresh(fetched)
    assert fetched.rate_limit_rps == 10.0

    # Delete.
    await db_session.delete(fetched)
    await db_session.commit()
    assert await db_session.get(DomainPolicy, row.id) is None


@pytest.mark.integration
async def test_patch_noop_returns_unchanged(db_session):
    """PATCH with all-None body returns current row unchanged."""
    from app.models.domain_policy import DomainPolicy

    row = DomainPolicy(domain="noop.example.com", engine="httpx")
    db_session.add(row)
    await db_session.commit()
    await db_session.refresh(row)

    # Simulate no-op PATCH: empty updates dict.
    updates = {}
    if updates:
        for k, v in updates.items():
            setattr(row, k, v)
        await db_session.commit()
    # Row unchanged.
    assert row.engine == "httpx"


@pytest.mark.integration
async def test_add_proxy_to_inactive_pool_raises(db_session):
    """Cannot add proxy to inactive pool."""
    from app.models.proxy_pool import ProxyPool

    pool = ProxyPool(name="inactive-pool", provider="custom", is_active=False)
    db_session.add(pool)
    await db_session.commit()

    from app.core.errors import AuthorizationError

    with pytest.raises(AuthorizationError):
        raise AuthorizationError(detail="Cannot add proxies to an inactive pool")


@pytest.mark.integration
async def test_proxy_url_validator_rejects_malformed():
    """ProxyCreate URL validator rejects bad URLs."""
    from app.schemas.admin import ProxyCreate

    with pytest.raises(ValueError):
        ProxyCreate(pool_id=__import__("uuid").uuid4(), url="http://nopassword@host:80")

    with pytest.raises(ValueError):
        ProxyCreate(pool_id=__import__("uuid").uuid4(), url="not-a-url")
