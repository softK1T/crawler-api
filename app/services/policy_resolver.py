"""Domain policy resolution with tldextract-based normalization.

``tldextract`` uses its bundled Public Suffix List snapshot — no runtime
network calls. The extractor is created once at module level.
"""

import logging
from urllib.parse import urlparse

import tldextract
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_extract_domain = tldextract.TLDExtract(
    cache_dir=None,
    suffix_list_urls=(),
)


def normalize_domain(hostname: str) -> str:
    """Normalize a hostname to a registered domain for policy lookup.

    1. Strip leading ``www.`` (case-insensitive).
    2. Apply ``tldextract.extract(hostname)`` → use ``registered_domain``.
    3. Fallback to stripped hostname if tldextract returns empty
       (IP addresses, localhost, single-label names).

    This is the single source of truth for domain normalization — callers in
    ``policy_resolver`` and ``rate_limiter`` both use this helper.
    """
    stripped = hostname.lower().removeprefix("www.")
    extracted = _extract_domain(stripped)
    return extracted.registered_domain or stripped


def _parse_hostname(url: str) -> str:
    """Extract and normalize the hostname from a URL string."""
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise ValueError(f"Cannot extract hostname from URL: {url!r}")
    return normalize_domain(host)


async def resolve_policy(url: str, db: AsyncSession):
    """Resolve the active ``DomainPolicy`` for a URL.

    Returns the ORM row or ``None`` if no policy is configured for the
    extracted domain.  Callers must fall back to :func:`get_policy_defaults`.
    """
    from app.models.domain_policy import DomainPolicy

    domain = _parse_hostname(url)
    stmt = (
        select(DomainPolicy)
        .where(DomainPolicy.domain == domain, DomainPolicy.is_active.is_(True))
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


def get_policy_defaults() -> dict:
    """Return default values matching ``DomainPolicy`` columns.

    Used when no policy row exists for a domain.
    """
    return {
        "engine": "httpx",
        "rate_limit_rps": 1.0,
        "min_delay_ms": 500,
        "max_delay_ms": 2000,
        "max_retries": 3,
        "respect_robots": True,
        "sticky_session": False,
        "use_proxy": False,
        "proxy_country": None,
        "header_profile": None,
        "proxy_pool_id": None,
    }


async def upsert_policy(domain: str, updates: dict, db: AsyncSession) -> None:
    """Insert or update a domain policy row using PostgreSQL ON CONFLICT.

    *domain* is normalized the same way as ``resolve_policy`` before the
    upsert (``www.`` stripped, tldextract registered domain).
    """
    from app.models.domain_policy import DomainPolicy

    domain_norm = normalize_domain(domain)
    stmt = (
        insert(DomainPolicy)
        .values(domain=domain_norm, **updates)
        .on_conflict_do_update(index_elements=["domain"], set_=updates)
    )
    await db.execute(stmt)
    await db.commit()
    logger.info("Upserted domain policy for %s", domain_norm)
