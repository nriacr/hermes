#!/usr/bin/env python3
"""Small regression checks for Hermes maintenance work.

These tests avoid live network calls. They focus on parser and state behavior that
has caused real production issues before.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "ha-addon" / "app"
sys.path.insert(0, str(APP_DIR))

from hermes.models import PriceResult, Product, Site, SummaryRow  # noqa: E402
from hermes.providers.search_amazon import parse_search_products  # noqa: E402
from hermes.service import HermesService  # noqa: E402
from hermes.settings import Settings  # noqa: E402
from hermes.state import HermesState  # noqa: E402


class HermesSmokeTests(unittest.TestCase):
    def make_service(self) -> HermesService:
        temp_dir = Path(tempfile.mkdtemp(prefix="hermes-smoke-"))
        settings = Settings(
            raw={
                "pushover_user_key": "",
                "pushover_api_token": "",
                "products": [],
                "amazon_search_pages": [],
                "amazon_search_targets": [],
            },
            data_dir=temp_dir,
        )
        state = HermesState(temp_dir / "state.json")
        return HermesService(settings, state)

    def test_amazon_search_card_uses_structured_price(self) -> None:
        html = """
        <div data-component-type="s-search-result" data-asin="BTEST123">
          <a class="a-link-normal" href="/dp/BTEST123"><h2><span>Test Product</span></h2></a>
          <span class="a-price"><span class="a-offscreen">1.234,56 TL</span></span>
          <span>Peşin fiyatına 9 x 3.210 TL</span>
        </div>
        """
        products = parse_search_products(html, "https://www.amazon.com.tr/s?k=test", 10)
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].price, Decimal("1234.56"))
        self.assertEqual(products[0].url, "https://www.amazon.com.tr/dp/BTEST123")

    def test_absurd_current_price_does_not_overwrite_history(self) -> None:
        service = self.make_service()
        state_entry = {"min_price": "4477.71", "max_price": "4983.55", "last_price": "4477.71"}
        product = Product(
            name="Belkin",
            url="https://www.amazon.com.tr/dp/example",
            target_price=Decimal("4400"),
            site=Site.AMAZON,
        )
        result = PriceResult(
            site=Site.AMAZON,
            title="Belkin charger",
            price=Decimal("104477.71"),
            url=product.url,
            seller="Amazon",
        )

        row = SummaryRow.from_product(product, result)
        service.apply_price_history(row, state_entry)

        self.assertEqual(row.min_price, Decimal("4477.71"))
        self.assertEqual(row.max_price, Decimal("4983.55"))
        self.assertEqual(state_entry["min_price"], "4477.71")
        self.assertEqual(state_entry["max_price"], "4983.55")

    def test_manual_price_history_reset_preserves_alert_state(self) -> None:
        service = self.make_service()
        service.state.data = {
            "product_alerts": {"x": {"last_notified_at": "2026-06-25 10:00:00"}},
            "product_seen": {"x": True},
            "product_prices": {
                "x": {
                    "last_price": "100",
                    "last_seen": "2026-06-25 10:00:00",
                    "min_price": "90",
                    "max_price": "120",
                    "min_price_at": "2026-06-24 10:00:00",
                    "max_price_at": "2026-06-25 10:00:00",
                }
            },
            "search_prices": {
                "y": {
                    "last_price": "200",
                    "min_price": "180",
                    "max_price": "250",
                }
            },
        }

        cleared = service.reset_price_history()

        self.assertEqual(cleared, 2)
        self.assertIn("last_notified_at", service.state.data["product_alerts"]["x"])
        self.assertEqual(service.state.data["product_prices"]["x"]["last_price"], "100")
        self.assertNotIn("min_price", service.state.data["product_prices"]["x"])
        self.assertNotIn("max_price", service.state.data["search_prices"]["y"])


if __name__ == "__main__":
    unittest.main()
