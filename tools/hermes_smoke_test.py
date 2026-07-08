import json
import requests
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path


APP_PATH = Path(__file__).resolve().parents[1] / "ha-addon" / "app"
sys.path.insert(0, str(APP_PATH))

from hermes import service  # noqa: E402
from hermes import http_client  # noqa: E402
from hermes.http_client import amazon_url_variants, fetch_amazon_page  # noqa: E402
from hermes.config_loader import _prepare_watches  # noqa: E402
from hermes.models import HermesConfig, OfferResult, PriceSummaryRow, SearchResultItem, TelegramConfig  # noqa: E402
from hermes.providers.base import soup_from_html  # noqa: E402
from hermes.providers.hepsiburada import (  # noqa: E402
    _embedded_detail_candidates,
    clean_display_title,
    extract_embedded_variant_label,
    extract_embedded_variant_offer,
    extract_offer as extract_hepsiburada_offer,
    extract_search_offers as extract_hepsiburada_search_offers,
    extract_selected_variant_label,
    extract_selected_variant_labels,
    extract_variant_urls,
    title_with_variant_label,
)
from hermes.providers.nordbron import extract_offer as extract_nordbron_offer  # noqa: E402
from hermes.providers.zara import extract_offers as extract_zara_offers  # noqa: E402
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

        original_curl_requests = http_client.curl_requests
        http_client.curl_requests = None
        try:
            first = fetch_amazon_page(session, url, 10)
            second = fetch_amazon_page(session, url, 10)
        finally:
            http_client.curl_requests = original_curl_requests

        self.assertIs(first, second)
        self.assertEqual(session.calls, 1)

    def test_amazon_hard_curl_block_can_recover_with_requests_after_rescue(self):
        curl_calls = {"count": 0}
        browser_calls = {"count": 0}

        class FakeCookies:
            def set(self, *_args, **_kwargs):
                return None

            def clear(self, *_args, **_kwargs):
                return None

        class FakeRequestsSession:
            def __init__(self):
                self.calls = 0
                self.cookies = FakeCookies()

            def get(self, *_args, **_kwargs):
                self.calls += 1
                return FakeRequestsResponse()

        class FakeRequestsResponse:
            status_code = 200
            headers = {"content-type": "text/html; charset=utf-8"}
            content = b'<html><body><div data-component-type="s-search-result"><a href="/dp/B000">Amazon</a></div></body></html>'
            text = content.decode("utf-8")

            def raise_for_status(self):
                return None

        class FakeCurlResponse:
            status_code = 503
            headers = {}
            content = b""
            text = ""

            def raise_for_status(self):
                error = requests.HTTPError("503 Server Error")
                error.response = self
                raise error

        class FakeCurlSession:
            def get(self, *_args, **_kwargs):
                curl_calls["count"] += 1
                return FakeCurlResponse()

        class FakeCurlRequests:
            @staticmethod
            def Session():
                return FakeCurlSession()

        original_curl_requests = http_client.curl_requests
        original_browser_rescue = http_client._get_amazon_response_with_browser
        http_client.curl_requests = FakeCurlRequests
        session = FakeRequestsSession()

        def fake_browser_rescue(*_args, **_kwargs):
            browser_calls["count"] += 1
            raise http_client.HermesError("browser rescue failed")

        http_client._get_amazon_response_with_browser = fake_browser_rescue
        try:
            response = fetch_amazon_page(session, "https://www.amazon.com.tr/s?k=juo+q3", 10, expect_search=True)
        finally:
            http_client.curl_requests = original_curl_requests
            http_client._get_amazon_response_with_browser = original_browser_rescue

        self.assertEqual(response.status_code, 200)
        self.assertGreater(session.calls, 0)
        self.assertEqual(curl_calls["count"], 3)
        self.assertEqual(browser_calls["count"], 2)
        self.assertFalse(hasattr(session, "_hermes_amazon_curl_session"))

    def test_amazon_hard_curl_block_can_recover_with_fresh_variant(self):
        curl_calls = {"count": 0}

        class FakeCookies:
            def set(self, *_args, **_kwargs):
                return None

            def clear(self, *_args, **_kwargs):
                return None

        class FakeRequestsSession:
            cookies = FakeCookies()

            def get(self, *_args, **_kwargs):
                raise AssertionError("requests fallback should not run while curl rescue can recover")

        class FakeCurlResponse:
            headers = {"content-type": "text/html; charset=utf-8"}
            content = b"<html><body>amazon <div data-component-type='s-search-result'>ok</div></body></html>"
            text = "<html><body>amazon <div data-component-type='s-search-result'>ok</div></body></html>"

            def __init__(self, status_code):
                self.status_code = status_code

            def raise_for_status(self):
                if self.status_code == 200:
                    return None
                error = requests.HTTPError("503 Server Error")
                error.response = self
                raise error

        class FakeCurlSession:
            def get(self, *_args, **_kwargs):
                curl_calls["count"] += 1
                return FakeCurlResponse(200 if curl_calls["count"] == 3 else 503)

        class FakeCurlRequests:
            @staticmethod
            def Session():
                return FakeCurlSession()

        original_curl_requests = http_client.curl_requests
        original_browser_rescue = http_client._get_amazon_response_with_browser
        http_client.curl_requests = FakeCurlRequests

        def fake_browser_rescue(*_args, **_kwargs):
            raise AssertionError("browser rescue should not run when fresh curl variant recovers")

        http_client._get_amazon_response_with_browser = fake_browser_rescue
        try:
            response = fetch_amazon_page(
                FakeRequestsSession(),
                "https://www.amazon.com.tr/s?k=juo+q3",
                10,
                expect_search=True,
            )
        finally:
            http_client.curl_requests = original_curl_requests
            http_client._get_amazon_response_with_browser = original_browser_rescue

        self.assertEqual(response.status_code, 200)
        self.assertEqual(curl_calls["count"], 3)

    def test_amazon_browser_rescue_recovers_after_curl_blocks(self):
        curl_calls = {"count": 0}
        browser_calls = {"count": 0}

        class FakeCookies:
            def set(self, *_args, **_kwargs):
                return None

            def clear(self, *_args, **_kwargs):
                return None

        class FakeRequestsSession:
            def __init__(self):
                self.cookies = FakeCookies()
                self.normal_requests = 0

            def get(self, *_args, **_kwargs):
                self.normal_requests += 1
                raise AssertionError("normal requests should not run while browser rescue can recover")

        class FakeCurlResponse:
            status_code = 503
            headers = {}
            content = b""
            text = ""

            def raise_for_status(self):
                error = requests.HTTPError("503 Server Error")
                error.response = self
                raise error

        class FakeCurlSession:
            def get(self, *_args, **_kwargs):
                curl_calls["count"] += 1
                return FakeCurlResponse()

        class FakeCurlRequests:
            @staticmethod
            def Session():
                return FakeCurlSession()

        def fake_browser_rescue(_session, candidate, _timeout, expect_search):
            browser_calls["count"] += 1
            html = "<html><body>amazon <div data-component-type='s-search-result'>ok</div></body></html>"
            return http_client._HtmlResponse(candidate, html)

        original_curl_requests = http_client.curl_requests
        original_browser_rescue = http_client._get_amazon_response_with_browser
        http_client.curl_requests = FakeCurlRequests
        http_client._get_amazon_response_with_browser = fake_browser_rescue
        session = FakeRequestsSession()
        try:
            response = fetch_amazon_page(session, "https://www.amazon.com.tr/s?k=juo+q3", 10, expect_search=True)
        finally:
            http_client.curl_requests = original_curl_requests
            http_client._get_amazon_response_with_browser = original_browser_rescue

        self.assertEqual(response.status_code, 200)
        self.assertEqual(curl_calls["count"], 3)
        self.assertEqual(browser_calls["count"], 1)
        self.assertEqual(session.normal_requests, 0)

    def test_amazon_browser_rescue_accepts_usable_stdout_with_nonzero_exit(self):
        class FakeCookies:
            def set(self, *_args, **_kwargs):
                return None

            def clear(self, *_args, **_kwargs):
                return None

        class FakeSession:
            cookies = FakeCookies()

        def fake_run(*_args, **_kwargs):
            return http_client.subprocess.CompletedProcess(
                args=["chromium"],
                returncode=1,
                stdout='<html><body><div data-component-type="s-search-result"><a href="/dp/B000">Amazon</a></div></body></html>',
                stderr="dbus connection warning",
            )

        original_run = http_client.subprocess.run
        original_chromium_binary = http_client._chromium_binary
        http_client.subprocess.run = fake_run
        http_client._chromium_binary = lambda: "/usr/bin/chromium"
        try:
            response = http_client._get_amazon_response_with_browser(
                FakeSession(),
                "https://www.amazon.com.tr/s?k=juo+q3",
                10,
                True,
            )
        finally:
            http_client.subprocess.run = original_run
            http_client._chromium_binary = original_chromium_binary

        self.assertEqual(response.status_code, 200)
        self.assertIn("s-search-result", response.text)

    def test_amazon_product_url_variants_start_with_clean_product_url(self):
        url = "https://www.amazon.com.tr/gp/product/B0B2PSDNV1?ref=ppx_yo2ov_dt_b_fed_asin_title&th=1"
        variants = amazon_url_variants(url)
        self.assertEqual(variants[0], "https://www.amazon.com.tr/dp/B0B2PSDNV1?th=1")
        self.assertIn(url, variants)

    def test_amazon_protection_error_is_scoped_per_key(self):
        state = {}
        error = service.HermesError("Amazon bot korumasi nedeniyle captcha/koruma sayfasi dondu.")

        self.assertTrue(service.is_amazon_protection_error(error))
        service.note_amazon_protection(state, "amazon-a", "test", error)

        self.assertEqual(service.amazon_protection_remaining_seconds(state, "amazon-a"), 0)
        self.assertEqual(service.amazon_protection_remaining_seconds(state, "amazon-b"), 0)
        self.assertIn("amazon_protection", state["_meta"])

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
        self.assertEqual(service.format_minutes(75), "1 dk 15 sn")
        self.assertEqual(service.format_minutes(600), "10 dk 0 sn")

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

    def test_hepsiburada_detail_prefers_visible_premium_price(self):
        html = """
        <html><head><title>Samsung Galaxy Tab S10 FE+ Fiyatı</title></head>
        <body>
          <h1>Samsung Galaxy Tab S10 FE+</h1>
          <span>Satıcı: Hepsiburada</span>
          <div data-test-id="price-current-price">18.199,00 TL</div>
          <div>Premium ile 17.949,00 TL</div>
          <button>Sepete ekle</button>
          <section>Ürün Bilgileri</section>
        </body></html>
        """

        offer = extract_hepsiburada_offer(html)

        self.assertEqual(offer.price, Decimal("17949.00"))

    def test_hepsiburada_detail_prefers_premium_special_price(self):
        html = """
        <html><head><title>Samsung Galaxy Tab S10 FE+ Fiyatı</title></head>
        <body>
          <h1>Samsung Galaxy Tab S10 FE+</h1>
          <span>Satıcı: Hepsiburada</span>
          <div data-test-id="price-current-price">18.199,00 TL</div>
          <div>Premium'a özel fiyat</div>
          <div>17.949 TL</div>
          <button>Sepete ekle</button>
          <section>Ürün Bilgileri</section>
        </body></html>
        """

        offer = extract_hepsiburada_offer(html)

        self.assertEqual(offer.price, Decimal("17949"))

    def test_hepsiburada_product_url_compares_embedded_and_visible_premium_price(self):
        html = """
        <html><head><title>Samsung Galaxy Tab S10 FE+ Fiyatı</title></head>
        <body>
          <h1>Samsung Galaxy Tab S10 FE+</h1>
          <span>Satıcı: Hepsiburada</span>
          <div>Premium’a özel fiyat</div>
          <div>17.949 TL</div>
          <div data-test-id="price-current-price">18.199,00 TL</div>
          <script>
            window.__HB_STATE__ = {
              "variants": [
                {"sku": "HBCV00008E1SXR", "variantListing": [
                  {"aiBasedShipmentDay": null, "listingId": "listing-hb", "merchantName": "Hepsiburada",
                   "finalPriceOnSale": 18199,
                   "minimumPrices": [{"name": "non-segmented-price", "value": 18199}]}
                ]}
              ]
            };
          </script>
        </body></html>
        """

        offer = extract_hepsiburada_offer(
            html,
            source_url="https://www.hepsiburada.com/samsung-tablet-p-HBCV00008E1SXR",
        )

        self.assertEqual(offer.price, Decimal("17949"))

    def test_hepsiburada_product_url_reads_public_premium_ile_price(self):
        html = """
        <html><head><title>Samsung Galaxy Tab S10 FE+ Fiyatı</title></head>
        <body>
          <h1>Samsung Galaxy Tab S10 FE+ 8GB 128GB SM-X620</h1>
          <span>Satıcı: Hepsiburada</span>
          <div data-test-id="price-current-price">18.299,00 TL</div>
          <div class="premium-price">Premium ile <strong>18.049 TL</strong></div>
          <div>Renk: Mavi</div>
          <button>Sepete ekle</button>
          <script>
            window.__HB_STATE__ = {
              "variants": [
                {"sku": "HBCV00008E1SXR", "variantListing": [
                  {"aiBasedShipmentDay": null, "listingId": "listing-hb", "merchantName": "Hepsiburada",
                   "finalPriceOnSale": 18299,
                   "minimumPrices": [{"name": "non-segmented-price", "value": 18299}]}
                ]}
              ]
            };
          </script>
        </body></html>
        """

        offer = extract_hepsiburada_offer(
            html,
            source_url="https://www.hepsiburada.com/samsung-tablet-p-HBCV00008E1SXR",
        )

        self.assertEqual(offer.price, Decimal("18049"))

    def test_hepsiburada_product_url_reads_escaped_premium_ile_price(self):
        html = r"""
        <html><head><title>Samsung Galaxy Tab S10 FE+ Fiyatı</title></head>
        <body>
          <h1>Samsung Galaxy Tab S10 FE+ 8GB 128GB SM-X620</h1>
          <span>Satıcı: Hepsiburada</span>
          <div data-test-id="price-current-price">18.299,00 TL</div>
          <script>
            window.__HB_PAGE__ = "{\"campaign\":\"Premium ile 18.049 TL\"}";
            window.__HB_STATE__ = {
              "variants": [
                {"sku": "HBCV00008E1SXR", "variantListing": [
                  {"aiBasedShipmentDay": null, "listingId": "listing-hb", "merchantName": "Hepsiburada",
                   "finalPriceOnSale": 18299,
                   "minimumPrices": [{"name": "non-segmented-price", "value": 18299}]}
                ]}
              ]
            };
          </script>
        </body></html>
        """

        offer = extract_hepsiburada_offer(
            html,
            source_url="https://www.hepsiburada.com/samsung-tablet-p-HBCV00008E1SXR",
        )

        self.assertEqual(offer.price, Decimal("18049"))

    def test_hepsiburada_product_url_reads_plain_integer_premium_price(self):
        html = """
        <html><head><title>Samsung Galaxy Tab S10 FE+ Fiyatı</title></head>
        <body>
          <h1>Samsung Galaxy Tab S10 FE+ 8GB 128GB SM-X620</h1>
          <span>Satıcı: Hepsiburada</span>
          <div data-test-id="price-current-price">18.299,00 TL</div>
          <script>
            window.__HB_PAGE__ = {"campaign": "Premium ile 18049 TL"};
            window.__HB_STATE__ = {
              "variants": [
                {"sku": "HBCV00008E1SXR", "variantListing": [
                  {"aiBasedShipmentDay": null, "listingId": "listing-hb", "merchantName": "Hepsiburada",
                   "finalPriceOnSale": 18299,
                   "minimumPrices": [{"name": "non-segmented-price", "value": 18299}]}
                ]}
              ]
            };
          </script>
        </body></html>
        """

        offer = extract_hepsiburada_offer(
            html,
            source_url="https://www.hepsiburada.com/samsung-tablet-p-HBCV00008E1SXR",
        )

        self.assertEqual(offer.price, Decimal("18049"))

    def test_hepsiburada_product_url_reads_premium_price_without_tl_suffix(self):
        html = """
        <html><head><title>Samsung Galaxy Tab S10 FE+ Fiyatı</title></head>
        <body>
          <h1>Samsung Galaxy Tab S10 FE+ 8GB 128GB SM-X620</h1>
          <span>Satıcı: Hepsiburada</span>
          <div data-test-id="price-current-price">18.299,00 TL</div>
          <div>Premium ile 18.049</div>
          <script>
            window.__HB_STATE__ = {
              "variants": [
                {"sku": "HBCV00008E1SXR", "variantListing": [
                  {"aiBasedShipmentDay": null, "listingId": "listing-hb", "merchantName": "Hepsiburada",
                   "finalPriceOnSale": 18299,
                   "minimumPrices": [{"name": "non-segmented-price", "value": 18299}]}
                ]}
              ]
            };
          </script>
        </body></html>
        """

        offer = extract_hepsiburada_offer(
            html,
            source_url="https://www.hepsiburada.com/samsung-tablet-p-HBCV00008E1SXR",
        )

        self.assertEqual(offer.price, Decimal("18049"))

    def test_hepsiburada_embedded_variant_price_is_not_overridden_by_other_visible_variant(self):
        html = """
        <html><head><title>Samsung Galaxy Tab S10 FE+ Fiyatı</title></head>
        <body>
          <nav>Hepsiburada'da Satıcı Ol</nav>
          <h1>Samsung Galaxy Tab S10 FE+ 8GB 128GB SM-X620</h1>
          <div data-test-id="price-current-price">18.299,00 TL</div>
          <div>Renk Mavi 18.299,00 TL</div>
          <div>Renk Gümüş 18.349,00 TL</div>
          <script>
            window.__HB_STATE__ = {
              "variants": [
                {"sku": "HBCV00008E1QWF", "variantListing": [
                  {"aiBasedShipmentDay": null, "listingId": "listing-hb", "merchantName": "Hepsiburada",
                   "finalPriceOnSale": 18349,
                   "minimumPrices": [{"name": "non-segmented-price", "value": 18349}]}
                ]}
              ]
            };
          </script>
        </body></html>
        """

        offer = extract_hepsiburada_offer(
            html,
            source_url="https://www.hepsiburada.com/samsung-tablet-p-HBCV00008E1QWF",
        )

        self.assertEqual(offer.price, Decimal("18349"))
        self.assertEqual(offer.seller, "Hepsiburada")

    def test_hepsiburada_product_url_ignores_premium_campaign_discount_amount(self):
        html = """
        <html><head><title>Samsung Galaxy Tab S10 FE+ Fiyatı</title></head>
        <body>
          <h1>Samsung Galaxy Tab S10 FE+ 8GB 128GB SM-X620</h1>
          <span>Satıcı: Hepsiburada</span>
          <div data-test-id="price-current-price">18.299,00 TL</div>
          <div>Seçili Tabletlerde Premium'a Özel Sepette 250 TL İndirim!</div>
          <button>Sepete ekle</button>
          <script>
            window.__HB_STATE__ = {
              "variants": [
                {"sku": "HBCV00008E1SXR", "variantListing": [
                  {"aiBasedShipmentDay": null, "listingId": "listing-hb", "merchantName": "Hepsiburada",
                   "finalPriceOnSale": 18299,
                   "minimumPrices": [{"name": "non-segmented-price", "value": 18299}]}
                ]}
              ]
            };
          </script>
        </body></html>
        """

        offer = extract_hepsiburada_offer(
            html,
            source_url="https://www.hepsiburada.com/samsung-tablet-p-HBCV00008E1SXR",
        )

        self.assertEqual(offer.price, Decimal("18299"))

    def test_hepsiburada_search_page_returns_each_card_as_offer(self):
        html = """
        <html><body>
          <ul>
            <li class="productCard">
              <a href="/samsung-galaxy-tab-s10-fe-gumus-p-HBCV00008GUMUS">
                Samsung Galaxy Tab S10 FE+ 8GB 128GB SM-X620 (Samsung Türkiye Garantili) Gümüş
              </a>
              <img alt="Samsung Galaxy Tab S10 FE+ 128 GB Gümüş">
              <div>Premium ile 18.099 TL</div>
            </li>
            <li class="productCard">
              <a href="/samsung-galaxy-tab-s10-fe-mavi-p-HBCV00008MAVI">
                Samsung Galaxy Tab S10 FE+ 8GB 128GB SM-X620 (Samsung Türkiye Garantili) Mavi
              </a>
              <img alt="Samsung Galaxy Tab S10 FE+ 128 GB Mavi">
              <div>Premium ile 18.049 TL</div>
            </li>
            <li class="productCard">
              <a href="/samsung-galaxy-tab-s10-fe-gri-p-HBCV00008GRI">
                Samsung Galaxy Tab S10 FE+ 8GB 128GB SM-X620 (Samsung Türkiye Garantili) Gri
              </a>
              <img alt="Samsung Galaxy Tab S10 FE+ 128 GB Gri">
              <div>18.399 TL</div>
            </li>
            <li class="productCard">
              <a href="/samsung-galaxy-tab-s10-fe-mavi-256gb-p-HBCV00008256GB">
                Samsung Galaxy Tab S10FE+ 13.1 12/256GB Tam Dokunmatik Tablet
              </a>
              <img alt="Samsung Galaxy Tab S10 FE+ 256 GB Mavi">
              <div>22.923,32 TL</div>
            </li>
          </ul>
        </body></html>
        """

        offers = extract_hepsiburada_search_offers(
            html,
            source_url="https://www.hepsiburada.com/ara?q=sm-x620",
            limit=10,
        )

        self.assertEqual(len(offers), 4)
        self.assertEqual([offer.price for offer in offers], [
            Decimal("18049"),
            Decimal("18099"),
            Decimal("18399"),
            Decimal("22923.32"),
        ])
        self.assertTrue(all(offer.url and "/samsung-galaxy-tab" in offer.url for offer in offers))
        title_by_url = {offer.url: offer.title for offer in offers}
        self.assertTrue(title_by_url["https://www.hepsiburada.com/samsung-galaxy-tab-s10-fe-gumus-p-HBCV00008GUMUS"].endswith("/ 128 GB / Gümüş"))
        self.assertTrue(title_by_url["https://www.hepsiburada.com/samsung-galaxy-tab-s10-fe-mavi-p-HBCV00008MAVI"].endswith("/ 128 GB / Mavi"))
        self.assertTrue(title_by_url["https://www.hepsiburada.com/samsung-galaxy-tab-s10-fe-gri-p-HBCV00008GRI"].endswith("/ 128 GB / Gri"))
        self.assertTrue(title_by_url["https://www.hepsiburada.com/samsung-galaxy-tab-s10-fe-mavi-256gb-p-HBCV00008256GB"].endswith("/ 256 GB / Mavi"))
        self.assertFalse(any("/ Renk" in offer.title or "/ Kapasite" in offer.title for offer in offers))

    def test_hepsiburada_embedded_prefers_premium_price(self):
        html = """
        <html><head><title>Samsung Galaxy Tab S10 FE+</title></head>
        <body>
          <script>
            window.__HB_STATE__ = {
              "variantListing": [
                {"aiBasedShipmentDay": null, "listingId": "listing-hb", "merchantName": "Hepsiburada",
                 "finalPriceOnSale": 18199,
                 "minimumPrices": [
                   {"name": "non-segmented-price", "value": 18199},
                   {"name": "Premium ile", "value": 17949}
                 ]}
              ]
            };
          </script>
        </body></html>
        """

        offer = extract_hepsiburada_offer(html)

        self.assertEqual(offer.price, Decimal("17949"))

    def test_hepsiburada_embedded_prefers_premium_special_price(self):
        html = """
        <html><head><title>Samsung Galaxy Tab S10 FE+</title></head>
        <body>
          <script>
            window.__HB_STATE__ = {
              "variantListing": [
                {"aiBasedShipmentDay": null, "listingId": "listing-hb", "merchantName": "Hepsiburada",
                 "finalPriceOnSale": 18199,
                 "minimumPrices": [
                   {"name": "non-segmented-price", "value": 18199},
                   {"name": "Premium'a özel fiyat", "value": 17949}
                 ]}
              ]
            };
          </script>
        </body></html>
        """

        offer = extract_hepsiburada_offer(html)

        self.assertEqual(offer.price, Decimal("17949"))

    def test_hepsiburada_embedded_prefers_nested_premium_price(self):
        html = """
        <html><head><title>Samsung Galaxy Tab S10 FE+</title></head>
        <body>
          <script>
            window.__HB_STATE__ = {
              "variantListing": [
                {"aiBasedShipmentDay": null, "listingId": "listing-hb", "merchantName": "Hepsiburada",
                 "finalPriceOnSale": 18199,
                 "premiumCampaign": {
                   "label": "Premium ile",
                   "price": {"value": 17949}
                 }}
              ]
            };
          </script>
        </body></html>
        """

        offer = extract_hepsiburada_offer(html)

        self.assertEqual(offer.price, Decimal("17949"))

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

    def test_hepsiburada_variant_urls_are_discovered_without_listing_urls(self):
        html = """
        <html><body>
          <div aria-label="Renk seçenekleri">
            <a href="/samsung-galaxy-tab-s10-fe-mavi-p-HBCV00008E1SXR">Mavi</a>
            <a href="/samsung-galaxy-tab-s10-fe-gri-p-HBCV00008E1ABC">Gri</a>
            <a href="/samsung-galaxy-tab-s10-fe-gri-p-HBCV00008E1ABC?magaza=Teknosa">Gri kopya</a>
            <a href="/samsung-galaxy-tab-s10-fe-gri-degerlendirmeleri-p-HBCV00008E1ABC">Yorumlar</a>
            <a href="https://com.pozitron.hepsiburada/https/www.hepsiburada.com/samsung-galaxy-tab-s10-fe-gumus-p-HBCV00008E1BAD">Uygulama linki</a>
          </div>
          <script>
            {"variantListing":[
              {"merchantName":"Hepsiburada","url":"/satici-link-p-HBCV00008E1BAD","finalPriceOnSale":18999}
            ]}
          </script>
        </body></html>
        """
        urls = extract_variant_urls(
            html,
            "https://www.hepsiburada.com/samsung-galaxy-tab-s10-fe-mavi-p-HBCV00008E1SXR",
        )

        self.assertEqual(len(urls), 2)
        self.assertIn("https://www.hepsiburada.com/samsung-galaxy-tab-s10-fe-gri-p-HBCV00008E1ABC", urls)
        self.assertFalse(any("BAD" in url for url in urls))

    def test_hepsiburada_selected_variant_label_is_added_to_title(self):
        html = """
        <html><body>
          <main>
            <h1>Samsung Galaxy Tab S10 FE+ 8GB 128GB SM-X620</h1>
            <span>Renk:</span><strong>Gümüş</strong>
            <button>Sepete ekle</button>
          </main>
          <section>Ürün Bilgileri</section>
        </body></html>
        """
        label = extract_selected_variant_label(html)
        title = title_with_variant_label("Samsung Galaxy Tab S10 FE+ 8GB 128GB SM-X620", label)

        self.assertEqual(label, "Gümüş")
        self.assertTrue(title.endswith("/ Gümüş"))

    def test_hepsiburada_selected_variant_label_combines_color_and_capacity(self):
        html = """
        <html><body>
          <main>
            <h1>Samsung Galaxy Tab S11 Ultra</h1>
            <span>Renk:</span><strong>Gri</strong>
            <span>Kapasite:</span><strong>512 GB</strong>
            <button>Sepete ekle</button>
          </main>
          <section>Ürün Bilgileri</section>
        </body></html>
        """

        self.assertEqual(extract_selected_variant_labels(html), ["Gri", "512 GB"])
        self.assertEqual(extract_selected_variant_label(html), "Gri / 512 GB")

    def test_hepsiburada_variant_label_strips_field_names(self):
        title = title_with_variant_label(
            "Nordbron Stark Deri Sırt Çantası",
            "Renk / Antrasit",
        )
        tablet_title = title_with_variant_label(
            "Samsung Galaxy Tab S10 FE+",
            "Kapasite / 128 GB / Renk",
        )

        self.assertEqual(title, "Nordbron Stark Deri Sırt Çantası / Antrasit")
        self.assertEqual(tablet_title, "Samsung Galaxy Tab S10 FE+ / 128 GB")

    def test_hepsiburada_display_title_strips_legacy_field_names(self):
        self.assertEqual(
            clean_display_title(
                "Nordbron Stark Deri Sırt Çantası Su İtici Özellikli Orta Boy Çok Gözlü Günlük Kullanım İçin / Renk / Antrasit"
            ),
            "Nordbron Stark Sırt Çantası / Antrasit",
        )
        self.assertEqual(
            clean_display_title(
                "Nordbron Stark Deri Sırt Çantası Su İtici Özellikli Orta Boy Çok Gözlü Günlük Kullanım İçin / Nordbron Stark Sırt Çantası / Renk / Lacivert"
            ),
            "Nordbron Stark Sırt Çantası / Lacivert",
        )
        self.assertEqual(
            clean_display_title(
                "Samsung Galaxy Tab S10 FE+ 8GB 128GB SM-X620 (Samsung Türkiye Garantili) / Kapasite / 128 GB / Renk"
            ),
            "Samsung Galaxy Tab S10 FE+ 8GB 128GB SM-X620 (Samsung Türkiye Garantili) / 128 GB",
        )
        self.assertEqual(
            clean_display_title(
                "Nordbron Stark Deri Sırt Çantası Su İtici Özellikli Orta Boy Çok Gözlü Günlük Kullanım İçin / Bej"
            ),
            "Nordbron Stark Sırt Çantası / Bej",
        )

    def test_hepsiburada_search_offer_title_is_enriched_from_detail_variant(self):
        class FakeResponse:
            headers = {"content-type": "text/html; charset=utf-8"}
            text = """
            <html><body>
              <h1>Samsung Galaxy Tab S10 FE+ 8GB 128GB SM-X620</h1>
              <span>Kapasite:</span><strong>128 GB</strong>
              <span>Renk:</span><strong>Mavi</strong>
              <div>18.299,00 TL</div>
              <section>Ürün Bilgileri</section>
            </body></html>
            """
            content = text.encode("utf-8")

            def raise_for_status(self):
                return None

        original_fetch = service.fetch_hepsiburada_page
        try:
            service.fetch_hepsiburada_page = lambda *_args, **_kwargs: FakeResponse()
            config = HermesConfig(
                interval_seconds=30,
                request_timeout_seconds=5,
                request_delay_min_seconds=1,
                request_delay_max_seconds=1,
                pushover_user_key="",
                pushover_api_token="",
                watches=[],
                telegram=TelegramConfig(False, None, "", "", "", "", [], [], []),
            )
            enriched = service._enrich_hepsiburada_search_offer_titles(
                requests.Session(),
                [
                    OfferResult(
                        title="Samsung Galaxy Tab S10 FE+ 8GB 128GB SM-X620",
                        price=Decimal("18049"),
                        seller="Hepsiburada",
                        url="https://www.hepsiburada.com/samsung-tablet-p-HBCV00008E1SXR",
                    )
                ],
                config,
            )
        finally:
            service.fetch_hepsiburada_page = original_fetch

        self.assertEqual(
            enriched[0].title,
            "Samsung Galaxy Tab S10 FE+ 8GB 128GB SM-X620 / 128 GB / Mavi",
        )

    def test_hepsiburada_selected_variant_does_not_fall_back_to_other_variants(self):
        html = """
        <html><head><title>Samsung Galaxy Tab S11 Ultra Gri 512 GB</title></head>
        <body>
          <script>
            window.__HB_STATE__ = {
              "variants": [
                {"sku": "HBCV_SELECTED_WITHOUT_LISTING", "name": "Gri 512 GB"},
                {"sku": "HBCV_OTHER_VARIANT", "variantListing": [
                  {"listingId": "other-cheap", "merchantName": "Hepsiburada",
                   "finalPriceOnSale": 1000, "prices": [{"value": 1000}]}
                ]}
              ]
            };
          </script>
          <span>Renk:</span><strong>Gri</strong>
          <span>Kapasite:</span><strong>512 GB</strong>
          <div data-test-id="price-current-price">43.809,00 TL</div>
        </body></html>
        """
        offer = extract_hepsiburada_offer(
            html,
            source_url="https://www.hepsiburada.com/samsung-tablet-p-HBCV_SELECTED_WITHOUT_LISTING",
        )

        self.assertEqual(offer.price, Decimal("43809.00"))

    def test_hepsiburada_embedded_variant_offer_reads_requested_capacity(self):
        html = """
        <html><head><title>Samsung Galaxy Tab S11 Ultra</title></head>
        <body>
          <script>
            window.__HB_STATE__ = {
              "variants": [
                {"sku": "HBCV256GRI", "name": "Gri 256 GB", "variantListing": [
                  {"listingId": "v256", "merchantName": "cemil shop",
                   "finalPriceOnSale": 42499, "prices": [{"value": 42499}]}
                ]},
                {"sku": "HBCV512GRI", "name": "Gri 512 GB", "variantListing": [
                  {"listingId": "v512", "merchantName": "Hepsiburada",
                   "finalPriceOnSale": 54999, "prices": [{"value": 54999}],
                   "minimumPrices": [{"name": "non-segmented-price", "value": 54999}]}
                ]},
                {"sku": "HBCV1TBGRI", "name": "Gri 1 TB", "variantListing": [
                  {"listingId": "v1tb", "merchantName": "Hepsiburada",
                   "finalPriceOnSale": 68999, "prices": [{"value": 68999}]}
                ]}
              ]
            };
          </script>
        </body></html>
        """
        offer = extract_embedded_variant_offer(
            html,
            "https://www.hepsiburada.com/samsung-tablet-p-HBCV512GRI",
        )

        self.assertIsNotNone(offer)
        self.assertEqual(offer.price, Decimal("54999"))
        self.assertEqual(offer.seller, "Hepsiburada")
        self.assertEqual(extract_embedded_variant_label(html, "https://www.hepsiburada.com/samsung-tablet-p-HBCV512GRI"), "Gri 512 GB")
        self.assertNotIn(
            "non-segmented-price",
            extract_embedded_variant_label(html, "https://www.hepsiburada.com/samsung-tablet-p-HBCV512GRI"),
        )

    def test_hepsiburada_embedded_variant_label_uses_values_not_field_names(self):
        html = """
        <html><head><title>Nordbron Stark Deri Sırt Çantası</title></head>
        <body>
          <script>
            window.__HB_STATE__ = {
              "variants": [
                {"sku": "HBCVSTARKANTRASIT", "attributes": [
                  {"name": "Renk", "value": "Antrasit"}
                ], "variantListing": [
                  {"listingId": "v1", "merchantName": "Hepsiburada",
                   "finalPriceOnSale": 4675, "prices": [{"value": 4675}]}
                ]},
                {"sku": "HBCVTABLET128", "attributes": [
                  {"name": "Kapasite", "value": "128 GB"},
                  {"name": "Renk"}
                ], "variantListing": [
                  {"listingId": "v2", "merchantName": "Hepsiburada",
                   "finalPriceOnSale": 18199, "prices": [{"value": 18199}]}
                ]}
              ]
            };
          </script>
        </body></html>
        """

        self.assertEqual(
            extract_embedded_variant_label(html, "https://www.hepsiburada.com/nordbron-p-HBCVSTARKANTRASIT"),
            "Antrasit",
        )
        self.assertEqual(
            extract_embedded_variant_label(html, "https://www.hepsiburada.com/tablet-p-HBCVTABLET128"),
            "128 GB",
        )

    def test_hepsiburada_variant_identity_keeps_same_seller_price_variants(self):
        silver_identity = service.normalize_offer_text("Gümüş 128 GB")
        gray_identity = service.normalize_offer_text("Gri 128 GB")

        self.assertNotEqual(
            (
                silver_identity,
                service.normalize_offer_text("VATAN BİLGİSAYAR"),
                "18399",
                service.normalize_offer_text("Samsung Galaxy Tab S10 FE+ / Gümüş 128 GB"),
            ),
            (
                gray_identity,
                service.normalize_offer_text("VATAN BİLGİSAYAR"),
                "18399",
                service.normalize_offer_text("Samsung Galaxy Tab S10 FE+ / Gri 128 GB"),
            ),
        )

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

    def test_zara_product_page_is_not_misread_as_captcha(self):
        html = """
        <html>
          <body>
            <script type="application/ld+json">{"@type":"Product","name":"Zara ürün"}</script>
            <script>{"customerSettings":{"requireCaptchaValidation":true},"label":"robot"}</script>
          </body>
        </html>
        """
        self.assertFalse(service.is_bot_protection_page("zara", html))
        self.assertTrue(service.is_bot_protection_page("zara", "bm-verify _sec/verify"))

    def test_watch_card_can_expand_to_multiple_site_links(self):
        watches = _prepare_watches(
            [
                {
                    "name": "Ortak ürün",
                    "target_price": 1000,
                    "url_1": "https://www.amazon.com.tr/dp/B000000001",
                    "url_2": "https://www.hepsiburada.com/ornek-urun-p-HBCV000000000",
                    "url_3": "https://nordbron.com/stark-sirt-cantasi",
                    "size": "M",
                    "notify_once_in_24H": True,
                    "active": True,
                }
            ]
        )
        self.assertEqual(len(watches), 3)
        self.assertEqual([item.site for item in watches], ["amazon", "hepsiburada", "nordbron"])
        self.assertTrue(all(item.name == "Ortak ürün" for item in watches))
        self.assertTrue(all(item.size == "M" for item in watches))

    def test_watch_card_detects_site_from_url(self):
        watches = _prepare_watches(
            [
                {
                    "name": "Yeni ürün",
                    "target_price": 1000,
                    "url_1": "https://www.trendyol.com/ornek/urun-p-1",
                    "active": True,
                }
            ]
        )
        self.assertEqual(len(watches), 1)
        self.assertEqual(watches[0].site, "trendyol")

    def test_zara_site_detection(self):
        url = "https://www.zara.com/tr/tr/dokulu-regular-fit-polo-t-shirt-p03166301.html?v1=567184888"
        self.assertEqual(detect_site_from_url(url), "zara")

    def test_zara_size_filter_reads_only_available_size(self):
        html = """
        <html><body>
          <script type="application/ld+json">
          {
            "@type": "Product",
            "name": "DOKULU REGULAR FIT POLO T-SHIRT",
            "color": "sarımsı kahverengi",
            "hasVariant": [
              {
                "@type": "Product",
                "size": "M (US M)",
                "color": "sarımsı kahverengi",
                "offers": {
                  "@type": "Offer",
                  "price": "1290",
                  "priceCurrency": "TRY",
                  "availability": "https://schema.org/LimitedAvailability",
                  "url": "https://www.zara.com/tr/tr/m"
                }
              },
              {
                "@type": "Product",
                "size": "L (US L)",
                "color": "sarımsı kahverengi",
                "offers": {
                  "@type": "Offer",
                  "price": "1290",
                  "priceCurrency": "TRY",
                  "availability": "https://schema.org/OutOfStock",
                  "url": "https://www.zara.com/tr/tr/l"
                }
              }
            ]
          }
          </script>
        </body></html>
        """

        offers = extract_zara_offers(html, source_url="https://www.zara.com/tr/tr/product", size="M")

        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].price, Decimal("1290"))
        self.assertEqual(offers[0].seller, "Zara")
        self.assertIn("M (US M)", offers[0].title)
        self.assertIn("sarımsı kahverengi", offers[0].title)

    def test_zara_size_filter_rejects_out_of_stock_size(self):
        html = """
        <html><body>
          <script type="application/ld+json">
          {
            "@type": "Product",
            "name": "DOKULU REGULAR FIT POLO T-SHIRT",
            "hasVariant": [
              {
                "@type": "Product",
                "size": "L (US L)",
                "offers": {
                  "@type": "Offer",
                  "price": "1290",
                  "availability": "https://schema.org/OutOfStock"
                }
              }
            ]
          }
          </script>
        </body></html>
        """

        with self.assertRaisesRegex(Exception, "stokta"):
            extract_zara_offers(html, source_url="https://www.zara.com/tr/tr/product", size="L")

    def test_zara_blank_size_uses_lowest_available_offer(self):
        html = """
        <html><body>
          <script type="application/ld+json">
          {
            "@type": "Product",
            "name": "DOKULU REGULAR FIT POLO T-SHIRT",
            "hasVariant": [
              {
                "@type": "Product",
                "size": "M (US M)",
                "offers": {
                  "@type": "Offer",
                  "price": "1290",
                  "availability": "https://schema.org/LimitedAvailability"
                }
              },
              {
                "@type": "Product",
                "size": "S (US S)",
                "offers": {
                  "@type": "Offer",
                  "price": "990",
                  "availability": "https://schema.org/InStock"
                }
              }
            ]
          }
          </script>
        </body></html>
        """

        offers = extract_zara_offers(html, source_url="https://www.zara.com/tr/tr/product")

        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].price, Decimal("990"))

    def test_zara_variant_only_jsonld_title_is_not_duplicated(self):
        html = """
        <html><body>
          <script type="application/ld+json">
          {
            "@type": "Product",
            "name": "DOKULU REGULAR FIT POLO T-SHIRT - sarımsı kahverengi - M (US M)",
            "size": "M (US M)",
            "color": "sarımsı kahverengi",
            "offers": {
              "@type": "Offer",
              "price": "1290",
              "availability": "https://schema.org/LimitedAvailability"
            }
          }
          </script>
        </body></html>
        """

        offers = extract_zara_offers(html, source_url="https://www.zara.com/tr/tr/product", size="M")

        self.assertEqual(offers[0].title, "DOKULU REGULAR FIT POLO T-SHIRT / sarımsı kahverengi / M (US M)")

    def test_zara_requested_size_returns_each_available_color(self):
        html = """
        <html><body>
          <script type="application/ld+json">
          [
            {
              "@type": "Product",
              "name": "DOKULU REGULAR FIT POLO T-SHIRT - sarımsı kahverengi - M (US M)",
              "sku": "567184888-707-3",
              "size": "M (US M)",
              "color": "sarımsı kahverengi",
              "offers": {
                "@type": "Offer",
                "price": "1290",
                "availability": "https://schema.org/InStock",
                "url": "https://www.zara.com/tr/tr/product.html?v1=567184888"
              }
            },
            {
              "@type": "Product",
              "name": "DOKULU REGULAR FIT POLO T-SHIRT - Koyu pembe - M (US M)",
              "sku": "567184888-664-3",
              "size": "M (US M)",
              "color": "Koyu pembe",
              "offers": {
                "@type": "Offer",
                "price": "1290",
                "availability": "https://schema.org/InStock",
                "url": "https://www.zara.com/tr/tr/product.html?v1=567184887"
              }
            }
          ]
          </script>
        </body></html>
        """

        offers = extract_zara_offers(
            html,
            source_url="https://www.zara.com/tr/tr/product.html?v1=567184888",
            size="M",
        )

        self.assertEqual(len(offers), 2)
        self.assertEqual(
            [offer.title for offer in offers],
            [
                "DOKULU REGULAR FIT POLO T-SHIRT / sarımsı kahverengi / M (US M)",
                "DOKULU REGULAR FIT POLO T-SHIRT / Koyu pembe / M (US M)",
            ],
        )


if __name__ == "__main__":
    unittest.main()
