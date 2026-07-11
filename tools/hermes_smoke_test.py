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
from hermes import dashboard  # noqa: E402
from hermes import dashboard_with_settings  # noqa: E402
from hermes import settings_ui  # noqa: E402
from hermes.errors import HermesError, OutOfStockHermesError  # noqa: E402
from hermes.http_client import amazon_url_variants, fetch_amazon_page  # noqa: E402
from hermes.config_loader import _prepare_watches  # noqa: E402
from hermes.models import HermesConfig, OfferResult, PriceSummaryRow, SearchResultItem, StockSummaryRow, TelegramConfig  # noqa: E402
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
from hermes.providers.hm import extract_offers as extract_hm_offers  # noqa: E402
from hermes.providers.nordbron import extract_offer as extract_nordbron_offer  # noqa: E402
from hermes.providers.zara import extract_offers as extract_zara_offers  # noqa: E402
from hermes.search_amazon import extract_result_candidates  # noqa: E402
from hermes.utils import detect_site_from_url, parse_decimal  # noqa: E402


class HermesSmokeTests(unittest.TestCase):
    def test_dashboard_site_theme_classes_are_distinct_for_supported_providers(self):
        expected = {
            "Amazon": "site-amazon",
            "Hepsiburada": "site-hepsiburada",
            "Trendyol": "site-trendyol",
            "Network": "site-network",
            "Nordbron": "site-nordbron",
            "Zara": "site-zara",
            "H&M": "site-hm",
        }
        self.assertEqual(
            {seller: dashboard._site_theme_class(seller) for seller in expected},
            expected,
        )

    def test_summary_drop_alert_requires_a_meaningful_product_loss(self):
        self.assertEqual(service.summary_drop_threshold(18), 6)
        self.assertEqual(service.summary_drop_threshold(23), 8)
        self.assertFalse(18 - 14 >= service.summary_drop_threshold(18))
        self.assertTrue(18 - 11 >= service.summary_drop_threshold(18))

    def test_dashboard_collapses_multi_result_search_groups(self):
        rows = [
            {
                "seller": "Amazon",
                "product_title": "Juo Q3 Yeşil",
                "product_url": "https://example.test/green",
                "price": "2.037,00",
                "target": "2.000,00",
                "difference": "+37,00",
                "price_range": "2.037,00 / 2.037,00",
                "search_group": "amazon_juo_q3",
                "search_group_label": "Juo Q3",
            },
            {
                "seller": "Amazon",
                "product_title": "Juo Q3 Kırmızı",
                "product_url": "https://example.test/red",
                "price": "2.099,00",
                "target": "2.000,00",
                "difference": "+99,00",
                "price_range": "2.099,00 / 2.099,00",
                "search_group": "amazon_juo_q3",
                "search_group_label": "Juo Q3",
            },
        ]

        rendered = dashboard._render_table_section(
            "Hedefin Üstünde Kalan Ürünler",
            rows,
            "Boş",
            collapse_search_results=True,
        )

        self.assertIn('<details class="search-result-group">', rendered)
        self.assertIn("Juo Q3", rendered)
        self.assertIn("2 sonuç", rendered)

    def test_dashboard_rebuilds_missing_search_groups_from_state(self):
        rows = [
            {"product_url": "https://www.amazon.com.tr/dp/GREEN", "product_title": "Juo Q3 Yeşil"},
            {"product_url": "https://www.amazon.com.tr/dp/RED", "product_title": "Juo Q3 Kırmızı"},
        ]
        state = {
            "first": {
                "site": "amazon",
                "configured_url": "https://www.amazon.com.tr/s?k=juo+q3",
                "url": "https://www.amazon.com.tr/dp/GREEN",
                "watch_name": "Juo Q3",
            },
            "second": {
                "site": "amazon",
                "configured_url": "https://www.amazon.com.tr/s?k=juo+q3",
                "url": "https://www.amazon.com.tr/dp/RED",
                "watch_name": "Juo Q3",
            },
        }

        enriched = dashboard._attach_legacy_search_groups(rows, state)

        self.assertTrue(all(row["search_group"] for row in enriched))
        self.assertEqual([row["search_group_label"] for row in enriched], ["Juo Q3", "Juo Q3"])

    def test_public_settings_restart_paths_keep_the_public_token(self):
        context = dashboard_with_settings._public_settings_context("/public/secret-token/settings/save")

        self.assertEqual(context["settings_path"], "/public/secret-token/settings")
        self.assertEqual(context["restart_path"], "/public/secret-token/settings/restarting")
        self.assertEqual(context["health_path"], "/public/secret-token/health")

        page = settings_ui.render_settings_restart_page(
            "Ayarlar kaydedildi.",
            settings_path=context["settings_path"],
            health_path=context["health_path"],
        ).decode("utf-8")
        self.assertIn("/public/secret-token/settings", page)
        self.assertIn("/public/secret-token/health", page)

    def test_settings_page_shows_saving_overlay_for_each_save_form(self):
        original_options_path = settings_ui.OPTIONS_PATH
        original_state_path = settings_ui.STATE_PATH
        original_summary_path = settings_ui.SUMMARY_PATH
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                settings_ui.OPTIONS_PATH = Path(tmpdir) / "options.json"
                settings_ui.STATE_PATH = Path(tmpdir) / "state.json"
                settings_ui.SUMMARY_PATH = Path(tmpdir) / "summary.json"
                settings_ui.OPTIONS_PATH.write_text(json.dumps({"takip_edilenler": []}))

                page = settings_ui.render_settings_page("/public/secret-token/settings").decode("utf-8")

                self.assertIn('id="saving-overlay"', page)
                self.assertEqual(page.count("data-settings-save"), 2)
                self.assertIn("Ayarlar kaydediliyor", page)
            finally:
                settings_ui.OPTIONS_PATH = original_options_path
                settings_ui.STATE_PATH = original_state_path
                settings_ui.SUMMARY_PATH = original_summary_path

    def test_new_search_watch_without_a_name_is_rejected(self):
        form = {
            "watches_count": ["1"],
            "watches_0_target_price": ["2000"],
            "watches_0_url_1": ["https://www.amazon.com.tr/s?k=juo+q3"],
            "watches_0_notify_once_in_24H": ["1"],
            "watches_0_active": ["1"],
        }

        with self.assertRaisesRegex(ValueError, "arama sayfası"):
            settings_ui._build_watches(form)

    def test_new_watch_form_is_rendered_before_existing_watches(self):
        original_options_path = settings_ui.OPTIONS_PATH
        original_state_path = settings_ui.STATE_PATH
        original_summary_path = settings_ui.SUMMARY_PATH
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                settings_ui.OPTIONS_PATH = Path(tmpdir) / "options.json"
                settings_ui.STATE_PATH = Path(tmpdir) / "state.json"
                settings_ui.SUMMARY_PATH = Path(tmpdir) / "summary.json"
                settings_ui.OPTIONS_PATH.write_text(
                    json.dumps(
                        {
                            "takip_edilenler": [
                                {
                                    "name": "Mevcut",
                                    "target_price": 100,
                                    "url_1": "https://www.amazon.com.tr/dp/B000000001",
                                }
                            ]
                        }
                    )
                )

                page = settings_ui.render_settings_page().decode("utf-8")

                self.assertLess(page.index("Yeni takip ekle"), page.index("Takip edilenler"))
                self.assertIn("id='watch-search'", page)
                self.assertIn("data-watch-search='Mevcut'", page)
                self.assertIn("class='button danger'", page)
                self.assertIn("name='delete_watch_index'", page)
                self.assertIn("name='update_watch_index'", page)
                self.assertNotIn("Güncellemeleri Kaydet", page)
                self.assertIn("value='100'", page)
            finally:
                settings_ui.OPTIONS_PATH = original_options_path
                settings_ui.STATE_PATH = original_state_path
                settings_ui.SUMMARY_PATH = original_summary_path

    def test_direct_watch_delete_keeps_other_watches_unchanged(self):
        options, message = settings_ui._apply_settings_operation(
            {
                "takip_edilenler": [
                    {"name": "Silinecek", "target_price": 100, "url_1": "https://www.amazon.com.tr/dp/B000000001"},
                    {"name": "Kalacak", "target_price": 200, "url_1": "https://www.amazon.com.tr/dp/B000000002"},
                ]
            },
            {"operation": ["update_existing"], "delete_watch_index": ["0"]},
        )

        self.assertEqual(message, "Silinecek takip kaydı silindi.")
        self.assertEqual([item["name"] for item in options["takip_edilenler"]], ["Kalacak"])

    def test_card_update_only_changes_the_selected_watch(self):
        options, message = settings_ui._apply_settings_operation(
            {
                "takip_edilenler": [
                    {"name": "İlk", "group": "Diğer", "target_price": 100, "url_1": "https://www.amazon.com.tr/dp/B000000001"},
                    {"name": "İkinci", "group": "Moda", "target_price": 200, "url_1": "https://www.zara.com/tr/tr/ornek-p03166301.html"},
                ]
            },
            {
                "operation": ["update_watch"],
                "update_watch_index": ["1"],
                "watches_1_name": ["İkinci"],
                "watches_1_group": ["Teknoloji"],
                "watches_1_target_price": ["1.500"],
                "watches_1_url_1": ["https://www.zara.com/tr/tr/ornek-p03166301.html"],
                "watches_1_notify_once_in_24H": ["1"],
                "watches_1_active": ["1"],
            },
        )

        self.assertIn("güncellendi", message)
        self.assertEqual(options["takip_edilenler"][0]["target_price"], 100)
        self.assertEqual(options["takip_edilenler"][1]["group"], "Teknoloji")
        self.assertEqual(options["takip_edilenler"][1]["target_price"], 1500)

    def test_displayed_prices_use_whole_lira_with_tl_suffix(self):
        self.assertEqual(parse_decimal("1.500"), Decimal("1500"))
        self.assertEqual(settings_ui._price_input_value("3000,0"), "3.000")
        self.assertEqual(dashboard._display_tl("1.500,75"), "1.500 TL")
        self.assertEqual(dashboard._display_tl("+125,90", signed=True), "+125 TL")
        self.assertEqual(dashboard._display_tl_range("1.500,75 / 2.000,01"), "1.500 TL / 2.000 TL")

    def test_settings_mutations_preserve_required_supervisor_options(self):
        source = {
            "interval_seconds": 10,
            "request_delay_min_seconds": 1,
            "request_delay_max_seconds": 2,
            "pushover_user_key": "user",
            "pushover_api_token": "token",
            "telegram_enabled": True,
            "api_id": "123",
            "api_hash": "hash",
            "phone_number": "+900000000000",
            "verification_code": "",
            "session_name": "telegram_keyword_alert",
            "channels": ["@example"],
            "keywords": ["fırsat"],
            "exclude_keywords": ["hariç"],
            "gruplar": ["Moda"],
            "takip_edilenler": [
                {"name": "Silinecek", "target_price": 100, "url_1": "https://www.amazon.com.tr/dp/B000000001"},
            ],
        }

        options, _ = settings_ui._apply_settings_operation(
            source,
            {"operation": ["update_existing"], "delete_watch_index": ["0"]},
        )

        self.assertEqual(options["channels"], ["@example"])
        self.assertEqual(options["keywords"], ["fırsat"])
        self.assertTrue(options["telegram_enabled"])
        self.assertEqual(options["takip_edilenler"], [])

    def test_settings_assets_are_external_and_shared_by_both_surfaces(self):
        page = settings_ui.render_settings_page().decode("utf-8")
        restart_page = settings_ui.render_settings_restart_page("Kaydedildi.").decode("utf-8")
        interaction_script = settings_ui.render_settings_script().decode("utf-8")
        restart_script = settings_ui.render_settings_restart_script().decode("utf-8")

        self.assertIn('<script src="./settings.js" defer>', page)
        self.assertIn('src="./restart.js" defer', restart_page)
        self.assertIn("watchSearch?.addEventListener('input', refreshWatchList)", interaction_script)
        self.assertIn("data-watch-group-filter", interaction_script)
        self.assertIn("waitForHermes", restart_script)

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

    def test_amazon_search_ignores_all_departments_fallback_section(self):
        html = """
        <div class="s-main-slot">
          <div data-component-type="s-search-result" data-asin="B000000001">
            <h2><a href="/dp/B000000001"><span>Depo sonucu iPad</span></a></h2>
            <span class="a-price"><span class="a-offscreen">30.000,00 TL</span></span>
          </div>
          <div>All Departments içindeki sonuçlar gösteriliyor</div>
          <div data-component-type="s-search-result" data-asin="B000000002">
            <h2><a href="/dp/B000000002"><span>Alakasız stok dışı ürün</span></a></h2>
            <span class="a-price"><span class="a-offscreen">1.000,00 TL</span></span>
          </div>
        </div>
        """
        items = extract_result_candidates(html, 10)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Depo sonucu iPad")

    def test_amazon_search_url_can_be_used_as_product_url(self):
        self.assertTrue(service.is_amazon_search_url("https://www.amazon.com.tr/s?k=juo+q3"))
        self.assertFalse(service.is_amazon_search_url("https://www.amazon.com.tr/dp/B000000001"))

    def test_product_amazon_search_returns_all_matching_offers(self):
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
        offers = service.offers_from_amazon_search_results(results, "juo q3")
        self.assertEqual([offer.price for offer in offers], [Decimal("2037.00"), Decimal("2099.00")])
        self.assertEqual(
            [offer.url for offer in offers],
            [
                "https://www.amazon.com.tr/dp/B000000001",
                "https://www.amazon.com.tr/dp/B000000002",
            ],
        )

    def test_amazon_search_keeps_distinct_variation_links(self):
        html = """
        <div class="s-main-slot">
          <div data-component-type="s-search-result" data-asin="B000000001">
            <h2><a href="/dp/B000000001?th=1"><span>Juo Q3 Yeşil</span></a></h2>
            <span class="a-price"><span class="a-offscreen">2.037,00 TL</span></span>
          </div>
          <div data-component-type="s-search-result" data-asin="B000000001">
            <h2><a href="/dp/B000000001?th=2"><span>Juo Q3 Kırmızı</span></a></h2>
            <span class="a-price"><span class="a-offscreen">2.099,00 TL</span></span>
          </div>
        </div>
        """
        items = extract_result_candidates(html, 10)

        self.assertEqual(len(items), 2)
        self.assertEqual(
            [item.url for item in items],
            [
                "https://www.amazon.com.tr/dp/B000000001?th=1",
                "https://www.amazon.com.tr/dp/B000000001?th=2",
            ],
        )

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

    def test_stock_missing_rows_are_saved_separately_from_price_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_summary_path = service.SUMMARY_PATH
            try:
                service.SUMMARY_PATH = Path(tmpdir) / "latest_price_summary.json"
                service.save_price_summary(
                    [],
                    [
                        StockSummaryRow(
                            seller="Zara",
                            product_title="Polo T-Shirt / M",
                            product_url="https://example.com/zara",
                            target_price=Decimal("1290"),
                            reason="Zara beden stokta değil: M",
                        )
                    ],
                )
                payload = json.loads(service.SUMMARY_PATH.read_text(encoding="utf-8"))
                self.assertEqual(payload["row_count"], 0)
                self.assertEqual(payload["stock_row_count"], 1)
                self.assertEqual(payload["stock_rows"][0]["seller"], "Zara")
                self.assertEqual(payload["stock_rows"][0]["reason"], "Zara beden stokta değil: M")
            finally:
                service.SUMMARY_PATH = original_summary_path

    def test_stock_missing_rows_are_collapsed_by_site(self):
        html = dashboard._render_stock_section(
            [
                {"seller": "Zara", "product_title": "Polo / M", "target": "1.500 TL"},
                {"seller": "Zara", "product_title": "Gömlek / XL", "target": "1.500 TL"},
                {"seller": "H&M", "product_title": "Pantolon / L", "target": "1.200 TL"},
            ]
        )

        self.assertEqual(html.count('class="search-result-group stock-site-group"'), 2)
        self.assertIn("Zara</strong><span>2 ürün", html)
        self.assertIn("H&amp;M</strong><span>1 ürün", html)

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
                    "group": "Moda",
                    "size": "M",
                    "notify_once_in_24H": True,
                    "active": True,
                }
            ]
        )
        self.assertEqual(len(watches), 3)
        self.assertEqual([item.site for item in watches], ["amazon", "hepsiburada", "nordbron"])
        self.assertTrue(all(item.name == "Ortak ürün" for item in watches))
        self.assertTrue(all(item.group == "Moda" for item in watches))
        self.assertTrue(all(item.size == "M" for item in watches))

    def test_summary_keeps_one_row_for_an_identical_product_link(self):
        rows = [
            PriceSummaryRow("Amazon", "Ürün", "https://example.test/product", Decimal("100"), Decimal("90"), Decimal("100"), Decimal("100")),
            PriceSummaryRow("Amazon", "Ürün", "https://example.test/product", Decimal("95"), Decimal("90"), Decimal("95"), Decimal("100")),
            PriceSummaryRow("Amazon", "Farklı varyasyon", "https://example.test/product?color=blue", Decimal("96"), Decimal("90"), Decimal("96"), Decimal("96")),
        ]

        unique_rows = service.deduplicate_summary_rows(rows)

        self.assertEqual(len(unique_rows), 2)
        self.assertEqual(unique_rows[0].price, Decimal("95"))

    def test_watch_settings_show_configured_groups_as_a_dropdown(self):
        html = settings_ui._watch_form(
            {"name": "Polo tişört", "group": "Moda"},
            0,
            groups=["Moda", "Teknoloji", "Market"],
        )

        self.assertIn("<select", html)
        self.assertIn("Moda", html)
        self.assertIn("Teknoloji", html)
        self.assertIn("Market", html)

    def test_watch_settings_keep_configured_groups_without_existing_watches(self):
        html = settings_ui._watch_section([], ["Moda", "Teknoloji", "Market"])

        self.assertIn("data-watch-group-filter='Moda'", html)
        self.assertIn("data-watch-group-filter='Teknoloji'", html)
        self.assertIn("data-watch-group-filter='Market'", html)

    def test_existing_zara_and_hm_watches_default_to_moda_group(self):
        watches = _prepare_watches(
            [
                {
                    "target_price": 1000,
                    "url_1": "https://www.zara.com/tr/tr/ornek-p03166301.html",
                    "active": True,
                },
                {
                    "target_price": 1000,
                    "url_1": "https://www2.hm.com/tr_tr/productpage.1286182003.html",
                    "active": True,
                },
                {
                    "target_price": 1000,
                    "url_1": "https://www.amazon.com.tr/dp/B000000001",
                    "active": True,
                },
            ]
        )

        self.assertEqual([watch.group for watch in watches], ["Moda", "Moda", ""])

    def test_settings_ignores_empty_new_watch_with_only_a_group_selected(self):
        watches = settings_ui._build_watches(
            {
                "watches_count": ["1"],
                "watches_0_group": ["Moda"],
                "watches_0_max_items_to_scan": ["24"],
                "watches_0_notify_once_in_24H": ["1"],
                "watches_0_active": ["1"],
            }
        )

        self.assertEqual(watches, [])

    def test_settings_error_identifies_watch_with_missing_required_fields(self):
        with self.assertRaisesRegex(ValueError, r"Takip 1 \(Eksik ürün\): en az bir link"):
            settings_ui._build_watches(
                {
                    "watches_count": ["1"],
                    "watches_0_name": ["Eksik ürün"],
                    "watches_0_target_price": ["1000"],
                }
            )

    def test_watch_settings_use_learned_title_when_name_is_blank(self):
        html = settings_ui._watch_form(
            {"url_1": "https://www.zara.com/tr/tr/ornek-p03166301.html"},
            8,
            groups=["Moda"],
            known_titles={
                "https://www.zara.com/tr/tr/ornek-p03166301.html": "DOKULU REGULAR FIT POLO T-SHIRT"
            },
        )

        self.assertIn("[Moda] DOKULU REGULAR FIT POLO T-SHIRT", html)

    def test_watch_settings_match_learned_titles_without_url_query_parameters(self):
        html = settings_ui._watch_form(
            {"url_1": "https://www.zara.com/tr/tr/dokulu-p03166301.html?v1=567"},
            8,
            groups=["Moda"],
            known_titles={
                "https://www.zara.com/tr/tr/dokulu-p03166301.html": "Dokulu Regular Fit Polo T-Shirt"
            },
        )

        self.assertIn("[Moda] Dokulu Regular Fit Polo T-Shirt", html)

    def test_settings_preserve_selected_watch_group(self):
        watches = settings_ui._build_watches(
            {
                "watches_count": ["1"],
                "watches_0_name": ["Gömlek"],
                "watches_0_group": ["Moda"],
                "watches_0_target_price": ["1000"],
                "watches_0_url_1": ["https://www.zara.com/tr/tr/gomlek-p01234567.html"],
            }
        )

        self.assertEqual(watches[0]["group"], "Moda")

    def test_settings_separate_existing_updates_from_new_watch_additions(self):
        existing_options = {
            "takip_edilenler": [
                {
                    "name": "Mevcut tablet",
                    "group": "Diğer",
                    "target_price": 1000,
                    "url_1": "https://www.amazon.com.tr/dp/B000000001",
                }
            ]
        }
        options, message = settings_ui._apply_settings_operation(
            existing_options,
            {
                "operation": ["update_existing"],
                "watches_count": ["1"],
                "watches_0_name": ["Mevcut tablet"],
                "watches_0_group": ["Teknoloji"],
                "watches_0_target_price": ["1000"],
                "watches_0_url_1": ["https://www.amazon.com.tr/dp/B000000001"],
            },
        )

        self.assertEqual(options["takip_edilenler"][0]["group"], "Teknoloji")
        self.assertIn("güncellendi", message)

        added_options, add_message = settings_ui._apply_settings_operation(
            options,
            {
                "operation": ["add_watch"],
                "watches_count": ["1"],
                "watches_0_name": ["Yeni gömlek"],
                "watches_0_group": ["Moda"],
                "watches_0_target_price": ["2000"],
                "watches_0_url_1": ["https://www.zara.com/tr/tr/gomlek-p01234567.html"],
            },
        )

        self.assertEqual(len(added_options["takip_edilenler"]), 2)
        self.assertEqual(added_options["takip_edilenler"][1]["group"], "Moda")
        self.assertIn("eklendi", add_message)

    def test_settings_use_out_of_stock_summary_title_for_blank_hm_watch_name(self):
        original_load_json = settings_ui.load_json

        def fake_load_json(path, _default):
            if path == settings_ui.SUMMARY_PATH:
                return {
                    "stock_rows": [
                        {
                            "product_url": "https://www2.hm.com/tr_tr/productpage.1286182003.html?color=009",
                            "product_title": "Keten Karışımlı Erkek Yaka Gömlek Regular Fit / Kahverengi / XL",
                        }
                    ]
                }
            return {}

        settings_ui.load_json = fake_load_json
        try:
            titles = settings_ui._stored_watch_titles()
        finally:
            settings_ui.load_json = original_load_json

        html = settings_ui._watch_form(
            {"url_1": "https://www2.hm.com/tr_tr/productpage.1286182003.html"},
            3,
            groups=["Moda"],
            known_titles=titles,
        )

        self.assertIn("Keten Karışımlı Erkek Yaka Gömlek Regular Fit", html)
        self.assertNotIn("WWW2 ürünü", html)

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

    def test_watch_name_is_optional_for_product_links(self):
        watches = _prepare_watches(
            [
                {
                    "target_price": 1000,
                    "url_1": "https://www.hepsiburada.com/ornek-urun-p-HBCV000000000",
                    "url_2": "https://www2.hm.com/tr_tr/productpage.1286182003.html",
                    "active": True,
                }
            ]
        )
        self.assertEqual(len(watches), 2)
        self.assertTrue(all(item.name == "" for item in watches))

    def test_watch_name_is_required_for_search_links(self):
        for url in (
            "https://www.amazon.com.tr/s?k=ipad",
            "https://www.hepsiburada.com/ara?q=sm-x620",
        ):
            with self.subTest(url=url):
                with self.assertRaisesRegex(HermesError, "Arama linkleri"):
                    _prepare_watches(
                        [
                            {
                                "target_price": 1000,
                                "url_1": url,
                                "active": True,
                            }
                        ]
                    )

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
        self.assertIn("M", offers[0].title)
        self.assertNotIn("US M", offers[0].title)
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

        with self.assertRaisesRegex(OutOfStockHermesError, "stokta") as caught:
            extract_zara_offers(html, source_url="https://www.zara.com/tr/tr/product", size="L")
        self.assertEqual(caught.exception.product_title, "DOKULU REGULAR FIT POLO T-SHIRT / L")
        self.assertEqual(caught.exception.product_url, "https://www.zara.com/tr/tr/product")

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

        self.assertEqual(offers[0].title, "DOKULU REGULAR FIT POLO T-SHIRT / sarımsı kahverengi / M")

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
                "DOKULU REGULAR FIT POLO T-SHIRT / sarımsı kahverengi / M",
                "DOKULU REGULAR FIT POLO T-SHIRT / Koyu pembe / M",
            ],
        )

    def test_zara_numeric_size_ignores_parenthetical_values(self):
        html = """
        <html><body>
          <script type="application/ld+json">
          [
            {
              "@type": "Product",
              "name": "REGULAR FIT DENIM BERMUDA - Kahverengi - EU 44 (US 34)",
              "size": "EU 44 (US 34)",
              "color": "Kahverengi",
              "offers": {"@type": "Offer", "price": "1190", "availability": "https://schema.org/InStock"}
            },
            {
              "@type": "Product",
              "name": "REGULAR FIT DENIM BERMUDA - Kahverengi - EU 46 (US 36)",
              "size": "EU 46 (US 36)",
              "color": "Kahverengi",
              "offers": {"@type": "Offer", "price": "1190", "availability": "https://schema.org/InStock"}
            }
          ]
          </script>
        </body></html>
        """

        offers = extract_zara_offers(html, source_url="https://www.zara.com/tr/tr/product", size="44")

        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].title, "REGULAR FIT DENIM BERMUDA / Kahverengi / EU 44")
        with self.assertRaisesRegex(Exception, "bulunamadı"):
            extract_zara_offers(html, source_url="https://www.zara.com/tr/tr/product", size="34")

    def test_zara_age_size_can_be_requested_as_number(self):
        html = """
        <html><body>
          <script type="application/ld+json">
          {
            "@type": "Product",
            "name": "ÇOCUK SWEATSHIRT - Lacivert - 6 yaş",
            "size": "6 yaş",
            "color": "Lacivert",
            "offers": {"@type": "Offer", "price": "790", "availability": "https://schema.org/InStock"}
          }
          </script>
        </body></html>
        """

        offers = extract_zara_offers(html, source_url="https://www.zara.com/tr/tr/product", size="6")

        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].title, "ÇOCUK SWEATSHIRT / Lacivert / 6 yaş")

    def test_hm_url_is_detected(self):
        self.assertEqual(
            detect_site_from_url("https://www2.hm.com/tr_tr/productpage.1285132002.html"),
            "hm",
        )

    def test_hm_requested_size_returns_each_available_color(self):
        html = """
        <html><body>
          <script type="application/json" id="hm-product-data">
          {
            "products": [
              {
                "name": "Lastik Örgülü Erkek Yaka Gömlek Loose Fit",
                "colorName": "Turkuaz",
                "url": "/tr_tr/productpage.1285132002.html",
                "price": {"formattedValue": "799,99 TL"},
                "sizes": [
                  {"name": "XS", "available": true},
                  {"name": "S", "available": true},
                  {"name": "M", "available": false},
                  {"name": "L", "availability": "Sold out"},
                  {"name": "XL", "available": true},
                  {"name": "XXL", "stock": 2}
                ]
              },
              {
                "name": "Lastik Örgülü Erkek Yaka Gömlek Loose Fit",
                "colorName": "Kahverengi",
                "url": "/tr_tr/productpage.1285132001.html",
                "price": {"formattedValue": "799,99 TL"},
                "sizes": [
                  {"name": "XS", "available": true},
                  {"name": "S", "available": true},
                  {"name": "XL", "available": true},
                  {"name": "XXL", "available": true}
                ]
              }
            ]
          }
          </script>
        </body></html>
        """

        xs_offers = extract_hm_offers(
            html,
            source_url="https://www2.hm.com/tr_tr/productpage.1285132002.html",
            size="XS",
        )
        self.assertEqual(
            [offer.title for offer in xs_offers],
            [
                "Lastik Örgülü Erkek Yaka Gömlek Loose Fit / Turkuaz / XS",
                "Lastik Örgülü Erkek Yaka Gömlek Loose Fit / Kahverengi / XS",
            ],
        )
        with self.assertRaisesRegex(OutOfStockHermesError, "stokta değil") as caught:
            extract_hm_offers(
                html,
                source_url="https://www2.hm.com/tr_tr/productpage.1285132002.html",
                size="M",
            )
        self.assertEqual(
            caught.exception.product_title,
            "Lastik Örgülü Erkek Yaka Gömlek Loose Fit / Turkuaz / M",
        )

    def test_hm_size_matrix_matches_expected_available_sizes(self):
        html = """
        <html><body>
          <script type="application/json">
          {
            "name": "Lastik Örgülü Erkek Yaka Gömlek Loose Fit",
            "colorName": "Turkuaz",
            "price": "799,99 TL",
            "sizes": [
              {"name": "XS", "available": true},
              {"name": "S", "available": true},
              {"name": "M", "available": false},
              {"name": "L", "available": false},
              {"name": "XL", "available": true},
              {"name": "XXL", "available": true}
            ]
          }
          </script>
        </body></html>
        """

        available_sizes = []
        for size in ("XS", "S", "M", "L", "XL", "XXL"):
            try:
                offers = extract_hm_offers(
                    html,
                    source_url="https://www2.hm.com/tr_tr/productpage.1285132002.html",
                    size=size,
                )
            except Exception:
                offers = []
            if offers:
                available_sizes.append(size)

        self.assertEqual(available_sizes, ["XS", "S", "XL", "XXL"])

    def test_hm_byids_api_shape_returns_requested_size_for_each_color(self):
        html = """
        <html><body>
          <script type="application/json" id="hm-product-data">
          {
            "products": [
              {
                "id": "1286182003",
                "productName": "Keten Karışımlı Erkek Yaka Gömlek Regular Fit",
                "colorName": "Koyu bej",
                "url": "/tr_tr/productpage.1286182003.html",
                "prices": [
                  {"priceType": "redPrice", "price": 579.0, "formattedPrice": "579,00 TL"},
                  {"priceType": "whitePrice", "price": 1999.0, "formattedPrice": "1.999,00 TL"}
                ],
                "sizes": [
                  {"label": "M", "stock": 0},
                  {"label": "S", "stock": 2},
                  {"label": "XS", "stock": 2},
                  {"label": "XXL", "stock": 0},
                  {"label": "XL", "stock": 0}
                ]
              },
              {
                "id": "1286182002",
                "productName": "Keten Karışımlı Erkek Yaka Gömlek Regular Fit",
                "colorName": "Adaçayı yeşili",
                "url": "/tr_tr/productpage.1286182002.html",
                "prices": [{"priceType": "redPrice", "price": 489.0, "formattedPrice": "489,00 TL"}],
                "sizes": [
                  {"label": "XS", "stock": 1},
                  {"label": "S", "stock": 1},
                  {"label": "XL", "stock": 1},
                  {"label": "XXL", "stock": 1}
                ]
              },
              {
                "id": "1286182001",
                "productName": "Keten Karışımlı Erkek Yaka Gömlek Regular Fit",
                "colorName": "Krem",
                "url": "/tr_tr/productpage.1286182001.html",
                "prices": [{"priceType": "redPrice", "price": 1049.0, "formattedPrice": "1.049,00 TL"}],
                "sizes": [
                  {"label": "XS", "stock": 1},
                  {"label": "S", "stock": 1},
                  {"label": "XL", "stock": 1},
                  {"label": "XXL", "stock": 1}
                ]
              }
            ]
          }
          </script>
        </body></html>
        """

        offers = extract_hm_offers(
            html,
            source_url="https://www2.hm.com/tr_tr/productpage.1286182003.html",
            size="XL",
        )

        self.assertEqual(
            [(offer.title, offer.price, offer.url) for offer in offers],
            [
                (
                    "Keten Karışımlı Erkek Yaka Gömlek Regular Fit / Adaçayı yeşili / XL",
                    Decimal("489.0"),
                    "https://www2.hm.com/tr_tr/productpage.1286182002.html",
                ),
                (
                    "Keten Karışımlı Erkek Yaka Gömlek Regular Fit / Krem / XL",
                    Decimal("1049.0"),
                    "https://www2.hm.com/tr_tr/productpage.1286182001.html",
                ),
            ],
        )

    def test_hm_fallback_reads_visible_text_price(self):
        html = """
        <html><body>
          <h1>Lastik Örgülü Erkek Yaka Gömlek Loose Fit</h1>
          <span>Renk: Turkuaz</span>
          <span>799,99 TL</span>
        </body></html>
        """

        offers = extract_hm_offers(html, source_url="https://www2.hm.com/tr_tr/productpage.1285132002.html")

        self.assertEqual(offers[0].seller, "H&M")
        self.assertEqual(offers[0].price, Decimal("799.99"))

    def test_zara_out_of_stock_is_typed_as_non_technical_state(self):
        html = """
        <html><body>
          <script type="application/ld+json">
          {
            "@type": "Product",
            "name": "DOKULU REGULAR FIT POLO T-SHIRT - sarımsı kahverengi - L",
            "size": "L",
            "color": "sarımsı kahverengi",
            "offers": {"@type": "Offer", "price": "1290", "availability": "Benzer ürünler"}
          }
          </script>
        </body></html>
        """

        with self.assertRaisesRegex(OutOfStockHermesError, "stokta değil"):
            extract_zara_offers(html, source_url="https://www.zara.com/tr/tr/product", size="L")


if __name__ == "__main__":
    unittest.main()
