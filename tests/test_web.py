"""Panel pages, settings forms and the shared ingress/public router."""

import json
import unittest
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from support import TempData

from hermes.config import prepare_watches
from hermes.errors import EmptySearchResultsHermesError
from hermes.models import OfferResult
from hermes.utils import parse_decimal, utc_now
from hermes.web import assets, dashboard, link_test, server, settings

TOKEN = "t" * 32


class FakeRuntime:
    config_error = ""

    def __init__(self):
        self.resets = []

    def health(self):
        return True, "ok"

    def reset_notifications(self):
        self.resets.append("notifications")
        return True, "Bildirimler sıfırlandı."

    def reset_price_history(self):
        self.resets.append("history")
        return True, "Min/maks sıfırlandı."


class DataFilesMixin:
    """Points the panel at a private /data directory."""

    def setUp(self):
        self.data = TempData()
        root = self.data.root
        self.options_path = root / "options.json"
        self.options_path.write_text(json.dumps({"takip_edilenler": []}), encoding="utf-8")
        self.patches = [
            patch("hermes.config.OPTIONS_PATH", self.options_path),
            patch.object(dashboard, "SUMMARY_PATH", self.data.files.summary),
            patch.object(dashboard, "STATE_PATH", self.data.files.state),
            patch.object(dashboard, "CYCLE_HISTORY_PATH", self.data.files.cycle_history),
            patch.object(dashboard, "TELEGRAM_STATUS_PATH", root / "status.json"),
            patch.object(dashboard, "TELEGRAM_ERROR_EVENTS_PATH", root / "error_events.json"),
            patch.object(settings, "SUMMARY_PATH", self.data.files.summary),
            patch.object(settings, "STATE_PATH", self.data.files.state),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.data.cleanup()

    def write_options(self, options):
        self.options_path.write_text(json.dumps(options), encoding="utf-8")


def price_row(**fields):
    row = {"seller": "Amazon", "product_title": "Ürün", "product_url": "https://www.amazon.com.tr/dp/B000000001",
           "price": "12.999 TL", "target": "13.000 TL", "difference": "-1 TL", "min_price": "12.999 TL", "max_price": "12.999 TL"}
    row.update(fields)
    return row


class RouterTests(DataFilesMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.runtime = FakeRuntime()
        self.router = server.Router(self.runtime)

    def request(self, path, method="GET", body=b"", public_only=False):
        request = server.split_request(path, method, body, public_only)
        return self.router.handle(request) if request else server.NOT_FOUND

    def test_ingress_pages_and_assets_are_served(self):
        for path in ("/", "/statistics", "/settings", "/link-test", "/restarting"):
            with self.subTest(path=path):
                response = self.request(path)
                self.assertEqual(response.status, 200)
                self.assertIn(b"app.css?v=", response.payload)
        self.assertEqual(self.request("/app.css").content_type, "text/css; charset=utf-8")
        self.assertIn(b"waitForHermes", self.request("/restart.js").payload)
        self.assertEqual(self.request("/health").payload, b"ok\n")
        self.assertEqual(self.request("/unknown").status, 404)

    def test_public_surface_needs_an_enabled_long_token(self):
        self.assertEqual(self.request(f"/public/{TOKEN}/", public_only=True).status, 404)
        self.write_options({"public_dashboard_enabled": True, "public_dashboard_token": TOKEN})
        self.assertEqual(self.request(f"/public/{TOKEN}/", public_only=True).status, 200)
        self.assertEqual(self.request("/public/" + "x" * 32 + "/", public_only=True).status, 404)
        self.write_options({"public_dashboard_enabled": True, "public_dashboard_token": "short"})
        self.assertEqual(self.request("/public/short/", public_only=True).status, 404)
        # The public port serves nothing but the token surface and health.
        self.assertEqual(self.request("/settings", public_only=True).status, 404)
        self.assertEqual(self.request("/health", public_only=True).status, 200)

    def test_public_pages_link_inside_the_token_surface(self):
        self.write_options({"public_dashboard_enabled": True, "public_dashboard_token": TOKEN})
        page = self.request(f"/public/{TOKEN}/statistics", public_only=True).payload.decode()
        self.assertIn(f"/public/{TOKEN}/settings", page)
        self.assertIn(f"/public/{TOKEN}/app.css", page)
        settings_page = self.request(f"/public/{TOKEN}/settings", public_only=True).payload.decode()
        self.assertIn(f"action='/public/{TOKEN}/settings/save'", settings_page)

    def test_ingress_relative_links_and_redirects_resolve_from_each_page(self):
        page = self.request("/settings").payload.decode()
        self.assertIn("action='./settings/save'", page)
        self.assertIn("href='./statistics'", page)
        with patch.object(server, "handle_settings_save", return_value=(True, "Kaydedildi.")):
            response = self.request("/settings/save", "POST", b"operation=update_existing")
        self.assertEqual(response.status, 303)
        self.assertTrue(response.headers["Location"].startswith("../restarting?"))
        response = self.request("/reset-notifications", "POST")
        self.assertTrue(response.headers["Location"].startswith("./?reset=ok"))
        self.assertEqual(self.runtime.resets, ["notifications"])

    def test_public_actions_redirect_inside_the_token_surface(self):
        self.write_options({"public_dashboard_enabled": True, "public_dashboard_token": TOKEN})
        response = self.request(f"/public/{TOKEN}/reset-price-history", "POST", public_only=True)
        self.assertTrue(response.headers["Location"].startswith(f"/public/{TOKEN}/?history=ok"))
        with patch.object(server, "handle_settings_save", return_value=(False, "Hata")):
            response = self.request(f"/public/{TOKEN}/settings/save", "POST", b"x=1", public_only=True)
        self.assertTrue(response.headers["Location"].startswith(f"/public/{TOKEN}/settings?saved=fail"))

    def test_restart_page_polls_health_on_its_own_surface(self):
        self.write_options({"public_dashboard_enabled": True, "public_dashboard_token": TOKEN})
        page = self.request(f"/public/{TOKEN}/restarting?msg=Kaydedildi", public_only=True).payload.decode()
        self.assertIn(f"data-health-path='/public/{TOKEN}/health'", page)
        self.assertIn(f"data-settings-path='/public/{TOKEN}/settings'", page)
        ingress = self.request("/restarting?return_to_main=1").payload.decode()
        self.assertIn("data-health-path='./health'", ingress)
        self.assertIn("data-return-path='./'", ingress)

    def test_live_data_refreshes_in_place_on_both_surfaces(self):
        self.data.write_summary({"rows": [price_row(product_title="Canlı ürün")], "checked_at": "2026-10-03 20:00:00"})
        page = self.request("/").payload.decode()
        self.assertIn("data-live-url='./live/dashboard'", page)
        self.assertIn("src='./live.js'", page)
        self.assertIn("<noscript><meta http-equiv='refresh' content='60'></noscript>", page)
        response = self.request("/live/dashboard")
        self.assertEqual(response.content_type, "application/json; charset=utf-8")
        live = json.loads(response.payload)["html"]
        self.assertIn("Canlı ürün", live)
        self.assertNotIn("Bildirim Sıfırla", live)  # actions stay outside the refreshed block
        self.assertIn("Günlük çevrim özeti", json.loads(self.request("/live/statistics").payload)["html"])
        self.write_options({"public_dashboard_enabled": True, "public_dashboard_token": TOKEN})
        public_page = self.request(f"/public/{TOKEN}/", public_only=True).payload.decode()
        self.assertIn(f"data-live-url='/public/{TOKEN}/live/dashboard'", public_page)
        public_live = self.request(f"/public/{TOKEN}/live/dashboard", public_only=True)
        self.assertEqual(public_live.status, 200)
        self.assertIn("Canlı ürün", json.loads(public_live.payload)["html"])
        self.assertEqual(self.request("/public/" + "x" * 32 + "/live/dashboard", public_only=True).status, 404)

    def test_live_script_keeps_open_groups_and_scroll(self):
        script = self.request("/live.js").payload.decode()
        for text in ("details[data-key][open]", "window.scrollTo", "document.hidden", "data.html === last"):
            self.assertIn(text, script)

    def test_unhealthy_monitor_answers_503(self):
        self.runtime.health = lambda: (False, "izleyici çalışmıyor")
        response = self.request("/health")
        self.assertEqual(response.status, 503)

    def test_config_error_is_shown_on_the_dashboard(self):
        self.runtime.config_error = "Pushover anahtarları zorunlu."
        page = self.request("/").payload.decode()
        self.assertIn("Ayarlarda hata var", page)
        self.assertIn("Pushover anahtarları zorunlu.", page)


class LiveServerTests(DataFilesMixin, unittest.TestCase):
    def test_real_http_server_serves_pages_actions_and_health(self):
        import urllib.request

        runtime = FakeRuntime()
        httpd = server.start_server(server.Router(runtime), 0, public_only=False)
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            with urllib.request.urlopen(f"{base}/health", timeout=5) as response:
                self.assertEqual(response.read(), b"ok\n")
            with urllib.request.urlopen(f"{base}/", timeout=5) as response:
                self.assertIn("Özet Tablo", response.read().decode())
                self.assertEqual(response.headers["Cache-Control"], "no-store")

            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args, **kwargs):
                    return None

            opener = urllib.request.build_opener(NoRedirect)
            with self.assertRaises(urllib.error.HTTPError) as caught:
                opener.open(urllib.request.Request(f"{base}/reset-notifications", data=b"", method="POST"), timeout=5)
            caught.exception.close()
            self.assertEqual(caught.exception.code, 303)
            self.assertTrue(caught.exception.headers["Location"].startswith("./?reset=ok"))
            self.assertEqual(runtime.resets, ["notifications"])
        finally:
            httpd.shutdown()
            httpd.server_close()


class DashboardTests(DataFilesMixin, unittest.TestCase):
    def test_site_theme_classes_are_distinct(self):
        expected = {"Amazon": "site-amazon", "Hepsiburada": "site-hepsiburada", "Trendyol": "site-trendyol",
                    "Network": "site-network", "Beymen Club": "site-beymenclub", "Nordbron": "site-nordbron",
                    "Zara": "site-zara", "H&M": "site-hm"}
        self.assertEqual({seller: dashboard.site_theme_class(seller) for seller in expected}, expected)

    def test_recent_errors_from_every_watch_are_listed_after_the_summary(self):
        self.data.write_state({
            "first": {"site": "amazon", "watch_name": "iPhone", "configured_url": "https://www.amazon.com.tr/dp/B000000001",
                      "last_checked_at": utc_now(), "last_error": "İlk hata"},
            "second": {"site": "hepsiburada", "configured_url": "https://www.hepsiburada.com/x-p-1",
                       "last_checked_at": utc_now(), "last_error": "İkinci hata"},
            "old": {"site": "zara", "last_checked_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(), "last_error": "Eski"},
        })
        self.data.write_summary({"rows": [price_row()]})
        html = dashboard.render_dashboard_page(".", {}).decode()
        self.assertIn("Hata sayısı (son 24 saat)</span><strong>2</strong>", html)
        self.assertIn("Amazon: iPhone", html)
        self.assertNotIn("Eski", html)
        self.assertGreater(html.index("Hata sayısı"), html.index("Özet Tablo"))

    def test_error_links_of_variants_are_listed(self):
        details = dashboard.error_link_details("https://www.hepsiburada.com/a-p-1 | Fiyat yok; https://www.hepsiburada.com/b-p-2 | Erişim yok")
        self.assertEqual([item["url"] for item in details], ["https://www.hepsiburada.com/a-p-1", "https://www.hepsiburada.com/b-p-2"])
        self.assertEqual(dashboard.clean_error_message("https://x.test | Site 403"), "Site 403")

    def test_multi_result_watch_collapses_under_its_name(self):
        rows = [price_row(product_title="Juo Q3 Yeşil", product_url="https://example.test/green", difference="+37,00",
                          search_group="g", search_group_label="Juo Q3"),
                price_row(product_title="Juo Q3 Kırmızı", product_url="https://example.test/red", difference="+99,00",
                          search_group="g", search_group_label="Juo Q3")]
        rendered = dashboard.render_table_section("Hedefin Üstünde Kalan Ürünler", rows, "Boş", collapse=True)
        self.assertIn("<details class='search-result-group' data-key='group:Juo Q3'>", rendered)
        self.assertIn("2 sonuç", rendered)

    def test_open_rows_are_ordered_by_seller_then_difference(self):
        rows = [{"seller": "Hepsiburada", "product_title": "Uzak", "difference": "+900,00"},
                {"seller": "Amazon", "product_title": "Orta", "difference": "+600,00"},
                {"seller": "Amazon", "product_title": "Yakın", "difference": "+100,00"},
                {"seller": "Hepsiburada", "product_title": "Yakın", "difference": "+150,00"},
                {"seller": "Amazon", "product_title": "Varyasyon B", "difference": "+75,00", "search_group": "g", "search_group_label": "Juo"},
                {"seller": "Amazon", "product_title": "Varyasyon A", "difference": "+50,00", "search_group": "g", "search_group_label": "Juo"}]
        open_rows, groups = dashboard.split_result_groups(rows)
        self.assertEqual([row["product_title"] for row in open_rows], ["Yakın", "Orta", "Yakın", "Uzak"])
        self.assertEqual([row["product_title"] for row in groups[0][1]], ["Varyasyon A", "Varyasyon B"])

    def test_a_single_grouped_row_stays_open(self):
        open_rows, groups = dashboard.split_result_groups([price_row(search_group="g", search_group_label="Tek")])
        self.assertEqual((len(open_rows), groups), (1, []))

    def test_stock_rows_are_collapsed_by_site(self):
        html = dashboard.render_stock_section([{"seller": "Zara", "product_title": "Polo / M", "target": "1.500 TL"},
                                               {"seller": "Zara", "product_title": "Gömlek / XL", "target": "1.500 TL"},
                                               {"seller": "H&M", "product_title": "Pantolon / L", "target": "1.200 TL"}])
        self.assertEqual(html.count("stock-site-group"), 2)
        self.assertIn("Zara</strong><span>2 ürün", html)
        self.assertIn("H&amp;M</strong><span>1 ürün", html)

    def test_warehouse_rows_are_labeled_without_priority_dot(self):
        html = dashboard.render_table_row(price_row(is_warehouse=True))
        self.assertIn('class="warehouse-tag">DEPO</strong>', html)
        self.assertNotIn("priority-dot", html)

    def test_normal_rows_show_priority_dots(self):
        for priority, label in (("high", "Yüksek"), ("medium", "Orta"), ("low", "Düşük")):
            with self.subTest(priority=priority):
                html = dashboard.render_table_row(price_row(priority=priority))
                self.assertIn(f"priority-{priority}", html)
                self.assertIn(f'title="{label} öncelik"', html)

    def test_titles_are_60_characters_without_ellipsis_and_groups_70_with(self):
        full = "Çok uzun ürün adı " * 12
        visible, tooltip = dashboard.shortened_title(full, dashboard.TABLE_TITLE_MAX_LENGTH, ellipsis=False)
        self.assertEqual(visible, full.strip()[:60].rstrip())
        html = dashboard.render_table_row(price_row(product_title=full))
        self.assertIn(visible, html)
        self.assertIn(tooltip, html)
        group_title = "Çok uzun grup başlığı " * 8
        visible, tooltip = dashboard.shortened_title(group_title, dashboard.GROUP_TITLE_MAX_LENGTH)
        self.assertEqual(len(visible), 70)
        self.assertTrue(visible.endswith("..."))
        self.assertIn(f"title='{tooltip}'", dashboard.render_group(group_title, []))
        self.assertIn(f"data-key='group:{tooltip}'", dashboard.render_group(group_title, []))

    def test_prices_are_whole_lira(self):
        self.assertEqual(parse_decimal("1.500"), Decimal("1500"))
        self.assertEqual(settings._price_input_value("3000,0"), "3.000")
        self.assertEqual(dashboard.display_tl("1.500,75"), "1.500 TL")
        self.assertEqual(dashboard.display_tl("+125,90", signed=True), "+125 TL")
        self.assertEqual(dashboard.display_tl_range("1.500,75 / 2.000,01"), "1.500 TL / 2.000 TL")

    def test_price_age_is_shown_in_minutes(self):
        checked_at = (datetime.now(timezone.utc) - timedelta(minutes=125)).isoformat()
        html = dashboard.render_table_row(price_row(price_checked_at=checked_at))
        self.assertIn("125 dk önce", html)
        self.assertIn('data-label="Son güncelleme"', html)

    def test_statistics_show_a_compact_daily_range_without_outliers(self):
        same_day = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
        history = [{"checked_at": (same_day - timedelta(days=1, minutes=m)).isoformat(), "duration_seconds": d}
                   for m, d in ((0, 120), (1, 230), (2, 240), (3, 250), (4, 900))]
        self.data.files.cycle_history.write_text(json.dumps(history), encoding="utf-8")
        html = dashboard.render_statistics_page(".").decode()
        self.assertIn("Son 7 gün · 5 çevrim", html)
        self.assertIn('class="cycle-line"', html)
        self.assertEqual(html.count('<details class="statistics-day"'), 1)
        self.assertIn('<span class="statistics-day-typical"><strong>4:00</strong></span>', html)
        self.assertIn("<span><strong>3:50–4:10</strong></span>", html)
        self.assertIn('<span class="statistics-day-slow"><strong>1</strong></span>', html)
        self.assertIn("En uzun <strong>15 dk 0 sn</strong>", html)

    def test_telegram_notifications_are_listed(self):
        (self.data.root / "status.json").write_text(json.dumps({"recent_notifications": [
            {"keyword": "airpods", "channel": "@firsatz", "created_at": "2026-10-03 12:00:00", "message": "AirPods indirim",
             "url": "https://t.me/firsatz/1"}]}), encoding="utf-8")
        html = dashboard.render_dashboard_page(".", {}).decode()
        self.assertIn("https://t.me/firsatz/1", html)
        self.assertIn("AirPods indirim", html)


class SettingsTests(DataFilesMixin, unittest.TestCase):
    def test_seller_filter_and_priority_are_saved_and_rendered(self):
        watches = settings.build_watches({"watches_count": ["1"], "watches_0_name": ["iPhone"], "watches_0_target_price": ["100000"],
                                          "watches_0_url_1": ["https://www.amazon.com.tr/dp/B000000001"],
                                          "watches_0_priority": ["low"], "watches_0_official_seller_only": ["1"]})
        self.assertEqual(watches[0]["priority"], "low")
        self.assertTrue(watches[0]["official_seller_only"])
        html = settings.watch_form(watches[0], 0)
        for text in ("Yalnızca platformun kendi satıcısı", "Düşük · 6 saatte bir", "Orta · 2 saatte bir", "Yüksek · her çevrim"):
            self.assertIn(text, html)

    def test_updating_a_card_keeps_its_variation_setting(self):
        options, _ = settings.apply_settings_operation(
            {"takip_edilenler": [{"name": "Tablet", "target_price": 20000, "url_1": "https://www.amazon.com.tr/dp/B000000001?th=1"}]},
            {"operation": ["update_watch"], "watch_index": ["0"], "update_watch_index": ["0"], "watches_0_name": ["Tablet"],
             "watches_0_target_price": ["20000"], "watches_0_url_1": ["https://www.amazon.com.tr/dp/B000000001?th=1"],
             "watches_0_include_variations": ["on"], "watches_0_notify_once_in_24H": ["on"], "watches_0_active": ["on"]})
        self.assertTrue(prepare_watches(options["takip_edilenler"])[0].include_variations)
        self.assertTrue(options["takip_edilenler"][0]["check_now_token"])

    def test_page_renders_new_card_first_with_one_save_form_and_overlay(self):
        self.write_options({"takip_edilenler": [{"name": "Mevcut", "target_price": 100, "url_1": "https://www.amazon.com.tr/dp/B000000001"}]})
        page = settings.render_settings_page(".", {}).decode()
        self.assertLess(page.index("Yeni takip ekle"), page.index("Takip edilenler"))
        self.assertEqual(page.count("data-settings-save"), 1)
        for text in ("id='saving-overlay'", "Değişiklikleri uygula", "Ayarlar kaydediliyor", "id='watch-search'",
                     "data-watch-search='Mevcut'", "class='button danger'", "data-delete-watch", "value='100'",
                     "src='./settings.js' defer"):
            self.assertIn(text, page)
        for text in ("name='delete_watch_index'", "name='update_watch_index'", "name='watch_index'"):
            self.assertNotIn(text, page)

    def test_configured_groups_are_offered_and_filterable(self):
        self.write_options({"gruplar": ["Moda", "Teknoloji", "Market"], "takip_edilenler": []})
        page = settings.render_settings_page(".", {}).decode()
        for group in ("Moda", "Teknoloji", "Market"):
            self.assertIn(f"data-watch-group-filter='{group}'", page)
        html = settings.watch_form({"name": "Polo", "group": "Moda"}, 0, groups=["Moda", "Teknoloji"])
        self.assertIn("<select", html)
        self.assertIn("Teknoloji", html)

    def test_cards_use_the_compact_three_row_layout(self):
        for html in (settings.watch_form({}, 0, is_new=True, groups=["Moda"]),
                     settings.watch_form({"name": "Mevcut", "group": "Teknoloji", "target_price": 1500,
                                          "url_1": "https://www.amazon.com.tr/dp/B000000001"}, 1, groups=["Moda", "Teknoloji"])):
            for css in ("watch-layout", "watch-top", "watch-links", "watch-bottom"):
                self.assertIn(css, html)
            self.assertLess(html.index(">Grup<"), html.index(">Ad<"))
            self.assertLess(html.index(">Hedef Fiyat Maks<"), html.index(">Beden<"))
            for number in range(1, 6):
                self.assertIn(f">Link {number}<", html)

    def test_search_card_without_a_name_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "arama sayfası"):
            settings.build_watches({"watches_count": ["1"], "watches_0_target_price": ["2000"],
                                    "watches_0_url_1": ["https://www.amazon.com.tr/s?k=juo+q3"]})

    def test_empty_new_card_is_ignored_and_missing_fields_are_named(self):
        self.assertEqual(settings.build_watches({"watches_count": ["1"], "watches_0_group": ["Moda"], "watches_0_active": ["1"]}), [])
        with self.assertRaisesRegex(ValueError, r"Takip 1 \(Eksik ürün\): en az bir link"):
            settings.build_watches({"watches_count": ["1"], "watches_0_name": ["Eksik ürün"], "watches_0_target_price": ["1000"]})

    def test_delete_update_and_batch_operations(self):
        two = {"takip_edilenler": [{"name": "Silinecek", "target_price": 100, "url_1": "https://www.amazon.com.tr/dp/B000000001"},
                                   {"name": "Kalacak", "group": "Diğer", "target_price": 200, "url_1": "https://www.amazon.com.tr/dp/B000000002"}]}
        for form in ({"operation": ["update_existing"], "delete_watch_index": ["0"]}, {"operation": ["delete_watch"], "watch_index": ["0"]}):
            options, message = settings.apply_settings_operation(two, form)
            self.assertEqual(message, "Silinecek takip kaydı silindi.")
            self.assertEqual([item["name"] for item in options["takip_edilenler"]], ["Kalacak"])
        options, message = settings.apply_settings_operation(dict(two, telegram_enabled=False, channels=["@firsatz"]), {
            "operation": ["update_existing"], "watches_count": ["3"],
            "watches_0_delete": ["1"], "watches_0_name": ["Silinecek"], "watches_0_target_price": ["100"],
            "watches_0_url_1": ["https://www.amazon.com.tr/dp/B000000001"],
            "watches_1_name": ["Kalacak"], "watches_1_group": ["Teknoloji"], "watches_1_target_price": ["250"],
            "watches_1_url_1": ["https://www.amazon.com.tr/dp/B000000002"], "watches_1_active": ["1"],
            "watches_2_name": ["Yeni kayıt"], "watches_2_target_price": ["1.500"],
            "watches_2_url_1": ["https://www.hepsiburada.com/ara?q=yeni"], "watches_2_active": ["1"],
            "telegram_enabled": ["1"], "channels": ["@firsatz"]})
        self.assertEqual(message, "Ayarlar kaydedildi.")
        self.assertEqual([item["name"] for item in options["takip_edilenler"]], ["Kalacak", "Yeni kayıt"])
        self.assertEqual((options["takip_edilenler"][0]["group"], options["takip_edilenler"][0]["target_price"]), ("Teknoloji", 250))
        self.assertEqual(options["takip_edilenler"][1]["target_price"], 1500)
        self.assertTrue(options["telegram_enabled"])
        self.assertFalse(options["telegram_saved_messages_enabled"])  # the unchecked box was absent

    def test_single_card_update_changes_only_that_card(self):
        source = {"takip_edilenler": [
            {"name": "İlk", "group": "Diğer", "target_price": 100, "url_1": "https://www.amazon.com.tr/dp/B000000001"},
            {"name": "İkinci", "group": "Moda", "target_price": 200, "url_1": "https://www.zara.com/tr/tr/ornek-p03166301.html"}]}
        for index_field in ("update_watch_index", "watch_index"):
            options, message = settings.apply_settings_operation(source, {
                "operation": ["update_watch"], index_field: ["1"], "watches_1_name": ["İkinci"], "watches_1_group": ["Teknoloji"],
                "watches_1_target_price": ["1.500"], "watches_1_url_1": ["https://www.zara.com/tr/tr/ornek-p03166301.html"],
                "watches_1_active": ["1"]})
            self.assertIn("güncellendi", message)
            self.assertEqual(options["takip_edilenler"][0], source["takip_edilenler"][0])
            self.assertEqual((options["takip_edilenler"][1]["group"], options["takip_edilenler"][1]["target_price"]), ("Teknoloji", 1500))

    def test_new_card_additions_and_a_cached_mislabeled_form(self):
        options, message = settings.apply_settings_operation(
            {"takip_edilenler": [{"name": "Nordbron çanta", "target_price": 4500, "url_1": "https://nordbron.com/stark"}]},
            {"operation": ["update_watch"], "watches_count": ["1"], "watches_0_name": ["Belkin şarj"],
             "watches_0_target_price": ["4000"], "watches_0_url_1": ["https://www.amazon.com.tr/dp/B000000001"]})
        self.assertIn("eklendi", message)
        self.assertEqual([item["name"] for item in options["takip_edilenler"]], ["Nordbron çanta", "Belkin şarj"])
        self.assertTrue(options["takip_edilenler"][1]["check_now_token"])

    def test_saving_preserves_every_other_option(self):
        source = {"interval_seconds": 10, "pushover_user_key": "user", "pushover_api_token": "token", "telegram_enabled": True,
                  "api_id": "123", "channels": ["@example"], "keywords": ["fırsat"], "gruplar": ["Moda"],
                  "public_dashboard_token": TOKEN,
                  "takip_edilenler": [{"name": "Silinecek", "target_price": 100, "url_1": "https://www.amazon.com.tr/dp/B000000001"}]}
        options, _ = settings.apply_settings_operation(source, {"operation": ["update_existing"], "delete_watch_index": ["0"]})
        self.assertEqual((options["channels"], options["keywords"], options["public_dashboard_token"]), (["@example"], ["fırsat"], TOKEN))
        self.assertTrue(options["telegram_enabled"])
        self.assertEqual(options["takip_edilenler"], [])
        self.assertEqual(options["request_delay_max_seconds"], 8)  # schema default supplied

    def test_fashion_cards_default_to_the_moda_group(self):
        watches = settings.build_watches({"watches_count": ["1"], "watches_0_name": ["Gömlek"], "watches_0_target_price": ["1000"],
                                          "watches_0_url_1": ["https://www.zara.com/tr/tr/gomlek-p01234567.html"]})
        self.assertEqual(watches[0]["group"], "Moda")

    def test_cards_without_a_name_use_learned_titles(self):
        html = settings.watch_form({"url_1": "https://www.zara.com/tr/tr/dokulu-p03166301.html?v1=567"}, 8, groups=["Moda"],
                                   known_titles={"https://www.zara.com/tr/tr/dokulu-p03166301.html": "Dokulu Polo"})
        self.assertIn("[Moda] Dokulu Polo", html)
        self.data.write_summary({"stock_rows": [{"product_url": "https://www2.hm.com/tr_tr/productpage.1286182003.html?color=009",
                                                 "product_title": "Keten Gömlek / Kahverengi / XL"}]})
        html = settings.watch_form({"url_1": "https://www2.hm.com/tr_tr/productpage.1286182003.html"}, 3, groups=["Moda"],
                                   known_titles=settings.stored_watch_titles())
        self.assertIn("Keten Gömlek", html)

    def test_shared_scripts_handle_search_filters_and_restart(self):
        self.assertIn("watchSearch?.addEventListener('input', refreshWatchList)", assets.SETTINGS_SCRIPT)
        for text in ("data-watch-group-filter", "data-delete-watch", "add-watch-card"):
            self.assertIn(text, assets.SETTINGS_SCRIPT)
        self.assertIn("waitForHermes", assets.RESTART_SCRIPT)


class LinkTestPageTests(DataFilesMixin, unittest.TestCase):
    def submit(self, body: bytes) -> str:
        return link_test.render_link_test_result(".", body).decode()

    def test_results_are_rendered_without_saving(self):
        offer = OfferResult("Örnek ürün / Mavi", Decimal("18999"), "Amazon", "https://www.amazon.com.tr/dp/B000000001")
        with patch.object(link_test, "inspect_link", return_value=("amazon", [offer])) as inspect:
            page = self.submit(b"url=https%3A%2F%2Fwww.amazon.com.tr%2Fdp%2FB000000001&name=Ornek&size=XL"
                               b"&exclude_terms=kilif%2Ckoruyucu&include_variations=1")
        inspect.assert_called_once_with("https://www.amazon.com.tr/dp/B000000001", name="Ornek", size="XL", include_variations=True,
                                        excluded_terms=["kilif", "koruyucu"], amazon_browser=False)
        for text in ("Test sonuçları", "Örnek ürün / Mavi", "18.999 TL", "Geçici sonuçlar. Kayıt ve bildirim oluşturmaz."):
            self.assertIn(text, page)
        self.assertFalse(self.data.files.state.exists())

    def test_empty_search_is_a_normal_notice(self):
        error = EmptySearchResultsHermesError("Amazon Depo içinde juo 240w için sonuç bulunamadı", no_results_notice=True)
        with patch.object(link_test, "inspect_link", side_effect=error):
            page = self.submit(b"url=" + urllib.parse.quote("https://www.amazon.com.tr/s?k=juo").encode() + b"&name=Juo")
        self.assertIn("Ürün bulunamadı", page)
        self.assertNotIn("notice-fail", page)


if __name__ == "__main__":
    unittest.main()
