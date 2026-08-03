"""Block detection with high-confidence pattern matching — no loose keywords.

All fetchers call :func:`detect_block_reason` instead of the old
``_detect_block`` in ``base.py``.  Generic words such as "bot", "robot",
"blocked", and "captcha" are deliberately ignored — ecommerce pages
frequently contain them.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from app.schemas.fetch import BlockReason

_CAPTCHA_PATTERNS = (
    re.compile(rb"\bg-recaptcha\b", re.I),
    re.compile(rb"\bhcaptcha\b", re.I),
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
