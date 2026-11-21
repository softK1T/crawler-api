"""Typed rows from an OLX listing page: a category or a search result.

Works on HTML the fetch API returned; the service itself stays fetch-only.
Seller names, contact details and ad descriptions are left out on purpose.
"""

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class Listing(BaseModel):
    listing_id: int
    title: str
    url: str
    price: Decimal | None
    currency: str | None
    negotiable: bool | None
    free: bool
    exchange: bool
    condition: str | None
    city: str
    region: str
    business: bool
    # A paid slot; the same ad can appear once more in the organic list below it.
    promoted: bool
    created_at: datetime
    refreshed_at: datetime
    collected_at: datetime


def parse_listings(html: str, *, collected_at: datetime) -> tuple[list[Listing], int]:
    """Every ad on the page in page order, repeats included, and how many pages the listing has."""
    try:
        # The page carries its data as JSON inside a JavaScript string literal.
        start = html.index('"', html.index("window.__PRERENDERED_STATE__"))
        literal, _ = json.JSONDecoder().raw_decode(html, start)
        listing = json.loads(literal)["listing"]["listing"]
        ads, pages = listing["ads"], listing["totalPages"]
    except (ValueError, KeyError) as exc:
        # A block page has no state at all; the home page and an ad page have no listing in it.
        raise ValueError("no listing data on the page") from exc
    return [_listing(ad, collected_at) for ad in ads], pages


def _listing(ad: dict[str, Any], collected_at: datetime) -> Listing:
    # Jobs and services have no price object; free and exchange ads have one without a price.
    price = ad["price"] or {}
    regular = price.get("regularPrice") or {}
    return Listing(
        listing_id=ad["id"],
        title=ad["title"],
        url=ad["url"],
        price=regular.get("value"),
        currency=regular.get("currencyCode"),
        negotiable=regular.get("negotiable"),
        free=price.get("free", False),
        exchange=price.get("exchange", False),
        condition=next((p["normalizedValue"] for p in ad["params"] if p["key"] == "state"), None),
        city=ad["location"]["cityName"],
        region=ad["location"]["regionName"],
        business=ad["isBusiness"],
        promoted=ad["searchReason"] == "promoted",
        created_at=ad["createdTime"],
        refreshed_at=ad["lastRefreshTime"],
        collected_at=collected_at,
    )
