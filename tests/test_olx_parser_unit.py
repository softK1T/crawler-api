"""Offline tests for how an OLX listing page becomes typed rows."""

import json
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from scripts import olx_parser

COLLECTED = datetime(2026, 11, 21, 17, 0, tzinfo=UTC)


def _ad(**changes: Any) -> dict[str, Any]:
    ad: dict[str, Any] = {
        "id": 1001,
        "title": "Telefon przykładowy 128GB",
        "url": "https://www.olx.pl/d/oferta/telefon-przykladowy-CID99-ID1aB2c.html",
        "isBusiness": False,
        "createdTime": "2026-11-02T16:20:09+01:00",
        "lastRefreshTime": "2026-11-21T16:09:57+01:00",
        "searchReason": "organic",
        "params": [{"key": "state", "normalizedValue": "used"}],
        "price": {
            "free": False,
            "exchange": False,
            "regularPrice": {"value": 750, "currencyCode": "PLN", "negotiable": True},
        },
        "location": {"cityName": "Łódź", "regionName": "Łódzkie"},
    }
    return {**ad, **changes}


def _page(state: dict[str, Any]) -> str:
    """The state as OLX ships it: JSON inside a JavaScript string, with escaped slashes."""
    text = json.dumps(state, ensure_ascii=False).replace("/", "\\u002F")
    literal = json.dumps(text, ensure_ascii=False)
    return f"<script>window.__PRERENDERED_STATE__= {literal};\nwindow.__TAURUS__= [];</script>"


def _rows(*ads: dict[str, Any]) -> list[olx_parser.Listing]:
    page = _page({"listing": {"listing": {"ads": ads, "totalPages": 1}}})
    return olx_parser.parse_listings(page, collected_at=COLLECTED)[0]


class ParseListingsTests(unittest.TestCase):
    def test_ad_becomes_a_typed_row(self) -> None:
        (row,) = _rows(_ad())
        self.assertEqual(
            row.model_dump(),
            {
                "listing_id": 1001,
                "title": "Telefon przykładowy 128GB",
                "url": "https://www.olx.pl/d/oferta/telefon-przykladowy-CID99-ID1aB2c.html",
                "price": Decimal("750"),
                "currency": "PLN",
                "negotiable": True,
                "free": False,
                "exchange": False,
                "condition": "used",
                "city": "Łódź",
                "region": "Łódzkie",
                "business": False,
                "promoted": False,
                "created_at": datetime.fromisoformat("2026-11-02T16:20:09+01:00"),
                "refreshed_at": datetime.fromisoformat("2026-11-21T16:09:57+01:00"),
                "collected_at": COLLECTED,
            },
        )

    def test_ad_without_a_price_keeps_the_reason(self) -> None:
        free = _ad(price={"free": True, "exchange": False, "regularPrice": None})
        swap = _ad(price={"free": False, "exchange": True, "regularPrice": None})
        job = _ad(price=None, params=[])
        self.assertEqual(
            [
                (row.price, row.currency, row.negotiable, row.free, row.exchange, row.condition)
                for row in _rows(free, swap, job)
            ],
            [
                (None, None, None, True, False, "used"),
                (None, None, None, False, True, "used"),
                (None, None, None, False, False, None),
            ],
        )

    def test_page_order_is_kept_and_a_paid_slot_is_marked(self) -> None:
        rows = _rows(_ad(searchReason="promoted"), _ad(id=1002), _ad())
        self.assertEqual(
            [(row.listing_id, row.promoted) for row in rows],
            [(1001, True), (1002, False), (1001, False)],
        )

    def test_listing_page_with_no_ads_gives_no_rows(self) -> None:
        self.assertEqual(_rows(), [])

    def test_page_count_comes_with_the_rows(self) -> None:
        page = _page({"listing": {"listing": {"ads": [_ad()], "totalPages": 25}}})
        rows, pages = olx_parser.parse_listings(page, collected_at=COLLECTED)
        self.assertEqual((len(rows), pages), (1, 25))

    def test_page_without_listings_is_an_error(self) -> None:
        for page in ("<title>Access denied</title>", _page({"home": {}})):
            with (
                self.subTest(page=page[:40]),
                self.assertRaisesRegex(ValueError, "no listing data"),
            ):
                olx_parser.parse_listings(page, collected_at=COLLECTED)


if __name__ == "__main__":
    unittest.main()
