"""Panel pages, settings forms and the shared ingress/public router."""

import gzip
import json
import unittest
from html import escape

from hermes.web.assets import APP_CSS
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from support import TempData

from hermes.config import prepare_watches
from hermes import history as history_module
from hermes.history import History
from hermes.diagnostics import Diagnostics
from hermes.utils import SystemLoad, parse_decimal, utc_now
from hermes.web import assets, dashboard, server, settings
from hermes.web import statistics as statistics_page

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

    def reset_error_history(self):
        self.resets.append("errors")
        return True, "Hata kayıtları silindi (3 kayıt)."

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
            patch.object(dashboard, "DATABASE_PATH", self.data.files.database),
            patch.object(statistics_page, "DATABASE_PATH", self.data.files.database),
            patch.object(statistics_page, "SUMMARY_PATH", self.data.files.summary),
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
        for path in ("/", "/statistics", "/settings", "/restarting"):
            with self.subTest(path=path):
                response = self.request(path)
                self.assertEqual(response.status, 200)
                self.assertIn(b"app.css?v=", response.payload)
        self.assertEqual(self.request("/app.css").content_type, "text/css; charset=utf-8")
        self.assertIn(b"waitForHermes", self.request("/restart.js").payload)
        self.assertEqual(self.request("/health").payload, b"ok\n")
        self.assertEqual(self.request("/unknown").status, 404)
        self.assertEqual(self.request("/link-test").status, 404)  # the link test was removed in 3.11
        self.assertEqual(self.request("/link-test", "POST").status, 404)

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

    def test_top_bar_has_logo_and_gear_and_one_button_row_sits_under_settings(self):
        for path in ("/", "/statistics"):
            page = self.request(path).payload.decode()
            self.assertIn("<a class='badge' href='./'", page)
            self.assertIn("class='gear-button' href='./settings'", page)
            self.assertNotIn("page-links", page)
        settings_page = self.request("/settings").payload.decode()
        self.assertIn("<a class='badge' href='./'", settings_page)
        self.assertIn("aria-current='page'", settings_page)
        row = settings_page[settings_page.index("tool-actions"):]
        row = row[:row.index("</div>")]
        self.assertIn("href='./statistics'>İstatistik</a>", row)
        self.assertIn("action='./restart' data-confirm='Hermes, Home Assistant üzerinden uygulama olarak yeniden başlatılacak.", row)
        self.assertGreater(row.index("Hermes'i yeniden başlat"), row.index("İstatistik"))
        self.assertNotIn("link-test", settings_page)
        self.assertNotIn(">Test</a>", settings_page)
        self.assertNotIn("Özet Tablo</a>", settings_page)
        for label in ("Pushover testi", "Bildirim Sıfırla", "Min/Maks Sıfırla"):
            self.assertIn(label, row)
            self.assertNotIn(label, self.request("/").payload.decode())
        self.assertGreater(settings_page.index("Pushover testi"), settings_page.index("Değişiklikleri uygula"))
        self.assertGreater(settings_page.index("tool-actions"), settings_page.index("Değişiklikleri uygula"))

    def test_restart_button_restarts_hermes_through_home_assistant_on_both_surfaces(self):
        self.write_options({"public_dashboard_enabled": True, "public_dashboard_token": TOKEN})
        with patch.object(settings, "schedule_restart") as restart:
            ingress = self.request("/restart", "POST")
            public = self.request(f"/public/{TOKEN}/restart", "POST", public_only=True)
        self.assertEqual(restart.call_count, 2)
        self.assertEqual(ingress.status, 303)
        self.assertTrue(ingress.headers["Location"].startswith("./restarting?msg="))
        self.assertTrue(public.headers["Location"].startswith(f"/public/{TOKEN}/restarting?msg="))
        page = self.request("/restarting?msg=Hermes+yeniden+ba%C5%9Flat%C4%B1l%C4%B1yor%3B+ayarlar+de%C4%9Fi%C5%9Fmedi.").payload.decode()
        self.assertIn("Hermes yeniden başlatılıyor; ayarlar değişmedi.", page)
        self.assertNotIn("kaydedildi", page)
        with patch.object(settings, "schedule_restart", side_effect=RuntimeError("Supervisor yok")):
            failed = self.request("/restart", "POST")
        self.assertIn("saved=fail", failed.headers["Location"])

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
        self.assertTrue(response.headers["Location"].startswith("./settings?saved=ok"))
        self.assertEqual(self.runtime.resets, ["notifications"])

    def test_public_actions_redirect_inside_the_token_surface(self):
        self.write_options({"public_dashboard_enabled": True, "public_dashboard_token": TOKEN})
        response = self.request(f"/public/{TOKEN}/reset-price-history", "POST", public_only=True)
        self.assertTrue(response.headers["Location"].startswith(f"/public/{TOKEN}/settings?saved=ok"))
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
        self.assertIn("data-live-url='./live/dashboard' data-live-interval='15'", page)
        self.assertIn("data-live-url='./live/statistics?p=24h' data-live-interval='60'", self.request("/statistics").payload.decode())
        self.assertIn("src='./live.js?v=", page)  # versioned, so a new release is never served from cache
        self.assertIn("<noscript><meta http-equiv='refresh' content='60'></noscript>", page)
        response = self.request("/live/dashboard")
        self.assertEqual(response.content_type, "application/json; charset=utf-8")
        live = json.loads(response.payload)["html"]
        self.assertIn("Canlı ürün", live)
        self.assertNotIn("Bildirim Sıfırla", live)  # actions stay outside the refreshed block
        self.assertIn("Kontrol sıklığı", json.loads(self.request("/live/statistics").payload)["html"])
        self.write_options({"public_dashboard_enabled": True, "public_dashboard_token": TOKEN})
        public_page = self.request(f"/public/{TOKEN}/", public_only=True).payload.decode()
        self.assertIn(f"data-live-url='/public/{TOKEN}/live/dashboard'", public_page)
        public_live = self.request(f"/public/{TOKEN}/live/dashboard", public_only=True)
        self.assertEqual(public_live.status, 200)
        self.assertIn("Canlı ürün", json.loads(public_live.payload)["html"])
        self.assertEqual(self.request("/public/" + "x" * 32 + "/live/dashboard", public_only=True).status, 404)

    def test_live_script_keeps_open_groups_and_scroll(self):
        script = self.request("/live.js").payload.decode()
        for text in ("details[data-key][open]", "window.scrollTo", "document.hidden", "data.same", "&v=", "liveInterval"):
            self.assertIn(text, script)

    def test_unchanged_live_block_is_not_sent_again(self):
        first = json.loads(self.request("/live/statistics").payload)
        self.assertIn("html", first)
        again = json.loads(self.request(f"/live/statistics?v={first['v']}").payload)
        self.assertEqual(again, {"v": first["v"], "same": True})
        self.assertIn("html", json.loads(self.request("/live/statistics?v=eski").payload))

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
                self.assertIsNone(response.headers["Content-Encoding"])
            request = urllib.request.Request(f"{base}/", headers={"Accept-Encoding": "gzip"})
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertEqual(response.headers["Content-Encoding"], "gzip")
                self.assertIn("Özet Tablo", gzip.decompress(response.read()).decode())

            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args, **kwargs):
                    return None

            opener = urllib.request.build_opener(NoRedirect)
            with self.assertRaises(urllib.error.HTTPError) as caught:
                opener.open(urllib.request.Request(f"{base}/reset-notifications", data=b"", method="POST"), timeout=5)
            caught.exception.close()
            self.assertEqual(caught.exception.code, 303)
            self.assertTrue(caught.exception.headers["Location"].startswith("./settings?saved=ok"))
            self.assertEqual(runtime.resets, ["notifications"])
        finally:
            httpd.shutdown()
            httpd.server_close()


class DashboardTests(DataFilesMixin, unittest.TestCase):
    def test_failed_variants_show_exact_identity_link_time_and_no_duplicate_watch_error(self):
        self.data.write_state({"watch": {"site": "amazon", "watch_name": "Telefon", "last_error": "Fiyat yok",
                                        "last_checked_at": utc_now()}})
        context = {"site": "amazon", "watch_name": "Telefon", "watch_url": "https://example.test/root", "failures": [
            {"product_title": "iPhone Pro Max 512 GB / Gümüş Rengi", "variant": "512 GB / Gümüş Rengi",
             "product_url": "https://example.test/silver", "reason": "Fiyat yok"},
            {"product_title": "iPhone Pro 256 GB / Burgonya", "variant": "256 GB / Burgonya",
             "product_url": "https://example.test/burgundy", "reason": "Stok doğrulanamadı"}]}
        diagnostics = Diagnostics(self.data.files.database)
        for kind in ("partial", "read"):
            diagnostics.incident("watch", kind, "Fiyat yok", "Diğer varyantlar okunuyor", context=context)
        for base in (".", "/public/test-access"):
            html = dashboard.dashboard_live_html(base)
            self.assertIn("Açık sorunlar <small>2", html)
            self.assertIn("<strong>iPhone Pro Max</strong>", html)
            self.assertIn("512 GB / Gümüş", html)
            self.assertIn("<strong>iPhone Pro</strong>", html)
            self.assertIn("256 GB / Burgonya", html)
            self.assertIn("href='https://example.test/silver'", html)
            self.assertIn("href='https://example.test/burgundy'", html)
            self.assertEqual(html.count("class='problem-row'"), 2)
            self.assertNotIn("Son hata:", html)
            self.assertNotIn("href='https://example.test/root'", html)

    def test_unknown_error_identity_is_not_inferred_and_unsafe_links_are_not_rendered(self):
        diagnostic = Diagnostics(self.data.files.database)
        diagnostic.incident("watch", "partial", "Fiyat yok", context={"site": "amazon", "failures": [
            {"product_title": "", "variant": "Burgonya", "product_url": "javascript:alert(1)", "reason": "<script>"}]})
        html = self.live()
        self.assertIn("Ürün adı bu okumada alınamadı", html)
        self.assertIn("Burgonya</span>", html)
        self.assertIn("Okuma hatası", html)
        self.assertNotIn("<script>", html)
        self.assertNotIn("javascript:", html)

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
        self.assertIn("Açık sorunlar <small>2</small>", html)
        self.assertIn("<strong>iPhone</strong>", html)
        self.assertNotIn("Eski", html)
        self.assertGreater(html.index("Açık sorunlar <small>"), html.index("Özet Tablo"))
        self.data.write_state({})
        self.assertIn("Şu anda açık sorun yok", dashboard.render_dashboard_page(".", {}).decode())

    def test_error_links_of_variants_are_listed(self):
        details = dashboard.error_link_details("https://www.hepsiburada.com/a-p-1 | Fiyat yok; https://www.hepsiburada.com/b-p-2 | Erişim yok")
        self.assertEqual([item["url"] for item in details], ["https://www.hepsiburada.com/a-p-1", "https://www.hepsiburada.com/b-p-2"])
        self.assertEqual(dashboard.clean_error_message("https://x.test | Site 403"), "Site 403")

    def live(self):
        return dashboard.dashboard_live_html(".")

    def test_multi_result_watch_gets_its_own_section_with_variant_tags(self):
        rows = [price_row(product_title="Juo Q3, 256 GB / Yeşil", product_url="https://example.test/green", price="14.000 TL",
                          search_group="g", search_group_label="Juo Q3"),
                price_row(product_title="Juo Q3, 256 GB / Kırmızı", product_url="https://example.test/red", price="14.500 TL",
                          search_group="g", search_group_label="Juo Q3")]
        self.data.write_summary({"rows": rows})
        html = self.live()
        self.assertIn("<h3 class='ov-group-head site-amazon'>", html)
        self.assertIn("2 sonuç", html)
        self.assertIn("<em>Yeşil</em>", html)
        self.assertIn("<em>Kırmızı</em>", html)
        self.assertNotIn("<em>256 GB</em>", html)  # shared by every result, so it is not a difference

    def test_watching_tiles_are_ordered_by_distance_to_target_and_single_groups_stay_plain(self):
        rows = [price_row(product_title="Uzak", product_url="https://example.test/far", price="20.000 TL"),
                price_row(product_title="Yakın", product_url="https://example.test/near", price="13.100 TL"),
                price_row(product_title="Tek", product_url="https://example.test/one", price="16.000 TL",
                          search_group="g", search_group_label="Tek")]
        self.data.write_summary({"rows": rows})
        html = self.live()
        self.assertEqual([html.index(f"aria-label='{title}'") for title in ("Yakın", "Tek", "Uzak")],
                         sorted(html.index(f"aria-label='{title}'") for title in ("Yakın", "Tek", "Uzak")))
        self.assertNotIn("ov-group-head", html)

    def test_deals_come_first_with_the_biggest_discount_first_and_only_watching_rows_are_tiles(self):
        rows = [price_row(product_title="Az indirim", product_url="https://example.test/a", price="12.900 TL"),
                price_row(product_title="Çok indirim", product_url="https://example.test/b", price="10.000 TL"),
                price_row(product_title="Hedef üstü", product_url="https://example.test/c", price="14.000 TL")]
        self.data.write_summary({"rows": rows})
        html = self.live()
        self.assertEqual(html.count("class='ov-deal "), 2)
        self.assertEqual(html.count("class='ov-tile "), 1)
        self.assertLess(html.index("aria-label='Çok indirim'"), html.index("aria-label='Az indirim'"))
        self.assertIn("<div class='ov-num deal'><b>2</b><span>fırsat</span></div>", html)
        self.assertIn("−%23,1<small>hedefin altında</small>", html)

    def test_site_filter_chips_list_only_sellers_that_have_tiles(self):
        self.data.write_summary({"rows": [price_row(price="14.000 TL"), price_row(seller="Network", product_url="https://n.test/p", price="14.000 TL")]})
        html = self.live()
        self.assertIn("data-filter='site-amazon'", html)
        self.assertIn("data-filter='site-network'", html)
        self.assertNotIn("data-filter='site-zara'", html)

    def test_stock_rows_show_reason_target_link_and_when_they_were_last_checked(self):
        stamp = (datetime.now(timezone.utc) - timedelta(days=2, minutes=1)).isoformat()
        self.data.write_summary({"rows": [], "stock_rows": [
            {"seller": "Zara", "product_title": "Polo / M", "product_url": "https://zara.test/p", "target": "1.500 TL",
             "reason": "Zara beden stokta değil: M", "checked_at": stamp},
            {"seller": "H&M", "product_title": "Pantolon / L", "target": "1.200 TL"}]})
        html = self.live()
        self.assertIn("<li class='ov-row site-zara'>", html)
        self.assertIn("<a href='https://zara.test/p'", html)
        self.assertIn("Zara · Zara beden stokta değil: M · hedef 1.500 TL", html)
        self.assertIn("<span class='ov-ago'>2 gün önce</span>", html)
        self.assertIn("<span class='ov-ago'>-</span>", html)
        self.assertIn("Stokta yok <small>2</small>", html)
        self.assertIn("H&amp;M · Stokta yok · hedef 1.200 TL", html)

    def test_warehouse_offers_are_tagged_and_have_no_scan_interval(self):
        self.data.write_summary({"rows": [price_row(is_warehouse=True)]})
        html = self.live()
        self.assertIn("<span class='ov-depo'>DEPO</span>", html)
        self.assertNotIn("Tarama sıklığı", html)
        self.assertIn("<span class='ov-depo'>DEPO</span>", html.split("<div class='ov-detail'")[0])  # on the visible card
        self.data.write_summary({"rows": [price_row(is_warehouse=True), price_row(product_url="https://example.test/n")]})
        deals = self.live()
        self.assertEqual(deals.count("ov-deal-depo"), 1)  # only the Depo deal card turns yellow
        self.assertIn(".ov-deal-depo", APP_CSS)
        self.data.write_summary({"rows": [price_row(is_warehouse=True, price="14.000 TL"), price_row(product_url="https://example.test/n", price="14.000 TL")]})
        self.assertEqual(self.live().count("ov-tile-depo"), 1)  # a watching Depo tile is yellow too
        self.assertIn(".ov-tile-depo", APP_CSS)
        self.data.write_summary({"rows": [price_row(is_warehouse=True, price="14.000 TL")]})
        self.assertIn("<div class='ov-vars'><span class='ov-depo'>DEPO</span></div>", self.live().split("<div class='ov-detail'")[0])
        self.data.write_summary({"rows": [price_row(price="14.000 TL")]})
        self.assertNotIn("DEPO", self.live())

    def test_detail_shows_one_of_five_priority_dots(self):
        for priority, label in (("cycle", "Her çevrimde fiyat taranır"), ("30m", "30 dk'da bir taranır"),
                                ("60m", "60 dk'da bir taranır"), ("3h", "3 saatte bir taranır"), ("6h", "6 saatte bir taranır"),
                                ("high", "6 saatte bir taranır")):  # a row saved before 3.12
            with self.subTest(priority=priority):
                self.data.write_summary({"rows": [price_row(priority=priority)]})
                html = self.live()
                short = {"cycle": "Her çevrim", "30m": "30 dk", "60m": "60 dk", "3h": "3 saat", "6h": "6 saat", "high": "6 saat"}[priority]
                self.assertIn(f"priority-{'6h' if priority == 'high' else priority}", html)
                self.assertIn(f'title="{escape(label, quote=True)}"', html)
                self.assertIn(f"</i>{short}</b>", html)  # a short word under "Tarama sıklığı", not a sentence
        colors = [APP_CSS.split(f".priority-{key} {{ background:")[1].split(";")[0] for key in ("cycle", "30m", "60m", "3h", "6h")]
        self.assertEqual(colors, ["#ff5c64", "#ff9548", "#f2c94c", "#c4dc4a", "#3fbf6a"])  # red to green

    def test_stock_count_leaves_the_title_and_the_full_detail_is_in_the_sheet(self):
        self.data.write_summary({"rows": [price_row(product_title="Edifier M60 (Stok 5)", price="14.000 TL", min_price="12.000 TL",
                                                    max_price="15.000 TL", price_checked_at=utc_now())]})
        html = self.live()
        self.assertIn("<h4 title='Edifier M60'>Edifier M60</h4>", html)
        self.assertIn("<span>Stok</span><b>5 adet</b>", html)
        for fact in ("En düşük</span><b>12.000 TL", "En yüksek</span><b>15.000 TL", "Satıcı</span><b>Amazon",
                     "Son güncelleme</span><b>az önce", "Hedef 13.000 TL", "hedefe 1.000 TL var", "Ürüne git"):
            self.assertIn(fact, html)

    def test_price_line_comes_from_the_price_history_database(self):
        url = "https://www.amazon.com.tr/dp/B000000001"
        self.data.write_state({"offer": {"url": url, "tracking_id": "card1", "is_warehouse": False}})
        store = History.at(self.data.files.database)
        store.record_price("offer", "amazon", "Ürün", Decimal("16000"), datetime.now(timezone.utc) - timedelta(days=9))
        store.record_price("offer", "amazon", "Ürün", Decimal("14000"), datetime.now(timezone.utc) - timedelta(days=2))
        store.close()
        self.data.write_summary({"rows": [price_row(price="14.000 TL", tracking_id="card1")]})
        html = self.live()
        self.assertIn("class='ov-chart'", html)
        self.assertIn("data-points='[[", html)  # [x, y, epoch, price] per stored change, for the hover bubble
        self.assertIn(", 16000]", html)
        self.assertIn("class='ov-cross'", html)
        self.assertIn("<span class='ov-chg down' title='İlk kayıtlı fiyata göre (", html)  # 16.000 -> 14.000
        self.assertIn("▼ %12,5</span>", html)
        self.assertIn("<span>İlk kayıtlı fiyat</span><b>16.000 TL</b>", html)
        self.assertIn("Takip başlangıcı", html)
        self.data.write_summary({"rows": [price_row(price="14.000 TL", tracking_id="other card")]})
        self.assertNotIn("class='ov-chart'", self.live())
        self.assertIn("<span class='ov-chg flat'>yeni</span>", self.live())

    def test_price_that_rose_since_the_first_record_is_marked_up(self):
        self.data.write_state({"offer": {"url": "https://www.amazon.com.tr/dp/B000000001", "tracking_id": "", "is_warehouse": False}})
        store = History.at(self.data.files.database)
        store.record_price("offer", "amazon", "Ürün", Decimal("10000"), datetime.now(timezone.utc) - timedelta(days=3))
        store.close()
        self.data.write_summary({"rows": [price_row(price="14.000 TL")]})
        self.assertIn("▲ %40,0", self.live())

    def test_settings_share_the_home_screen_skin_and_the_logo_is_a_spinning_ring(self):
        page = self.router_get("/settings")
        self.assertIn("<body class='public ov settings-page'>", page)
        css = self.router_get("/app.css")
        for text in ("body.ov .topbar .badge::before", "conic-gradient", "ov-spin", ".settings-page .settings-section",
                     "body.ov .button.primary"):
            self.assertIn(text, css)

    def test_every_page_wears_the_same_skin(self):
        router = server.Router(FakeRuntime())
        for path in ("/", "/settings", "/statistics", "/restarting"):
            page = router.handle(server.split_request(path, "GET", b"", False)).payload.decode()
            self.assertIn("<body class='public ov", page, path)
        for text in (".ov .stat-tile", ".ov .summary-panel", ".ov .site-health", ".ov .health-ok"):
            self.assertIn(text, self.router_get("/app.css"))

    def test_prices_are_whole_lira(self):
        self.assertEqual(parse_decimal("1.500"), Decimal("1500"))
        self.assertEqual(settings._price_input_value("3000,0"), "3.000")
        self.assertEqual(dashboard.display_tl("1.500,75"), "1.500 TL")
        self.assertEqual(dashboard.display_tl("+125,90", signed=True), "+125 TL")
        self.write_whole_lira_row()

    def write_whole_lira_row(self):
        self.data.write_summary({"rows": [price_row(price="14.000,75 TL", price_checked_at=utc_now())]})
        self.assertIn("<div class='ov-price'>14.000<small>TL</small></div>", self.live())

    def test_price_age_reads_in_minutes_hours_or_days(self):
        checked_at = (datetime.now(timezone.utc) - timedelta(minutes=125)).isoformat()
        self.data.write_summary({"rows": [price_row(price="14.000 TL", price_checked_at=checked_at)]})
        self.assertIn("<span class='ov-ago'>2 sa önce</span>", self.live())

    def test_the_home_screen_keeps_its_state_across_in_place_refreshes(self):
        script = self.router_get("/overview.js")
        for text in ("pointermove", "ov-tip", "ov-cross", "tr-TR", "hermes-live", "data-tab", "data-filter", "data-open", "ov-first", "Escape", "overflow"):
            self.assertIn(text, script)
        self.assertIn("hermes-live", self.router_get("/live.js"))

    def test_fonts_ship_with_the_panel(self):
        router = server.Router(FakeRuntime())
        for name in assets.FONT_FILES:
            response = router.handle(server.split_request(f"/fonts/{name}", "GET", b"", False))
            self.assertEqual((response.status, response.content_type), (200, "font/woff2"))
            self.assertTrue(response.payload.startswith(b"wOF2"))
            self.assertIn(f"fonts/{name}", APP_CSS)

    def router_get(self, path):
        response = server.Router(FakeRuntime()).handle(server.split_request(path, "GET", b"", False))
        return response.payload.decode()

    def test_telegram_notifications_are_listed(self):
        (self.data.root / "status.json").write_text(json.dumps({"recent_notifications": [
            {"keyword": "airpods", "channel": "@firsatz", "created_at": "2026-10-03 12:00:00", "message": "AirPods indirim",
             "url": "https://t.me/firsatz/1"}]}), encoding="utf-8")
        html = dashboard.render_dashboard_page(".", {}).decode()
        self.assertIn("https://t.me/firsatz/1", html)  # shown even before the first price table exists
        self.assertIn("AirPods indirim", html)
        self.data.write_summary({"rows": [price_row(price="14.000 TL")]})
        html = dashboard.render_dashboard_page(".", {}).decode()
        self.assertIn("data-pane='tg'", html)
        self.assertIn("AirPods indirim", html)
        self.assertIn("Telegram <small>1</small>", html)


class StatisticsPageTests(DataFilesMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.store = History.at(self.data.files.database)
        self.now = datetime.now().astimezone()
        self.runtime = FakeRuntime()
        router = server.Router(self.runtime)
        self.request = lambda path, public_only=False: router.handle(server.split_request(path, "GET", b"", public_only))
        self.request_post = lambda path: router.handle(server.split_request(path, "POST", b"", False))

    def read(self, minutes_ago, site="amazon", outcome="ok", key="w1", priority="cycle", ms=2000, detail="", load=None):
        moment = self.now - timedelta(minutes=minutes_ago)
        with patch.object(history_module, "_at", lambda value=None: moment.astimezone(timezone.utc).isoformat(timespec="seconds")):
            self.store.record_read(site, outcome, ms, key, priority, detail, load)

    def page(self, period="24h"):
        return self.request(f"/statistics?p={period}").payload.decode()

    def test_empty_database_explains_itself(self):
        html = self.page()
        self.assertIn("henüz kontrol sıklığı ölçümü yok", html)
        self.assertIn("Bu dönemde okuma yok.", html)
        self.assertIn("Bu dönemde engel veya hata yok.", html)

    def test_check_frequency_comes_from_gaps_between_reads_of_one_high_priority_watch(self):
        for minutes in (30, 26, 22):  # every 4 minutes
            self.read(minutes, key="iphone")
        for minutes in (30, 10):  # a medium card does not count
            self.read(minutes, key="kahve", priority="60m")
        self.read(29, key="")  # rows from 3.3 and older have no watch
        self.read(500, key="iphone")  # a 7-hour pause is not the rhythm
        html = self.page()
        tile = html.split("Kontrol sıklığı</span><strong>")[1].split("<")[0]
        self.assertEqual(tile, "4 dk")
        self.assertIn("class='check-bar'", html)

    def test_sites_show_health_bar_and_error_types(self):
        self.read(50)
        self.read(40, outcome="captcha")
        self.read(30, outcome="http_503")
        self.read(20, outcome="timeout")
        self.read(10, site="hepsiburada", outcome="unreadable", key="zeytin")
        self.read(5, site="hepsiburada", key="zeytin", ms=30000)
        self.store.record_request("amazon", "curl", "ürün", "bot_korumasi", 300)
        html = self.page()
        self.assertIn("<span>Başarı</span><strong>%33,3</strong>", html)
        self.assertIn("<strong>4</strong><small>0 kısmi · 2 engel · 2 hata</small>", html)
        amazon = html.split("<strong>Amazon</strong>")[1].split("</article>")[0]
        self.assertIn("4 okuma · %25,0 başarılı", amazon)
        self.assertIn("class='health-ok' style='flex-grow:1'", amazon)
        self.assertIn("class='health-blocked' style='flex-grow:2'", amazon)
        self.assertIn("1 ağ isteği · 1 captcha", amazon)
        errors = html.split("Engel ve hata türleri")[1].split("</table>")[0]
        for label in ("Captcha (bot koruması)", "503 · site meşgul", "Zaman aşımı", "Sayfa okunamadı"):
            self.assertIn(f"<td>{label}</td>", errors)
        self.assertLess(errors.index("Captcha"), errors.index("Zaman aşımı"))
        self.assertIn("<th>Tür</th><th>Amazon</th><th>Hepsiburada</th><th>En son</th>", errors)

    def test_failures_close_together_are_one_spell_with_reason_and_pi_load(self):
        url = "https://www.amazon.com.tr/dp/B0D95R2PXM/"
        timeout = f"{url} | Amazon gerçek tarayıcı sayfası okunamadı (TimeoutException)."
        for minutes, load in ((100, SystemLoad(80, 900)), (95, SystemLoad(160, 400)), (85, None)):
            self.read(minutes, outcome="timeout", detail=timeout, load=load)
        self.read(90)  # successful reads in between do not end the spell
        self.read(88, outcome="unreadable", detail="Fiyat bulunamadı")
        self.read(50, outcome="captcha")  # 35 minutes later: a new spell
        self.read(40, site="togg", outcome="connection")
        html = self.page()
        spells = html.split("Hata dönemleri")[1].split("</ul>")[0]
        rows = spells.split("<li>")[1:]
        self.assertEqual(len(rows), 3)
        self.assertIn("Togg</strong><span>1 okuma · Bağlantı hatası</span>", rows[0])
        self.assertIn("Amazon</strong><span>1 okuma · Captcha (bot koruması)</span>", rows[1])
        start, end = self.now - timedelta(minutes=100), self.now - timedelta(minutes=85)
        end_text = f"{end:%H:%M}" if end.date() == start.date() else f"{end:%d.%m %H:%M}"
        self.assertIn(f"<strong>{start:%d.%m %H:%M}–{end_text} · Amazon</strong>", rows[2])
        self.assertIn("<span>4 okuma · Zaman aşımı 3, Sayfa okunamadı 1</span>", rows[2])
        self.assertIn("<small>Amazon gerçek tarayıcı sayfası okunamadı (TimeoutException). · Pi: sistem yükü %160 · "
                      "boş bellek 400 MB</small>", rows[2])
        self.assertNotIn(url, spells)

    def test_reset_button_asks_first_and_returns_to_statistics_with_the_result(self):
        html = self.page("7d")
        form = html.split("statistics-reset")[1].split("</form>")[0]
        self.assertIn("action='./reset-errors'", form)
        self.assertIn("data-confirm='İstatistikteki tüm engel ve hata kayıtları kalıcı olarak silinecek", form)
        self.assertIn("form[data-confirm]", html)  # the confirm script is on the page
        response = self.request_post("/reset-errors")
        self.assertEqual(response.status, 303)
        self.assertTrue(response.headers["Location"].startswith("./statistics?saved=ok&msg="))
        self.assertEqual(self.runtime.resets, ["errors"])
        notice = self.request("/statistics?saved=ok&msg=Hata+kay%C4%B1tlar%C4%B1+silindi+%283+kay%C4%B1t%29.").payload.decode()
        self.assertIn("<p class='notice notice-ok'>Hata kayıtları silindi (3 kayıt).</p>", notice)

    def test_period_switch_drives_the_whole_page(self):
        self.read(3 * 24 * 60, outcome="captcha")
        day = self.page("24h")
        week = self.page("7d")
        self.assertIn("Bu dönemde engel veya hata yok.", day)
        self.assertIn("Captcha (bot koruması)", week)
        self.assertIn("href='./statistics?p=7d' aria-current='page'", week)
        self.assertIn("data-live-url='./live/statistics?p=7d'", week)
        self.assertEqual(week.count("<rect class='check-bar'"), 0)
        self.assertIn("Günlük geçmiş", day)
        self.assertEqual(self.page("bozuk"), day)  # unknown periods fall back to 24 hours
        self.write_options({"public_dashboard_enabled": True, "public_dashboard_token": TOKEN})
        public = self.request(f"/public/{TOKEN}/statistics?p=7d", public_only=True).payload.decode()
        self.assertIn(f"href='/public/{TOKEN}/statistics?p=24h'", public)
        self.assertIn(f"data-live-url='/public/{TOKEN}/live/statistics?p=7d'", public)

    def test_last_cycle_tile_uses_the_published_summary(self):
        self.data.write_summary({"checked_at": (self.now - timedelta(minutes=3)).strftime("%Y-%m-%d %H:%M:%S"),
                                 "cycle_duration_seconds": 270})
        self.assertIn("<small>süresi 4 dk 30 sn</small>", self.page())


class SettingsTests(DataFilesMixin, unittest.TestCase):
    def test_seller_filter_and_priority_are_saved_and_rendered(self):
        watches = settings.build_watches({"watches_count": ["1"], "watches_0_name": ["iPhone"], "watches_0_target_price": ["100000"],
                                          "watches_0_url_1": ["https://www.amazon.com.tr/dp/B000000001"],
                                          "watches_0_priority": ["3h"], "watches_0_official_seller_only": ["1"]})
        self.assertEqual(watches[0]["priority"], "3h")
        self.assertTrue(watches[0]["official_seller_only"])
        html = settings.watch_form(watches[0], 0)
        for text in ("Yalnızca platformun kendi satıcısı", ">Her çevrim</option>", ">30 dk</option>", ">60 dk</option>",
                     "selected>3 saat</option>", ">6 saat</option>", "<summary><i class=\"priority-dot priority-3h\""):
            self.assertIn(text, html)
        for old in ("Yüksek", "Orta", "Düşük"):
            self.assertNotIn(old, html)
        # A new card starts at every cycle; a card saved before 3.12 shows 6 hours.
        self.assertIn("value='cycle' title='Her çevrimde fiyat taranır' selected>", settings.watch_form({}, 0, is_new=True))
        self.assertIn("selected>6 saat</option>", settings.watch_form({"name": "Eski", "priority": "high"}, 0))

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
                     "src='./settings.js?v="):
            self.assertIn(text, page)
        for text in ("name='delete_watch_index'", "name='update_watch_index'", "name='watch_index'"):
            self.assertNotIn(text, page)

    def test_timing_options_sit_at_the_bottom_and_are_saved_with_the_page(self):
        self.write_options({"interval_seconds": 5, "request_delay_min_seconds": 1, "request_delay_max_seconds": 4, "takip_edilenler": []})
        page = settings.render_settings_page(".", {}).decode()
        timing = page[page.index("timing-settings"):page.index("apply-bar")]
        for label, name, value in (("Çevrim aralığı", "interval_seconds", 5), ("Bekleme süresi min", "request_delay_min_seconds", 1),
                                   ("Bekleme süresi maks", "request_delay_max_seconds", 4)):
            self.assertIn(f"{label}<input type='number' inputmode='numeric' name='{name}'", timing)
            self.assertIn(f"value='{value}' required>", timing)
        self.assertGreater(page.index("timing-settings"), page.index("Telegram takip"))
        source = {"interval_seconds": 5, "request_delay_min_seconds": 1, "request_delay_max_seconds": 4, "takip_edilenler": []}
        form = {"operation": ["update_existing"], "interval_seconds": ["30"], "request_delay_min_seconds": ["2"],
                "request_delay_max_seconds": ["6"]}
        options, _ = settings.apply_settings_operation(source, form)
        self.assertEqual((options["interval_seconds"], options["request_delay_min_seconds"], options["request_delay_max_seconds"]),
                         (30, 2, 6))
        # A card saved on its own (or an older page) carries no timing fields and keeps the saved values.
        options, _ = settings.apply_settings_operation(source, {"operation": ["update_existing"]})
        self.assertEqual(options["interval_seconds"], 5)
        for bad, message in (({"interval_seconds": ["0"]}, "Çevrim aralığı 1 ile 86400"),
                             ({"request_delay_max_seconds": ["abc"]}, "Bekleme süresi maks tam sayı"),
                             ({"request_delay_min_seconds": ["9"], "request_delay_max_seconds": ["3"]}, "min, bekleme süresi maks")):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, message):
                settings.apply_settings_operation(source, {"operation": ["update_existing"], **bad})

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
            self.assertEqual({k:v for k,v in options["takip_edilenler"][0].items() if k != "id"}, source["takip_edilenler"][0])
            self.assertTrue(options["takip_edilenler"][0]["id"])
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


if __name__ == "__main__":
    unittest.main()
