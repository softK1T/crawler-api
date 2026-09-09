"""Webshare.io proxy discovery — plan-aware, multi-plan sync.

Webshare splits proxies into independent "plans" (e.g. one shared/default
datacenter plan, one shared/isp static-residential plan). The plain
``/api/v2/proxy/list/`` endpoint without ``plan_id`` only returns the
default plan's proxies — any other active plan is silently invisible.

This module discovers all active plans via ``/api/v2/subscription/plan/``,
then fetches proxies per plan_id, mapping each plan's ``proxy_subtype``
to our internal ``ProxyType`` so a single ``WEBSHARE_API_KEY`` is enough
to pull every proxy the account has, correctly typed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.services.proxy_providers.base import RawProxy

logger = logging.getLogger(__name__)

WEBSHARE_PLAN_URL = "https://proxy.webshare.io/api/v2/subscription/plan/"
WEBSHARE_LIST_URL = "https://proxy.webshare.io/api/v2/proxy/list/"

# Webshare plan.proxy_subtype -> our internal ProxyType (app/models/proxy.py).
# "isp" is Webshare's name for static residential proxies; we treat it as
# "residential" for escalation-tier purposes (ADR-019 ladder only knows
# datacenter/residential/mobile). If Webshare ever returns a genuine
# rotating "residential" subtype, it maps 1:1.
_SUBTYPE_TO_PROXY_TYPE: dict[str, str] = {
    "isp": "residential",
    "residential": "residential",
    "default": "datacenter",
}

# Webshare plan subtypes known to return per-proxy host:port via mode=direct.
# Rotating residential pools generally do NOT expose stable per-proxy
# addresses this way and need the backbone gateway (p.webshare.io) instead —
# that requires session-in-username targeting, which is a different proxy
# model than "one row per IP". We skip those here rather than guess wrong.
_DIRECT_CAPABLE_SUBTYPES = {"isp", "default"}


@dataclass(frozen=True)
class WebsharePlan:
    id: int
    status: str
    proxy_type: str
    proxy_subtype: str


def list_webshare_plans(api_key: str, timeout: float = 30.0) -> list[WebsharePlan]:
    """Fetch all subscription plans for this Webshare account (all pages)."""
    headers = {"Authorization": f"Token {api_key}"}
    plans: list[WebsharePlan] = []
    url: str | None = WEBSHARE_PLAN_URL
    params: dict[str, object] | None = {"page": 1, "page_size": 100}

    with httpx.Client(timeout=timeout) as client:
        while url:
            resp = client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()
            for p in data.get("results", []):
                plans.append(
                    WebsharePlan(
                        id=p["id"],
                        status=p.get("status", "unknown"),
                        proxy_type=p.get("proxy_type", ""),
                        proxy_subtype=p.get("proxy_subtype", "default"),
                    )
                )
            url = data.get("next")
            params = None  # `next` already carries page/page_size as query params

    logger.info("Webshare: discovered %d plan(s) total", len(plans))
    return plans


def _internal_proxy_type(plan: WebsharePlan) -> str:
    return _SUBTYPE_TO_PROXY_TYPE.get(plan.proxy_subtype, "datacenter")


def fetch_webshare_proxies_for_plan(
    api_key: str,
    plan: WebsharePlan,
    page_size: int = 100,
    timeout: float = 30.0,
) -> list[RawProxy]:
    """Fetch every proxy belonging to a single Webshare plan."""
    if plan.proxy_subtype not in _DIRECT_CAPABLE_SUBTYPES:
        logger.warning(
            "Webshare: skipping plan_id=%s subtype=%s — not direct-capable "
            "(likely rotating residential; needs backbone gateway support)",
            plan.id,
            plan.proxy_subtype,
        )
        return []

    proxy_type = _internal_proxy_type(plan)
    headers = {"Authorization": f"Token {api_key}"}
    results: list[RawProxy] = []
    page = 1

    with httpx.Client(timeout=timeout) as client:
        while True:
            params = {
                "mode": "direct",
                "page": page,
                "page_size": page_size,
                "plan_id": plan.id,
            }
            resp = client.get(WEBSHARE_LIST_URL, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()

            for item in data.get("results", []):
                host = item.get("proxy_address") or ""
                port = item.get("port") or ""
                username = item.get("username") or ""
                password = item.get("password") or ""
                if not (host and port and username and password):
                    continue
                country = (item.get("country_code") or "").upper()[:2] or None
                city = item.get("city_name") or None
                results.append(
                    RawProxy(
                        url=f"http://{username}:{password}@{host}:{port}",
                        country=country,
                        proxy_type=proxy_type,
                        city=city,
                    )
                )

            logger.info(
                "Webshare: plan_id=%s subtype=%s page=%d fetched=%d (running total=%d)",
                plan.id,
                plan.proxy_subtype,
                page,
                len(data.get("results", [])),
                len(results),
            )

            if not data.get("next"):
                break
            page += 1

    return results


def fetch_all_webshare_proxies(api_key: str) -> list[RawProxy]:
    """Discover all active plans and fetch proxies for each.

    Fail-soft per plan: one plan erroring out does not abort the others.
    Plan discovery itself is not fail-soft — without it we cannot know
    what to fetch at all.
    """
    plans = list_webshare_plans(api_key)
    active_plans = [p for p in plans if p.status == "active"]

    if not active_plans:
        logger.warning("Webshare: no active plans found for this account")
        return []

    all_proxies: dict[str, RawProxy] = {}
    for plan in active_plans:
        try:
            proxies = fetch_webshare_proxies_for_plan(api_key, plan)
        except httpx.HTTPError:
            logger.exception(
                "Webshare: failed to fetch proxies for plan_id=%s subtype=%s — skipping",
                plan.id,
                plan.proxy_subtype,
            )
            continue

        for proxy in proxies:
            # If the same endpoint somehow appears under two plans, prefer
            # the residential classification (more capable tier).
            existing = all_proxies.get(proxy.url)
            if existing is None or (
                existing.proxy_type != "residential" and proxy.proxy_type == "residential"
            ):
                all_proxies[proxy.url] = proxy

    logger.info(
        "Webshare sync: %d unique proxies across %d active plan(s)",
        len(all_proxies),
        len(active_plans),
    )
    return list(all_proxies.values())
