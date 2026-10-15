"""Parser checks against cleaned real page snapshots (tests/fixtures/<site>/<name>.html).

Each page has a <name>.expected.json: {"url": ..., "offers": [{"title", "price"}], "size": optional}.
When a site changes its markup the failing fixture name says which page and field broke. Add
a page with tools/capture_fixture.py, then record what the live page showed in the .expected.json.
"""
import json
import unittest
from decimal import Decimal
from pathlib import Path

from support import APP_PATH  # noqa: F401

from hermes.providers.bengurme import extract_offers as bengurme_offers
from hermes.providers.nordbron import extract_offer as nordbron_offer

FIXTURES = Path(__file__).parent / "fixtures"


def _bengurme(html, url, size):
    return bengurme_offers(html, url, size)


def _nordbron(html, url, _size):
    return [nordbron_offer(html, url)]


EXTRACTORS = {"bengurme": _bengurme, "nordbron": _nordbron}


class FixturePageTests(unittest.TestCase):
    def test_every_fixture_has_an_extractor_and_expectation(self):
        for page in FIXTURES.glob("*/*.html"):
            self.assertIn(page.parent.name, EXTRACTORS, f"{page}: no extractor registered")
            self.assertTrue(page.with_suffix(".expected.json").exists(), f"{page}: missing .expected.json")

    def test_pages_parse_to_the_recorded_offers(self):
        pages = sorted(FIXTURES.glob("*/*.html"))
        self.assertTrue(pages)
        for page in pages:
            expected = json.loads(page.with_suffix(".expected.json").read_text(encoding="utf-8"))
            with self.subTest(page=f"{page.parent.name}/{page.stem}"):
                offers = EXTRACTORS[page.parent.name](page.read_text(encoding="utf-8"), expected["url"], expected.get("size", ""))
                self.assertEqual(
                    [{"title": offer.title, "price": str(offer.price)} for offer in offers],
                    [{"title": item["title"], "price": str(Decimal(item["price"]))} for item in expected["offers"]],
                )


if __name__ == "__main__":
    unittest.main()
