"""Block detection with high-confidence pattern matching — no loose keywords.

All fetchers call :func:`detect_block_reason` instead of the old
``_detect_block`` in ``base.py``.  Generic words such as "bot", "robot",
"blocked", and "captcha" are deliberately ignored — ecommerce pages
frequently contain them.

Phase 3 adds :func:`detect_vendor` which identifies the anti-bot *vendor*
present on a site, independent of whether the response is a block.  It must
run on every response including HTTP 200, because vendor presence is a
property of the site, not of the failure.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from app.schemas.fetch import BlockReason

_CAPTCHA_PATTERNS = (
    re.compile(rb"\bg-recaptcha\b", re.I),
    re.compile(rb"h-?captcha", re.I),
    re.compile(rb"\bcaptcha-container\b", re.I),
    re.compile(rb"\bverify you are human\b", re.I),
)

_CLOUDFLARE_PATTERNS = (
    re.compile(rb"\bcf-chl-", re.I),
    re.compile(rb"\bcloudflare ray id\b", re.I),
    re.compile(rb"\battention required!\s*\|\s*cloudflare\b", re.I),
)

_IP_BAN_PATTERNS = (
    re.compile(rb"\byour ip (?:address )?has been (?:blocked|banned)\b", re.I),
    re.compile(rb"\baccess from your ip has been denied\b", re.I),
    re.compile(rb"\btemporarily blocked due to suspicious activity\b", re.I),
)

_WAF_PATTERNS = (
    re.compile(rb"\brequest (?:was )?rejected\b", re.I),
    re.compile(rb"\bweb application firewall\b", re.I),
    re.compile(rb"\bsecurity policy has blocked\b", re.I),
)

# ── Vendor body patterns (body-scan fallback only) ────────────────────────────
# Only used when header/cookie checks are inconclusive.  Bounded to first 64 KB.
_DATADOME_BODY = re.compile(rb"\bgeo\.captcha-delivery\.com\b", re.I)
_KASADA_BODY = re.compile(rb"\bkasada\b", re.I)
_PERIMETERX_BODY = re.compile(rb"\bpx-captcha\b|\b_pxCaptcha\b", re.I)
_INCAPSULA_BODY = re.compile(rb"\bincapsula incident id\b", re.I)

_VENDOR_BODY_SLICE = 65_536  # 64 KB — cheap upper bound for body regex


def detect_block_reason(
    status_code: int,
    headers: Mapping[str, str],
    body: bytes,
) -> BlockReason | None:
    """Classify only high-confidence block pages.

    Generic words such as "bot", "robot", "blocked" and "captcha" are not
    sufficient on their own because normal ecommerce pages contain them.
    """
    normalized_headers = {str(k).lower(): str(v) for k, v in headers.items()}
    sample = body[:1_000_000]

    if status_code == 429:
        return BlockReason.RATE_LIMITED

    if (
        "cf-ray" in normalized_headers
        or "cloudflare" in normalized_headers.get("server", "").lower()
        or any(pattern.search(sample) for pattern in _CLOUDFLARE_PATTERNS)
    ):
        return BlockReason.CLOUDFLARE

    if any(pattern.search(sample) for pattern in _CAPTCHA_PATTERNS):
        return BlockReason.CAPTCHA

    if any(pattern.search(sample) for pattern in _IP_BAN_PATTERNS):
        return BlockReason.IP_BAN

    if any(pattern.search(sample) for pattern in _WAF_PATTERNS):
        return BlockReason.WAF

    if status_code in {401, 403, 407, 451}:
        return BlockReason.IP_BAN

    if status_code >= 400:
        return BlockReason.OTHER

    return None


def detect_vendor(
    status_code: int,
    headers: Mapping[str, str],
    cookies: Mapping[str, str],
    body: bytes,
) -> str | None:
    """Identify the anti-bot vendor protecting this site.

    Returns one of: cloudflare | akamai | datadome | kasada | perimeterx |
    incapsula | aws_waf — or None if no vendor is detected.

    Design constraints:
    - Runs on EVERY response (200s included) — vendor is a site property.
    - Header/cookie lookups first (O(1)); body regex only as fallback on a
      bounded 64 KB slice.
    - High-confidence only — no loose keyword matching.
    """
    nh = {k.lower(): v for k, v in headers.items()}
    nc = {k.lower(): v for k, v in cookies.items()}

    # ── Cloudflare ────────────────────────────────────────────────────────────
    # cf-ray is injected on every CF-proxied response; __cf_bm / cf_clearance
    # are CF bot management cookies present even on clean 200s.
    if (
        "cf-ray" in nh
        or "__cf_bm" in nc
        or "cf_clearance" in nc
        or nh.get("server", "").lower() == "cloudflare"
    ):
        return "cloudflare"

    # ── Akamai ────────────────────────────────────────────────────────────────
    # _abck / ak_bmsc / bm_sz are Akamai Bot Manager sensor cookies.
    # x-akamai-* headers appear on Akamai-fronted origins.
    if (
        "_abck" in nc
        or "ak_bmsc" in nc
        or "bm_sz" in nc
        or any(k.startswith("x-akamai-") for k in nh)
    ):
        return "akamai"

    # ── DataDome ──────────────────────────────────────────────────────────────
    # datadome cookie is always set; x-datadome-* headers on challenge pages.
    if (
        "datadome" in nc
        or any(k.startswith("x-datadome") for k in nh)
        or _DATADOME_BODY.search(body[:_VENDOR_BODY_SLICE])
    ):
        return "datadome"

    # ── Kasada ────────────────────────────────────────────────────────────────
    # x-kpsdk-* headers are injected by Kasada's edge logic.
    if any(k.startswith("x-kpsdk-") for k in nh):
        return "kasada"

    # ── PerimeterX / HUMAN ───────────────────────────────────────────────────
    # _px, _px2, _px3 are PerimeterX sensor cookies; x-px-* on challenge pages.
    if (
        "_px" in nc
        or "_px2" in nc
        or "_px3" in nc
        or any(k.startswith("x-px-") for k in nh)
        or _PERIMETERX_BODY.search(body[:_VENDOR_BODY_SLICE])
    ):
        return "perimeterx"

    # ── Incapsula / Imperva ───────────────────────────────────────────────────
    # incap_ses_* / visid_incap_* are Incapsula session cookies.
    # x-iinfo is the Incapsula request-info header.
    if (
        any(k.startswith("incap_ses") for k in nc)
        or any(k.startswith("visid_incap") for k in nc)
        or "x-iinfo" in nh
        or _INCAPSULA_BODY.search(body[:_VENDOR_BODY_SLICE])
    ):
        return "incapsula"

    # ── AWS WAF Bot Control ───────────────────────────────────────────────────
    # aws-waf-token cookie; x-amzn-waf-* headers on challenge/block pages.
    if "aws-waf-token" in nc or any(k.startswith("x-amzn-waf-") for k in nh):
        return "aws_waf"

    return None
