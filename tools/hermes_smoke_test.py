import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path


APP_PATH = Path(__file__).resolve().parents[1] / "ha-addon" / "app"
sys.path.insert(0, str(APP_PATH))

from hermes import service  # noqa: E402
from hermes.providers.hepsiburada import extract_offer as extract_hepsiburada_offer  # noqa: E402
from hermes.search_amazon import extract_result_candidates  # noqa: E402


class HermesSmokeTests(unittest.TestCase):
    def test_amazon_search_card_uses_structured_price(self):
        html = """
        <div class="s-main-slot">
          <div data-component-type="s-search-result" data-asin="B000000001">
            <h2><a href="/dp/B000000001"><span>Philips Hue Flare 2'li Paket</span></a></h2>
            <span>Pesin fiyatina 9 x 3.210 TL</span>
            <span class="a-price">
              <span class="a-offscreen">10.448,99 TL</span>
              <span class="a-price-whole">10.448</span>
              <span class="a-price-fraction">99</span>
            </span>
          </div>
        </div>
        """
        item = extract_result_candidates(html, 10)[0]
        self.assertEqual(item.price, Decimal("10448.99"))

    def test_absurd_current_price_does_not_overwrite_history(self):
        state_entry = {
            "last_price": "10448.99",
            "min_price": "10448.99",
            "max_price": "12398.40",
        }
        min_price, max_price = service.sanitized_price_bounds(
            state_entry,
            Decimal("3210448.99"),
            Decimal("9500"),
        )
        self.assertEqual(min_price, Decimal("10448.99"))
        self.assertEqual(max_price, Decimal("12398.40"))

    def test_hepsiburada_detail_embedded_listings_use_lowest_offer(self):
        html = """
        <html><head><title>Govee Uplighter Köşe Lambası RGB Fiyatı</title></head>
        <body>
          <script>
            window.__HB_STATE__ = {
              "variantListing": [
                {"aiBasedShipmentDay": null, "listingId": "listing-hb", "merchantName": "Hepsiburada",
                 "minimumPrice": 10499.25, "finalPriceOnSale": 13999,
                 "prices": [{"formattedPrice": "13.999,00", "value": 13999}]},
                {"aiBasedShipmentDay": null, "listingId": "listing-jetklik", "merchantName": "JetKlik",
                 "minimumPrice": 12358.43, "finalPriceOnSale": 12358.43,
                 "prices": [{"formattedPrice": "12.358,43", "value": 12358.43}]}
              ]
            };
          </script>
        </body></html>
        """
        offer = extract_hepsiburada_offer(html)
        self.assertEqual(offer.seller, "Hepsiburada")
        self.assertEqual(offer.price, Decimal("10499.25"))

    def test_manual_price_history_reset_preserves_alert_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_state_path = service.STATE_PATH
            original_summary_path = service.SUMMARY_PATH
            try:
                root = Path(tmpdir)
                service.STATE_PATH = root / "state.json"
                service.SUMMARY_PATH = root / "latest_price_summary.json"
                service.STATE_PATH.write_text(
                    json.dumps(
                        {
                            "product_a": {
                                "last_price": "100",
                                "min_price": "80",
                                "max_price": "300",
                                "last_alerted_price": "90",
                            },
                            "_meta": {"keep": "yes"},
                        }
                    ),
                    encoding="utf-8",
                )
                service.SUMMARY_PATH.write_text(
                    json.dumps(
                        {
                            "rows": [
                                {
                                    "price": "100,00",
                                    "min_price": "80,00",
                                    "max_price": "300,00",
                                    "price_range": "80,00 / 300,00",
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )

                cleared_count = service.reset_price_history()
                state = json.loads(service.STATE_PATH.read_text(encoding="utf-8"))
                summary = json.loads(service.SUMMARY_PATH.read_text(encoding="utf-8"))

                self.assertEqual(cleared_count, 2)
                self.assertEqual(state["product_a"]["last_price"], "100")
                self.assertEqual(state["product_a"]["last_alerted_price"], "90")
                self.assertNotIn("min_price", state["product_a"])
                self.assertNotIn("max_price", state["product_a"])
                self.assertEqual(summary["rows"][0]["price_range"], "100,00 / 100,00")
            finally:
                service.STATE_PATH = original_state_path
                service.SUMMARY_PATH = original_summary_path


if __name__ == "__main__":
    unittest.main()
