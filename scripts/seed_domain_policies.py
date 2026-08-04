#!/usr/bin/env python3
"""Seed domain policies from scripts/data/domains.txt.

Idempotent: safe to re-run. Uses INSERT ... ON CONFLICT DO NOTHING so that
learned fields (escalation_tier, antibot_type, tier_locked) on existing rows
are never clobbered.

Usage:
    DATABASE_URL=postgresql+asyncpg://... python scripts/seed_domain_policies.py
    python scripts/seed_domain_policies.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from urllib.parse import urlparse

import structlog
import tldextract
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).parent.parent
DOMAINS_FILE = REPO_ROOT / "scripts" / "data" / "domains.txt"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.INFO))
log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Domain normalization
# ---------------------------------------------------------------------------
_extract = tldextract.TLDExtract(cache_dir=None, suffix_list_urls=())

# Explicit country overrides for ambiguous domains whose TLD doesn't reveal
# the market (e.g. .com domains that operate in a single non-US market).
_COUNTRY_OVERRIDES: dict[str, str] = {
    "chewy.com": "US",  # US pet retailer
    "wayfair.com": "US",
    "walmart.com": "US",
    "target.com": "US",
    "bestbuy.com": "US",
    "newegg.com": "US",
    "costco.com": "US",
    "cvs.com": "US",
    "walgreens.com": "US",
    "verizon.com": "US",
    "att.com": "US",
    "t-mobile.com": "US",
    "amazon.sg": "SG",
    "amazon.ae": "AE",
    "talabat.com": "AE",
    "namshi.com": "AE",
    "faces.com": "AE",
    "noon.com": "AE",
    "shopee.com.my": "MY",
    "shopee.com.sg": "SG",
    "shopee.co.th": "TH",
    "shopee.vn": "VN",
    "shopee.ph": "PH",
    "shopee.co.id": "ID",
    "lazada.com.my": "MY",
    "lazada.sg": "SG",
    "lazada.co.th": "TH",
    "lazada.com.ph": "PH",
    "tokopedia.com": "ID",
    "bukalapak.com": "ID",
    "flipkart.com": "IN",
    "myntra.com": "IN",
    "snapdeal.com": "IN",
    "jd.com": "CN",
    "tmall.com": "CN",
    "taobao.com": "CN",
    "pinduoduo.com": "CN",
    "rakuten.co.jp": "JP",
    "qoo10.sg": "SG",
    "kogan.com": "AU",
    "catch.com.au": "AU",
    "mercadolibre.com.ar": "AR",
    "mercadolibre.com.mx": "MX",
    "mercadolibre.com.br": "BR",
    "rappi.com": "CO",
    "rappi.com.co": "CO",
    "falabella.com": "CL",
    "falabella.com.co": "CO",
    "sodimac.com": "CL",
    "coppel.com": "MX",
    "walmart.com.mx": "MX",
    "amazon.com.mx": "MX",
    "amazon.com.br": "BR",
    "amazon.com.au": "AU",
    "pricesmart.com": "US",  # operates in Central America/Caribbean but HQ US
    "jumia.com.ng": "NG",
    "jumia.co.ke": "KE",
}

# TLD → ISO-3166-1 alpha-2 for unambiguous ccTLDs.
_TLD_TO_COUNTRY: dict[str, str] = {
    "de": "DE",
    "fr": "FR",
    "pl": "PL",
    "es": "ES",
    "it": "IT",
    "nl": "NL",
    "se": "SE",
    "be": "BE",
    "co.uk": "GB",
    "uk": "GB",
    "ru": "RU",
    "ua": "UA",
    "ro": "RO",
    "bg": "BG",
    "cz": "CZ",
    "sk": "SK",
    "ae": "AE",
    "ng": "NG",
    "ke": "KE",
    "co.jp": "JP",
    "jp": "JP",
    "in": "IN",
    "com.au": "AU",
    "com.br": "BR",
    "com.mx": "MX",
    "com.ar": "AR",
}


def normalize_domain(raw: str) -> str | None:
    """Normalize a raw entry (URL or hostname) to a registered domain.

    Rules:
    1. If it looks like a URL (contains '://'), parse via urlparse.
    2. Strip leading 'www.' and any path component (split on '/').
    3. Apply tldextract; return registered_domain.
    4. Return None if normalization fails (log and skip).
    """
    raw = raw.strip()
    if not raw or raw.startswith("#"):
        return None
    # Handle URL-style entries like 'bol.com/nl' or 'chewy.com/ca'
    if "://" in raw:
        parsed = urlparse(raw)
        host = parsed.hostname or ""
    else:
        host = raw.split("/")[0]  # strip any path suffix
    host = host.lower().removeprefix("www.")
    extracted = _extract(host)
    return extracted.registered_domain or host or None


def country_for(domain: str) -> str | None:
    if domain in _COUNTRY_OVERRIDES:
        return _COUNTRY_OVERRIDES[domain]
    extracted = _extract(domain)
    suffix = extracted.suffix  # e.g. 'co.uk', 'com.au', 'de'
    return _TLD_TO_COUNTRY.get(suffix)


def load_domains(path: Path) -> list[str]:
    """Load, normalize, deduplicate domains preserving first-seen order."""
    seen: set[str] = set()
    result: list[str] = []
    for line in path.read_text().splitlines():
        domain = normalize_domain(line)
        if domain and domain not in seen:
            seen.add(domain)
            result.append(domain)
    return result


# ---------------------------------------------------------------------------
# DB seed
# ---------------------------------------------------------------------------


async def seed(domains: list[str], *, dry_run: bool, database_url: str) -> None:
    from app.models.domain_policy import DomainPolicy

    engine = create_async_engine(database_url, echo=False)
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    inserted = 0
    skipped = 0

    async with async_session() as session:
        for domain in domains:
            country = country_for(domain)
            defaults: dict = {
                "domain": domain,
                "is_active": True,
                "engine": "httpx",
                "rate_limit_rps": 0.5,
                "min_delay_ms": 2000,
                "max_delay_ms": 8000,
                "max_retries": 3,
                "respect_robots": True,
                "sticky_session": False,
                "use_proxy": True,
                "proxy_country": country,
                "proxy_type": "datacenter",
                "escalation_tier": 0,
                "tier_locked": False,
                "antibot_type": None,
                "consecutive_blocks": 0,
            }
            if dry_run:
                log.info("dry_run", domain=domain, country=country)
                inserted += 1
                continue

            # INSERT ... ON CONFLICT DO NOTHING — never clobbers learned fields.
            stmt = (
                insert(DomainPolicy)
                .values(**defaults)
                .on_conflict_do_nothing(index_elements=["domain"])
            )
            result: CursorResult = await session.execute(stmt)  # type: ignore[assignment]
            if result.rowcount:
                inserted += 1
                log.info("inserted", domain=domain, country=country)
            else:
                skipped += 1
                log.debug("exists_skip", domain=domain)

        if not dry_run:
            await session.commit()

    await engine.dispose()
    log.info("seed_complete", inserted=inserted, skipped=skipped, total=len(domains))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed domain policies")
    parser.add_argument("--dry-run", action="store_true", help="Print without writing")
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override DATABASE_URL env var",
    )
    args = parser.parse_args()

    import os

    database_url = args.database_url or os.environ.get("DATABASE_URL")
    if not database_url and not args.dry_run:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    domains = load_domains(DOMAINS_FILE)
    log.info("loaded_domains", count=len(domains), file=str(DOMAINS_FILE))

    asyncio.run(seed(domains, dry_run=args.dry_run, database_url=database_url or ""))


if __name__ == "__main__":
    main()
