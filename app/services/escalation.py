"""Escalation ladder — pure logic, no I/O, fully unit-testable.

The ladder defines an ordered sequence of (engine, proxy_type, use_proxy)
tiers from cheapest to most expensive.  The retry loop in fetch_with_retry
walks up the ladder when a block reason is in ESCALATABLE.

Design decisions — see docs/decisions/ADR-019-escalation-ladder.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.schemas.fetch import BlockReason

if TYPE_CHECKING:
    from app.models.domain_policy import DomainPolicy

logger = logging.getLogger(__name__)

MAX_ATTEMPTS_PER_TIER = 2


# ── Tier definition ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Tier:
    """A single rung in the escalation ladder.

    Attributes:
        engine:     Fetcher engine name passed to get_fetcher().
        proxy_type: Required proxy type, or None for direct (no proxy).
        use_proxy:  Whether a proxy is required at this tier.
    """

    engine: str
    proxy_type: str | None
    use_proxy: bool


# ── The ladder ───────────────────────────────────────────────────────────────

LADDER: tuple[Tier, ...] = (
    # Tier 0 — direct, no proxy.  Baseline; works for ~60% of open domains.
    Tier(engine="httpx", proxy_type=None, use_proxy=False),
    # Tier 1 — httpx + datacenter proxy.  Rotates IP without engine cost.
    Tier(engine="httpx", proxy_type="datacenter", use_proxy=True),
    # Tier 2 — curl_cffi + datacenter.  TLS fingerprint mimics a real browser;
    #           bypasses most Cloudflare JS challenges without a full browser.
    Tier(engine="curl_cffi", proxy_type="datacenter", use_proxy=True),
    # Tier 3 — curl_cffi + residential.  Exit IP looks like a home user;
    #           defeats datacenter-range IP blocks (Akamai, DataDome).
    #           PREMIUM — gated behind enable_premium_proxy_tiers.
    Tier(engine="curl_cffi", proxy_type="residential", use_proxy=True),
    # Tier 4 — playwright + residential.  Full browser JS execution; required
    #           for Kasada and PerimeterX interactive challenges.
    #           PREMIUM — gated behind enable_premium_proxy_tiers.
    Tier(engine="playwright", proxy_type="residential", use_proxy=True),
    # Tier 5 — camoufox + residential.  Firefox-based with humanize=True;
    #           strongest browser fingerprint camouflage vs Akamai/Kasada.
    #           PREMIUM — gated behind enable_premium_proxy_tiers.
    Tier(engine="camoufox", proxy_type="residential", use_proxy=True),
    # Tier 6 — camoufox + mobile proxy.  Mobile exit IP + humanised Firefox;
    #           last resort for the hardest targets (JD.com, Shopee SEA).
    #           PREMIUM — gated behind enable_premium_proxy_tiers.
    Tier(engine="camoufox", proxy_type="mobile", use_proxy=True),
)

# Index of the first premium tier (proxy_type in {residential, mobile}).
_FIRST_PREMIUM_TIER: int = next(
    i for i, t in enumerate(LADDER) if t.proxy_type in {"residential", "mobile"}
)

# ── Anti-bot vendor floor map ─────────────────────────────────────────────────

ANTIBOT_FLOOR: dict[str, int] = {
    # cloudflare: tier 2 because curl_cffi's TLS fingerprint bypasses
    #             JS challenges without spending a browser.  Tier 0/1 (httpx)
    #             always gets a 403 from CF's Bot Fight mode.
    "cloudflare": 2,
    # akamai: tier 3.  Akamai Bot Manager v2+ blocks datacenter ranges
    #         aggressively; residential IP is the minimum viable entry.
    "akamai": 3,
    # datadome: tier 3.  DataDome primarily blocks datacenter ranges and
    #           performs device fingerprinting that curl_cffi handles poorly.
    "datadome": 3,
    # kasada: tier 4.  Kasada requires real browser JS execution to generate
    #         x-kpsdk-* tokens; curl_cffi cannot produce them.
    "kasada": 4,
    # perimeterx: tier 4.  Same reasoning as Kasada — interactive JS proof.
    "perimeterx": 4,
    # incapsula: tier 2.  TLS + cookie-based; curl_cffi handles it well.
    "incapsula": 2,
    # aws_waf: tier 1.  AWS WAF Bot Control is IP-range based by default;
    #          rotating to a datacenter proxy is usually sufficient.
    "aws_waf": 1,
    # custom_sea (Shopee): tier 6.  Shopee SEA uses a proprietary stack
    #                       that requires mobile proxy + humanised browser.
    "custom_sea": 6,
    # custom_cn (JD/Tmall): tier 5.  CN stacks are strict but residential
    #                        Firefox is usually sufficient; mobile reserved
    #                        as manual escalation for edge cases.
    "custom_cn": 5,
    # none / unknown: start from tier 0, let the ladder discover.
    "none": 0,
}

# ── Escalatable block reasons ─────────────────────────────────────────────────

ESCALATABLE: frozenset[BlockReason] = frozenset(
    {
        # Vendor-specific challenges: a different engine/proxy_type can bypass them.
        BlockReason.CLOUDFLARE,
        BlockReason.WAF,
        BlockReason.CAPTCHA,
        # Vendor reasons added in Phase 3 (block_detector.detect_vendor).
        # Listed here by value so they work even before the enum is extended,
        # using _missing_ fallback in BlockReason.
    }
)

# Reasons that only justify IP rotation (same engine, new proxy):
# RATE_LIMITED — we're just hitting rate limits; a fresh IP fixes it.
# IP_BAN       — our specific IP is blocked; rotating proxy resolves it.
# OTHER        — unknown; conservative choice is to rotate, not escalate.
# Escalating engine for these would burn expensive tiers unnecessarily.
_ROTATION_ONLY: frozenset[BlockReason] = frozenset(
    {
        BlockReason.RATE_LIMITED,
        BlockReason.IP_BAN,
        BlockReason.OTHER,
    }
)


def is_escalatable(reason: str | None) -> bool:
    """Return True if *reason* justifies bumping the escalation tier."""
    if reason is None:
        return False
    # Compare by string value directly: BlockReason._missing_ silently maps
    # any unknown string to OTHER, so BlockReason(reason) never raises.
    # Instead, check if the raw string matches a known non-escalatable value;
    # anything not explicitly excluded escalates conservatively.
    non_escalatable_values = {br.value for br in BlockReason} - {br.value for br in ESCALATABLE}
    if reason in non_escalatable_values:
        return False
    # Known escalatable value OR unknown future vendor string — escalate.
    return True


# ── Tier resolution ───────────────────────────────────────────────────────────


def initial_tier(policy: DomainPolicy | None) -> int:
    """Return the starting escalation tier for *policy*.

    Respects:
    1. policy.escalation_tier (learned from previous successes).
    2. ANTIBOT_FLOOR[policy.antibot_type] (known vendor minimum).
    The higher of the two wins so we never waste attempts below the floor.
    """
    if policy is None:
        return 0
    learned = policy.escalation_tier or 0
    floor = ANTIBOT_FLOOR.get(policy.antibot_type or "none", 0) if policy.antibot_type else 0
    return max(learned, floor)


def next_tier(current: int) -> int | None:
    """Return the next tier index, or None if already at the top."""
    nxt = current + 1
    if nxt >= len(LADDER):
        return None
    return nxt


def effective_max_tier(enable_premium: bool) -> int:
    """Highest tier index reachable given the premium flag."""
    if enable_premium:
        return len(LADDER) - 1
    return _FIRST_PREMIUM_TIER - 1


def tier_for(policy: DomainPolicy | None, *, enable_premium: bool = False) -> Tier:
    """Return the current Tier dataclass for *policy*.

    Clamps to effective_max_tier when premium tiers are disabled.
    Emits a structured warning when clamping occurs.
    """
    idx = initial_tier(policy)
    max_idx = effective_max_tier(enable_premium)
    if idx > max_idx:
        logger.warning(
            "escalation_tier_clamped",
            extra={
                "requested_tier": idx,
                "clamped_to": max_idx,
                "domain": getattr(policy, "domain", "unknown"),
                "reason": "enable_premium_proxy_tiers=False",
            },
        )
        idx = max_idx
    return LADDER[idx]
