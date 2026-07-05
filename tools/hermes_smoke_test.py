import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path


APP_PATH = Path(__file__).resolve().parents[1] / "ha-addon" / "app"
sys.path.insert(0, str(APP_PATH))

from hermes import service  # noqa: E402
from hermes.http_client import amazon_url_variants, fetch_amazon_page  # noqa: E402
from hermes.config_loader import _prepare_products  # noqa: E402
from hermes.models import PriceSummaryRow, SearchResultItem  # noqa: E402
from hermes.providers.base import soup_from_html  # noqa: E402
from hermes.providers.hepsiburada import (  # noqa: E402
    _embedded_detail_candidates,
    extract_offer as extract_hepsiburada_offer,
)
from hermes.providers.nordbron import extract_offer as extract_nordbron_offer  # noqa: E402
from hermes.search_amazon import extract_result_candidates  # noqa: E402
from hermes.utils import detect_site_from_url  # noqa: E402


class HermesSmokeTests(unittest.TestCase):
    def test_amazon_page_fetch_is_cached_per_session(self):
        class FakeResponse:
            status_code = 200
            headers = {"content-type": "text/html; charset=utf-8"}
            content = b"<html><body>amazon product page</body></html>"
            text = "<html><body>amazon product page</body></html>"

            def raise_for_status(self):
                return None

        class FakeSession:
            def __init__(self):
                self.calls = 0
                self.cookies = self

            def set(self, *_args, **_kwargs):
                return None

            def get(self, *_args, **_kwargs):
                self.calls += 1
                return FakeResponse()

        session = FakeSession()
        url = "https://www.amazon.com.tr/dp/B000000001"

        first = fetch_amazon_page(session, url, 10)
        second = fetch_amazon_page(session, url, 10)

        self.assertIs(first, second)
        self.assertEqual(session.calls, 1)

    def test_amazon_product_url_variants_start_with_clean_product_url(self):
        url = "https://www.amazon.com.tr/gp/product/B0B2PSDNV1?ref=ppx_yo2ov_dt_b_fed_asin_title&th=1"
        variants = amazon_url_variants(url)
        self.assertEqual(variants[0], "https://www.amazon.com.tr/dp/B0B2PSDNV1?th=1")
        self.assertIn(url, variants)

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

    def test_amazon_search_url_can_be_used_as_product_url(self):
        self.assertTrue(service.is_amazon_search_url("https://www.amazon.com.tr/s?k=juo+q3"))
        self.assertFalse(service.is_amazon_search_url("https://www.amazon.com.tr/dp/B000000001"))

    def test_product_amazon_search_uses_lowest_matching_offer(self):
        results = [
            SearchResultItem(
                title="Juo Q3 Masa Lambası Siyah",
                url="https://www.amazon.com.tr/dp/B000000001",
                price=Decimal("2037.00"),
            ),
            SearchResultItem(
                title="Juo Q3 Masa Lambası Beyaz",
                url="https://www.amazon.com.tr/dp/B000000002",
                price=Decimal("2099.00"),
            ),
            SearchResultItem(
                title="Başka Marka Masa Lambası",
                url="https://www.amazon.com.tr/dp/B000000003",
                price=Decimal("999.00"),
            ),
        ]
        offer = service.best_offer_from_amazon_search_results(results, "juo q3")
        self.assertEqual(offer.price, Decimal("2037.00"))
        self.assertEqual(offer.url, "https://www.amazon.com.tr/dp/B000000001")

    def test_request_order_spreads_same_site_requests(self):
        items = [
            {"site": "amazon", "name": "amazon-1"},
            {"site": "amazon", "name": "amazon-2"},
            {"site": "amazon", "name": "amazon-3"},
            {"site": "hepsiburada", "name": "hb-1"},
            {"site": "hepsiburada", "name": "hb-2"},
            {"site": "nordbron", "name": "nordbron-1"},
        ]
        ordered = service.balanced_request_order(items)
        ordered_sites = [item["site"] for item in ordered]
        adjacent_same_site = sum(
            1 for previous, current in zip(ordered_sites, ordered_sites[1:]) if previous == current
        )

        self.assertCountEqual(ordered_sites, [item["site"] for item in items])
        self.assertEqual(adjacent_same_site, 0)

    def test_cycle_duration_is_formatted_in_minutes(self):
        self.assertEqual(service.format_minutes(75), "1.2 dk")
        self.assertEqual(service.format_minutes(600), "10 dk")

    def test_early_summary_save_preserves_previous_cycle_duration(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_summary_path = service.SUMMARY_PATH
            try:
                service.SUMMARY_PATH = Path(tmpdir) / "latest_price_summary.json"
                service.SUMMARY_PATH.write_text(
                    json.dumps(
                        {
                            "cycle_duration_seconds": 90,
                            "scan_duration_seconds": 30,
                            "rows": [],
                        }
                    ),
                    encoding="utf-8",
                )
                rows = [
                    PriceSummaryRow(
                        seller="Amazon",
                        product_title="Test ürün",
                        product_url="https://example.com",
                        price=Decimal("100"),
                        target_price=Decimal("90"),
                        min_price=Decimal("100"),
                        max_price=Decimal("100"),
                    )
                ]

                service.save_price_summary(rows)
                early_payload = json.loads(service.SUMMARY_PATH.read_text(encoding="utf-8"))
                self.assertEqual(early_payload["cycle_duration_seconds"], 90)
                self.assertEqual(early_payload["scan_duration_seconds"], 30)

                service.publish_price_summary(rows, cycle_duration_seconds=180, scan_duration_seconds=120)
                final_payload = json.loads(service.SUMMARY_PATH.read_text(encoding="utf-8"))
                self.assertEqual(final_payload["cycle_duration_seconds"], 180)
                self.assertEqual(final_payload["scan_duration_seconds"], 120)
            finally:
                service.SUMMARY_PATH = original_summary_path

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

    def test_nordbron_product_page_is_not_misread_as_captcha(self):
        html = """
        <html>
          <body>
            <div class="product-detail_price__hYyw9"><span>₺ 3,900.00</span></div>
            <script>{"customerSettings":{"requireCaptchaValidation":true},"label":"robot"}</script>
          </body>
        </html>
        """
        self.assertFalse(service.is_bot_protection_page("nordbron", html))
        self.assertTrue(service.is_bot_protection_page("nordbron", "captcha robot"))

    def test_product_card_can_expand_to_multiple_site_links(self):
        products = _prepare_products(
            [
                {
                    "name": "Ortak ürün",
                    "target_price": 1000,
                    "url_1": "https://www.amazon.com.tr/dp/B000000001",
                    "url_2": "https://www.hepsiburada.com/ornek-urun-p-HBCV000000000",
                    "url_3": "https://nordbron.com/stark-sirt-cantasi",
                    "notify_once_in_24H": True,
                    "active": True,
                }
            ]
        )
        self.assertEqual(len(products), 3)
        self.assertEqual([item.site for item in products], ["amazon", "hepsiburada", "nordbron"])
        self.assertTrue(all(item.name == "Ortak ürün" for item in products))

    def test_legacy_product_url_still_loads(self):
        products = _prepare_products(
            [
                {
                    "name": "Eski ürün",
                    "target_price": 1000,
                    "url": "https://www.trendyol.com/ornek/urun-p-1",
                    "active": True,
                }
            ]
        )
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0].site, "trendyol")


if __name__ == "__main__":
    unittest.main()
