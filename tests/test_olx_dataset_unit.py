"""Offline tests for how OLX listing pages become one deduplicated dataset."""

import unittest
from typing import Any

from scripts import olx_dataset
from tests.test_olx_parser_unit import _ad, _page

URL = "https://www.olx.pl/elektronika/telefony/"


class _Site:
    """Serves listing pages by the `page` number in the URL and keeps the URLs it was asked for."""

    def __init__(self, *pages: list[dict[str, Any]]) -> None:
        self.pages = pages
        self.asked: list[str] = []

    def __call__(self, url: str) -> str:
        self.asked.append(url)
        ads = self.pages[int(url.rsplit("=", 1)[1]) - 1]
        return _page({"listing": {"listing": {"ads": ads, "totalPages": len(self.pages)}}})


class CollectTests(unittest.TestCase):
    def test_walk_ends_at_the_last_page_the_site_has(self) -> None:
        site = _Site([_ad(id=1), _ad(id=2)], [_ad(id=3)])
        rows, seen, stopped = olx_dataset.collect(site, "https://www.olx.pl/oferty/?min=1", 5)
        self.assertEqual(
            site.asked,
            ["https://www.olx.pl/oferty/?min=1&page=1", "https://www.olx.pl/oferty/?min=1&page=2"],
        )
        self.assertEqual(([row.listing_id for row in rows], seen, stopped), ([1, 2, 3], 3, ""))

    def test_walk_ends_at_the_page_limit(self) -> None:
        site = _Site([_ad(id=1)], [_ad(id=2)])
        rows, _, stopped = olx_dataset.collect(site, URL, 1)
        self.assertEqual(site.asked, [URL + "?page=1"])
        self.assertEqual(([row.listing_id for row in rows], stopped), ([1], ""))

    def test_repeated_ad_is_one_row_that_remembers_its_paid_slot(self) -> None:
        site = _Site([_ad(id=1), _ad(id=2)], [_ad(id=2, searchReason="promoted"), _ad(id=3)])
        rows, seen, _ = olx_dataset.collect(site, URL, 2)
        self.assertEqual(
            [(row.listing_id, row.promoted) for row in rows], [(1, False), (2, True), (3, False)]
        )
        self.assertEqual(seen, 4)

    def test_page_without_listings_ends_the_walk_and_keeps_earlier_rows(self) -> None:
        first = _Site([_ad(id=1)], [], [])

        def fetch(url: str) -> str:
            return "<title>Access denied</title>" if url.endswith("page=2") else first(url)

        rows, seen, stopped = olx_dataset.collect(fetch, URL, 3)
        self.assertEqual(
            ([row.listing_id for row in rows], seen, stopped),
            ([1], 1, "page 2: no listing data on the page"),
        )


if __name__ == "__main__":
    unittest.main()
