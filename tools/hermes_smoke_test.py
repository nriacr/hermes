import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path


APP_PATH = Path(__file__).resolve().parents[1] / "ha-addon" / "app"
sys.path.insert(0, str(APP_PATH))

from hermes import service  # noqa: E402
from hermes.providers.base import soup_from_html  # noqa: E402
from hermes.providers.hepsiburada import (  # noqa: E402
    _embedded_detail_candidates,
    extract_offer as extract_hepsiburada_offer,
)
from hermes.providers.nordbron import extract_offer as extract_nordbron_offer  # noqa: E402
from hermes.search_amazon import extract_result_candidates  # noqa: E402
from hermes.utils import detect_site_from_url  # noqa: E402


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
                 "finalPriceOnSale": 10499.25,
                 "prices": [{"formattedPrice": "10.499,25", "value": 10499.25}]},
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

    def test_hepsiburada_detail_ignores_hidden_minimum_price_when_final_price_exists(self):
        html = """
        <html><head><title>Samsung Galaxy Tab S10 FE+ Fiyatı</title></head>
        <body>
          <script>
            window.__HB_STATE__ = {
              "variantListing": [
                {"aiBasedShipmentDay": null, "listingId": "listing-hb", "merchantName": "Hepsiburada",
                 "minimumPrice": 14279, "finalPriceOnSale": 18999,
                 "minimumPrices": [
                   {"name": "10", "value": 14279},
                   {"name": "30", "value": 14279},
                   {"name": "non-segmented-price", "value": 18999}
                 ]},
                {"aiBasedShipmentDay": null, "listingId": "listing-vatan", "merchantName": "VATAN BİLGİSAYAR",
                 "minimumPrice": 18999, "finalPriceOnSale": 18999}
              ]
            };
          </script>
        </body></html>
        """
        offer = extract_hepsiburada_offer(html)
        self.assertEqual(offer.price, Decimal("18999"))

    def test_hepsiburada_detail_offers_are_scoped_to_selected_variant(self):
        html = """
        <html><head><title>Samsung Galaxy Tab S10 FE+ Mavi Fiyatı</title></head>
        <body>
          <script>
            window.__HB_STATE__ = {
              "variants": [
                {"sku": "HBCV00008E1SF6", "variantListing": [
                  {"listingId": "selected-hb-1", "merchantName": "Hepsiburada",
                   "finalPriceOnSale": 18999, "prices": [{"value": 18999}]},
                  {"listingId": "selected-hb-2", "merchantName": "Hepsiburada",
                   "finalPriceOnSale": 19999, "prices": [{"value": 19999}]},
                  {"listingId": "selected-vatan", "merchantName": "VATAN BİLGİSAYAR",
                   "finalPriceOnSale": 18999, "prices": [{"value": 18999}]}
                ]},
                {"sku": "HBCV00008E1QWF", "variantListing": [
                  {"listingId": "other-color", "merchantName": "Başka Satıcı",
                   "finalPriceOnSale": 1000, "prices": [{"value": 1000}]}
                ]}
              ]
            };
          </script>
        </body></html>
        """
        source_url = "https://www.hepsiburada.com/samsung-tablet-p-HBCV00008E1SF6"
        offer = extract_hepsiburada_offer(html, source_url=source_url)
        candidates = _embedded_detail_candidates(soup_from_html(html), source_url=source_url)

        self.assertEqual(offer.price, Decimal("18999"))
        self.assertNotEqual(offer.seller, "Başka Satıcı")
        self.assertEqual([item.seller for item in candidates].count("Hepsiburada"), 1)
        self.assertFalse(any(item.price == Decimal("1000") for item in candidates))

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

    def test_nordbron_product_price(self):
        html = """
        <html>
          <head><title>Stark Sırt Çantası</title></head>
          <body>
            <h1>Stark Sırt Çantası</h1>
            <div class="product-detail_price__hYyw9"><span>₺ 4,850.00</span></div>
          </body>
        </html>
        """
        offer = extract_nordbron_offer(html)
        self.assertEqual(offer.title, "Stark Sırt Çantası")
        self.assertEqual(offer.price, Decimal("4850.00"))

    def test_nordbron_site_detection(self):
        url = "https://nordbron.com/stark-sirt-cantasi?Renk=Antrasit&Beden=Standart-Beden"
        self.assertEqual(detect_site_from_url(url), "nordbron")


if __name__ == "__main__":
    unittest.main()
