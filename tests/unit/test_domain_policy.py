"""Unit tests for domain policy resolution."""

from app.services.policy_resolver import get_policy_defaults, normalize_domain


def test_normalize_www_ceneo():
    assert normalize_domain("www.ceneo.pl") == "ceneo.pl"


def test_normalize_subdomain():
    assert normalize_domain("m.allegro.pl") == "allegro.pl"


def test_normalize_ip_fallback():
    result = normalize_domain("192.168.1.1")
    assert result == "192.168.1.1"


def test_get_policy_defaults():
    defaults = get_policy_defaults()
    assert defaults["engine"] == "httpx"
    assert defaults["rate_limit_rps"] == 1.0


async def test_unknown_domain_returns_none(db_session):
    from app.services.policy_resolver import resolve_policy

    result = await resolve_policy("https://this-domain-does-not-exist-12345.com/page", db_session)
    assert result is None


async def test_upsert_policy_creates_and_updates(db_session):
    from sqlalchemy import select

    from app.models.domain_policy import DomainPolicy
    from app.services.policy_resolver import upsert_policy

    await upsert_policy("example.com", {"engine": "httpx", "rate_limit_rps": 2.0}, db_session)

    stmt = select(DomainPolicy).where(DomainPolicy.domain == "example.com")
    result = await db_session.execute(stmt)
    row = result.scalar_one()
    assert row.rate_limit_rps == 2.0

    # Update same domain.
    await upsert_policy("example.com", {"rate_limit_rps": 5.0}, db_session)
    await db_session.refresh(row)
    assert row.rate_limit_rps == 5.0
