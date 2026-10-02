import json
import requests
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


APP_PATH = Path(__file__).resolve().parents[1] / "ha-addon" / "app"
sys.path.insert(0, str(APP_PATH))

from hermes import service  # noqa: E402
from hermes import amazon_transport_trial as transport_trial  # noqa: E402
from hermes import http_client  # noqa: E402
from hermes import dashboard  # noqa: E402
from hermes import dashboard_with_settings  # noqa: E402
from hermes import public_dashboard  # noqa: E402
from hermes import settings_ui  # noqa: E402
from hermes import telegram_listener  # noqa: E402
from hermes import link_test_ui  # noqa: E402
from hermes.errors import HermesError, OutOfStockHermesError  # noqa: E402
from hermes.http_client import (  # noqa: E402
    amazon_url_variants,
    fetch_amazon_page,
    fetch_bengurme_page,
    fetch_beymenclub_page,
    fetch_beymenclub_size_summary,
)
from hermes.config_loader import _prepare_watches  # noqa: E402
from hermes import config_loader  # noqa: E402
from hermes.models import HermesConfig, OfferResult, PriceSummaryRow, SearchResultItem, StockSummaryRow, TelegramConfig, WatchRule  # noqa: E402
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
from hermes.providers.beymenclub import (  # noqa: E402
    extract_offer as extract_beymenclub_offer,
    extract_offers as extract_beymenclub_offers,
    extract_product_id as extract_beymenclub_product_id,
    requested_size_state_from_summary,
)
from hermes.providers.bengurme import extract_offers as extract_bengurme_offers  # noqa: E402
from hermes.providers.network import (  # noqa: E402
    _network_requested_size_state,
    extract_offer as extract_network_offer,
    extract_offers as extract_network_offers,
)
from hermes.providers.zara import extract_offers as extract_zara_offers  # noqa: E402
from hermes.providers.amazon import (  # noqa: E402
    extract_product_variations,
    extract_low_stock_quantity,
    extract_offer as extract_amazon_offer,
    extract_offers as extract_amazon_offers,
    extract_used_offer_listing_url,
    extract_verified_warehouse_offers_from_listing,
    is_warehouse_search_url,
    title_with_variation,
)
from hermes.search_amazon import AmazonSearchCandidate, dedupe_results, extract_result_candidates  # noqa: E402
from hermes.utils import detect_site_from_url, parse_decimal, utc_now  # noqa: E402


class HermesSmokeTests(unittest.TestCase):
    def test_transport_trial_balances_modes_expires_and_does_not_restart_on_second_click(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(transport_trial, "CONTROL_PATH", Path(directory) / "control.json"):
            now = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
            trial = transport_trial.start_trial(now)
            self.assertEqual(transport_trial.start_trial(now + timedelta(minutes=1))["id"], trial["id"])
            self.assertEqual([transport_trial.active_trial(now + timedelta(hours=i))["transport"] for i in range(4)],
                             ["browser", "http", "http", "browser"])
            self.assertIsNone(transport_trial.active_trial(now + timedelta(hours=24)))
            self.assertIsNone(transport_trial.active_trial(now - timedelta(seconds=1)))
            transport_trial.stop_trial(now + timedelta(minutes=10))
            self.assertIsNone(transport_trial.active_trial(now + timedelta(minutes=11)))

    def test_transport_trial_uses_existing_read_and_preserves_protection_wait(self):
        priced = WatchRule("Fiyat", "amazon", "https://www.amazon.com.tr/dp/B000000001", Decimal("1000"))
        blocked = WatchRule("Engelli", "amazon", "https://www.amazon.com.tr/dp/B000000002", Decimal("1000"))
        config = SimpleNamespace(watches=[priced, blocked], interval_seconds=1, request_timeout_seconds=10,
                                 pushover_user_key="user", pushover_api_token="token")
        state = {}
        key = service.normalize_item_key("watch", blocked.site, blocked.name, blocked.url, blocked.size)
        service.note_amazon_protection(state, key, blocked.name, HermesError("Amazon captcha"))
        trial = {"id": "test", "transport": "browser", "phase": 0}

        def offers(session, watch, config):
            self.assertEqual(session._hermes_amazon_client.transport, "browser")
            http_client._note_amazon_request(session, "browser", watch.url)
            return iter([OfferResult("Fiyat", Decimal("500"), url=watch.url)])

        with (patch.object(service, "load_json", return_value=state), patch.object(service, "save_json"),
              patch.object(transport_trial, "active_trial", return_value=trial),
              patch.object(transport_trial, "append_cycle") as record,
              patch.object(service, "wait_before_request"),
              patch.object(service, "_iter_amazon_product_watch_offers", side_effect=offers) as fetch,
              patch.object(service, "save_incremental_price_summary"), patch.object(service, "publish_price_summary"),
              patch.object(service, "record_cycle_duration"), patch.object(service, "send_pushover") as notify):
            service.check_once(config)
        fetch.assert_called_once()
        notify.assert_called_once()
        samples = record.call_args.args[1]
        self.assertEqual([item["network_attempts"] for item in samples], [1, 0])
        self.assertEqual([item["outcome"] for item in samples], ["priced", "protection_wait"])
        self.assertEqual(samples[0]["variant_count"], 1)
        self.assertEqual(samples[1]["captcha"], 0)

    def test_transport_trial_report_matches_cards_and_excludes_waits_from_error_rates(self):
        base = {"watch": "same", "config": "unchanged", "name": "Ürün", "network_attempts": 1,
                "seconds": 2, "captcha": 0, "http_503": 0, "outcome": "priced", "variant_count": 1, "warehouse_count": 0}
        samples = [{**base, "transport": "http"}, {**base, "transport": "browser", "captcha": 1, "outcome": "error"},
                   {**base, "transport": "browser", "network_attempts": 0, "captcha": 0, "outcome": "protection_wait"},
                   {**base, "transport": "http", "watch": "only-http"},
                   {**base, "transport": "browser", "config": "changed"}]
        report = transport_trial.summarize({"samples": samples})
        self.assertEqual(report["matched_cards"], 1)
        self.assertEqual(report["modes"]["http"]["reads"], 1)
        self.assertEqual(report["modes"]["browser"]["reads"], 1)
        self.assertEqual(report["modes"]["browser"]["captcha"], 1)
        self.assertEqual(report["protection_waits"], 1)
        self.assertEqual(report["unmatched_reads"], 2)

    def test_transport_trial_controls_never_launch_extra_product_reads(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(transport_trial, "CONTROL_PATH", Path(directory) / "control.json"), \
                patch.object(transport_trial, "RESULTS_PATH", Path(directory) / "results.json"), \
                patch.object(link_test_ui, "inspect_link_now") as fetch:
            for action in ("start", "report", "stop"):
                page = link_test_ui.render_link_test_from_request("", "./link-test", "./",
                    f"amazon_trial_action={action}".encode()).decode()
                self.assertIn("Amazon okuyucu karşılaştırması", page)
            fetch.assert_not_called()
            self.assertIsNone(transport_trial.active_trial())

    def test_transport_trial_expiry_restores_http_without_resetting_client(self):
        config = SimpleNamespace(watches=[], interval_seconds=1)
        with (http_client.AmazonClient(transport="browser") as client,
              patch.object(transport_trial, "active_trial", return_value=None),
              patch.object(service, "load_json", return_value={}), patch.object(service, "save_json")):
            client.browser_driver = SimpleNamespace(quit=lambda: None)
            service.check_once(config, client)
            self.assertEqual(client.transport, "http")
            self.assertIsNotNone(client.browser_driver)

    def test_transport_trial_missing_browser_restores_http_for_remaining_watches(self):
        watches = [WatchRule(f"Ürün {i}", "amazon", f"https://www.amazon.com.tr/dp/B00000000{i}", Decimal("1000"))
                   for i in range(2)]
        config = SimpleNamespace(watches=watches, interval_seconds=1, request_timeout_seconds=10,
                                 pushover_user_key="", pushover_api_token="")
        trial = {"id": "test", "transport": "browser", "phase": 0}
        modes = []

        def read(session, watch, config):
            modes.append(session._hermes_amazon_client.transport)
            if len(modes) == 1:
                raise HermesError("Amazon gerçek tarayıcı oturumu başlatılamadı: Chromium bulunamadı.")
            return iter([OfferResult("Ürün", Decimal("1500"), url=watch.url)])

        with (patch.object(service, "load_json", return_value={}), patch.object(service, "save_json"),
              patch.object(transport_trial, "active_trial", return_value=trial), patch.object(transport_trial, "stop_trial") as stop,
              patch.object(transport_trial, "append_cycle"), patch.object(service, "wait_before_request"),
              patch.object(service, "_iter_amazon_product_watch_offers", side_effect=read),
              patch.object(service, "save_incremental_price_summary"), patch.object(service, "publish_price_summary"),
              patch.object(service, "record_cycle_duration")):
            service.check_once(config)
        self.assertEqual(modes, ["browser", "http"])
        stop.assert_called_once()

    def test_transport_trial_measurements_do_not_mix_runs_or_overwrite_price_state(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(transport_trial, "RESULTS_PATH", Path(directory) / "results.json"):
            first = {"id": "first", "transport": "http", "phase": 0, "started_at": utc_now(), "ends_at": utc_now()}
            second = {**first, "id": "second"}
            transport_trial.append_cycle(first, [{"sample": "old"}], 1)
            transport_trial.append_cycle(second, [{"sample": "new"}], 2)
            saved = transport_trial.load_json(transport_trial.RESULTS_PATH, {})
            self.assertEqual(saved["samples"], [{"sample": "new"}])
            self.assertEqual(saved["id"], "second")


    def test_captcha_and_503_errors_do_not_notify_but_verified_depot_offer_does(self):
        errors = [HermesError("Amazon bot korumasi nedeniyle captcha/koruma sayfasi dondu."),
                  http_client.HttpStatusHermesError(503, "https://www.amazon.com.tr/s?k=test"),
                  requests.HTTPError("Service Unavailable", response=SimpleNamespace(status_code=503)),
                  HermesError("Hepsiburada bot korumasi nedeniyle captcha sayfasi dondu."),
                  HermesError("Amazon varyantları okunamadı.")]
        watches = [WatchRule(f"Arama {i}", "hepsiburada" if i == 3 else "amazon",
                             f"https://www.hepsiburada.com/ara?q=test{i}" if i == 3 else f"https://www.amazon.com.tr/s?k=test{i}",
                             Decimal("1000")) for i in range(5)]
        depot = WatchRule("Depo", "amazon", "https://www.amazon.com.tr/dp/B000000001", Decimal("1000"))
        config = SimpleNamespace(watches=watches + [depot], interval_seconds=1, request_timeout_seconds=10,
                                 pushover_user_key="test-user", pushover_api_token="test-token")
        state = {"_meta": {"summary_config_signature": service.summary_config_signature(config),
                           "summary_expected_row_count": 20, "summary_drop_consecutive_cycles": 4}}
        warehouse = OfferResult("Depo ürünü", Decimal("500"), seller="Amazon Depo", url=depot.url, is_warehouse=True)
        now = datetime(2026, 10, 2, service.AMAZON_SEARCH_ERROR_NOTIFICATION_HOUR, tzinfo=timezone.utc)

        def fail_search(session, watch, config):
            if watch == watches[4]:
                service.remember_amazon_protection(session, http_client.HttpStatusHermesError(503, watch.url))
            raise errors[watches.index(watch)]

        with (patch.object(service, "load_json", return_value=state), patch.object(service, "save_json"),
              patch.object(service, "wait_before_request"), patch.object(service, "local_now", return_value=now),
              patch.object(service, "_fetch_watch_offers", side_effect=fail_search),
              patch.object(service, "_iter_amazon_product_watch_offers", return_value=iter([warehouse])),
              patch.object(service, "save_incremental_price_summary"), patch.object(service, "publish_price_summary") as publish,
              patch.object(service, "record_cycle_duration"), patch.object(service, "send_pushover") as notify):
            service.check_once(config)
        notify.assert_called_once()
        self.assertIn("Depo ürünü", notify.call_args.args[4])
        self.assertEqual(len(publish.call_args.args[0]), 1)
        self.assertTrue(publish.call_args.args[0][0].is_warehouse)
        for watch in watches:
            entry = state[service.normalize_item_key("watch", watch.site, watch.name, watch.url, watch.size)]
            self.assertTrue(entry["last_error"])
            self.assertNotIn("last_error_notified_at", entry)
        self.assertNotIn("last_search_failure_alert_at", state["_meta"])
        self.assertNotIn("last_summary_drop_alert_at", state["_meta"])
        wrapped_key = service.normalize_item_key("watch", watches[4].site, watches[4].name, watches[4].url, watches[4].size)
        self.assertEqual(state[wrapped_key]["last_error_status"], 503)

    def test_partial_depot_opportunity_notifies_even_when_a_sibling_hits_captcha(self):
        watch = WatchRule("Depo", "amazon", "https://www.amazon.com.tr/dp/B000000001", Decimal("1000"))
        config = SimpleNamespace(watches=[watch], interval_seconds=1, request_timeout_seconds=10,
                                 pushover_user_key="test-user", pushover_api_token="test-token")
        state = {"_meta": {"summary_config_signature": service.summary_config_signature(config),
                           "summary_expected_row_count": 20, "summary_drop_consecutive_cycles": 4}}
        warehouse = OfferResult("Depo ürünü", Decimal("500"), seller="Amazon Depo", url=watch.url, is_warehouse=True)

        def partial_family(session, watch, config):
            yield warehouse
            service.remember_amazon_protection(session, HermesError("Amazon captcha"))

        with (patch.object(service, "load_json", return_value=state), patch.object(service, "save_json"),
              patch.object(service, "wait_before_request"),
              patch.object(service, "local_now", return_value=datetime(2026, 10, 2, 12, tzinfo=timezone.utc)),
              patch.object(service, "_iter_amazon_product_watch_offers", side_effect=partial_family),
              patch.object(service, "save_incremental_price_summary"), patch.object(service, "publish_price_summary"),
              patch.object(service, "record_cycle_duration"), patch.object(service, "send_pushover") as notify):
            service.check_once(config)
        notify.assert_called_once()
        self.assertIn("Depo ürünü", notify.call_args.args[4])
        key = service.normalize_item_key("watch", watch.site, watch.name, watch.url, watch.size)
        self.assertTrue(state[key]["amazon_partial_result"])
        self.assertIn("captcha", state[key]["last_error"])
        self.assertNotIn("last_summary_drop_alert_at", state["_meta"])

    def test_summary_drop_remains_silent_while_captcha_or_503_watch_is_deferred(self):
        watch = WatchRule("Arama", "amazon", "https://www.amazon.com.tr/s?k=test", Decimal("1000"))
        key = service.normalize_item_key("watch", watch.site, watch.name, watch.url, watch.size)
        config = SimpleNamespace(watches=[watch], pushover_user_key="user", pushover_api_token="token", request_timeout_seconds=10)
        now = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
        for message, status in (("Amazon captcha", None), ("Service Unavailable", 503)):
            with self.subTest(status=status):
                state = {key: {"last_error": message, "last_error_status": status},
                         "_meta": {"summary_config_signature": service.summary_config_signature(config),
                                   "summary_expected_row_count": 20, "summary_drop_consecutive_cycles": 4}}
                with patch.object(service, "local_now", return_value=now), patch.object(service, "send_pushover") as notify:
                    for _ in range(6):
                        service.maybe_alert_summary_drop(state, [], config, Mock())
                    notify.assert_not_called()
                    # Recovery restores the ordinary summary warning after five new cycles.
                    state[key]["last_error"] = None
                    state[key]["last_error_status"] = None
                    for _ in range(5):
                        service.maybe_alert_summary_drop(state, [], config, Mock())
                    notify.assert_called_once()

    def test_other_search_errors_still_send_individual_and_aggregate_notifications(self):
        watches = [WatchRule(f"Arama {i}", "amazon", f"https://www.amazon.com.tr/s?k=test{i}", Decimal("1000"))
                   for i in range(4)]
        config = SimpleNamespace(watches=watches, interval_seconds=1, request_timeout_seconds=10,
                                 pushover_user_key="test-user", pushover_api_token="test-token")
        state = {}
        now = datetime(2026, 10, 2, service.AMAZON_SEARCH_ERROR_NOTIFICATION_HOUR, tzinfo=timezone.utc)
        with (patch.object(service, "load_json", return_value=state), patch.object(service, "save_json"),
              patch.object(service, "wait_before_request"), patch.object(service, "local_now", return_value=now),
              patch.object(service, "_fetch_watch_offers", side_effect=http_client.HttpStatusHermesError(500, watches[0].url)),
              patch.object(service, "save_incremental_price_summary"), patch.object(service, "publish_price_summary"),
              patch.object(service, "record_cycle_duration"), patch.object(service, "send_pushover") as notify):
            service.check_once(config)
        self.assertEqual(notify.call_count, 5)
        self.assertIn("Hermes arama erişim uyarısı", [call.args[3] for call in notify.call_args_list])

    def test_amazon_expired_recovery_is_consumed_by_empty_result(self):
        watch = WatchRule("Juo", "amazon", "https://www.amazon.com.tr/s?k=Juo", Decimal("1000"), priority="medium")
        key = service.normalize_item_key("watch", watch.site, watch.name, watch.url, watch.size)
        state = {key: {"last_checked_at": utc_now()}}
        service.note_amazon_protection(state, key, "Juo", HermesError("Amazon captcha"))
        state["_meta"]["amazon_protection"][key]["retry_after"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        config = SimpleNamespace(watches=[watch], interval_seconds=1, request_timeout_seconds=10,
                                 pushover_user_key="", pushover_api_token="")
        with (patch.object(service, "load_json", return_value=state), patch.object(service, "save_json"),
              patch.object(service, "wait_before_request"),
              patch.object(service, "_fetch_watch_offers", side_effect=service.EmptySearchResultsHermesError("Boş sonuç")) as fetch,
              patch.object(service, "save_incremental_price_summary"), patch.object(service, "publish_price_summary"),
              patch.object(service, "record_cycle_duration"), patch.object(service, "maybe_alert_summary_drop"),
              patch.object(service, "maybe_alert_search_failures")):
            service.check_once(config)
            service.check_once(config)
        fetch.assert_called_once()
        self.assertNotIn(key, state["_meta"]["amazon_protection"])
        self.assertIsNone(state[key]["last_error"])

    def test_amazon_stock_absence_is_not_a_price_parser_failure(self):
        html = '<span id="productTitle">iPhone Gümüş</span><div id="availability">Şu anda mevcut değil.</div>'
        with self.assertRaises(OutOfStockHermesError) as caught:
            extract_amazon_offers(html, "https://www.amazon.com.tr/dp/B000000001")
        self.assertEqual(caught.exception.product_title, "iPhone Gümüş")
        self.assertEqual(caught.exception.product_url, "https://www.amazon.com.tr/dp/B000000001")
        # Unknown/broken markup and an active purchase with a missing price are errors.
        for broken in ('<html>garbled</html>', '<span id="productTitle">iPhone</span><div id="availability">Stokta var</div>'):
            with self.subTest(broken=broken), self.assertRaises(HermesError) as caught:
                extract_amazon_offers(broken)
            self.assertNotIsInstance(caught.exception, OutOfStockHermesError)
        # Recommendation stock and disabled siblings are not the selected product.
        priced = '<span id="productTitle">iPhone</span><div id="corePrice_feature_div"><span class="a-price"><span class="a-offscreen">100,00 TL</span></span></div>'
        priced += '<div id="recommendations">Şu anda mevcut değil</div>'
        self.assertEqual(extract_amazon_offers(priced)[0].price, Decimal("100.00"))

    def test_amazon_unavailable_product_does_not_reuse_stale_metadata_price(self):
        html = '<span id="productTitle">iPhone</span><div id="availability">Şu anda mevcut değil.</div><meta property="product:price:amount" content="1000">'
        with self.assertRaises(OutOfStockHermesError):
            extract_amazon_offers(html)

    def test_amazon_missing_root_price_still_visits_all_discovered_variants(self):
        urls = ["https://www.amazon.com.tr/dp/B000000001", "https://www.amazon.com.tr/dp/B000000002"]
        watch = WatchRule("iPhone", "amazon", urls[0], Decimal("1000"), include_variations=True)
        config = SimpleNamespace(request_timeout_seconds=10)
        variations = [service.amazon_provider.AmazonProductVariation("Gümüş", urls[0]),
                      service.amazon_provider.AmazonProductVariation("Turuncu", urls[1])]
        pages = ['<span id="productTitle">iPhone Gümüş</span><div id="availability">Şu anda mevcut değil.</div>',
                 '<span id="productTitle">iPhone Turuncu</span><div id="corePrice_feature_div"><span class="a-price"><span class="a-offscreen">100,00 TL</span></span></div>']
        session = requests.Session()
        with (patch.object(service, "fetch_amazon_page", side_effect=pages) as fetch,
              patch.object(service, "cleaned_html", side_effect=lambda value: value),
              patch.object(service, "wait_before_request"),
              patch.object(service.amazon_provider, "extract_product_variations", return_value=variations)):
            offers = list(service._iter_amazon_product_watch_offers(session, watch, config))
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual([offer.url for offer in offers], [urls[1]])
        self.assertEqual(session._hermes_amazon_unavailable_variants[0]["product_url"], urls[0])

    def test_amazon_depot_listing_is_read_without_a_new_product_price(self):
        url = "https://www.amazon.com.tr/dp/B000000001"
        html = '<span id="productTitle">iPhone</span><div id="availability">Şu anda mevcut değil.</div><a href="/gp/offer-listing/B000000001?condition=used">Kullanılmış teklifler</a>'
        depot = OfferResult("iPhone", Decimal("90"), "Amazon Depo", url, True)
        with (patch.object(service, "fetch_amazon_page", return_value="depot-listing") as fetch,
              patch.object(service, "cleaned_html", side_effect=lambda value: value),
              patch.object(service.amazon_provider, "extract_verified_warehouse_offers_from_listing", side_effect=[[], [depot]])):
            offers = service._extract_amazon_page_offers(requests.Session(), url, html, SimpleNamespace(request_timeout_seconds=10))
        fetch.assert_called_once()
        self.assertEqual(offers, [depot])

    def test_deferred_stock_row_retains_last_successful_stock_classification(self):
        watch = WatchRule("iPhone", "amazon", "https://www.amazon.com.tr/dp/B000000001", Decimal("1000"), priority="medium")
        state = {}
        config = SimpleNamespace(watches=[watch], interval_seconds=1, request_timeout_seconds=10,
                                 pushover_user_key="", pushover_api_token="")
        with (patch.object(service, "load_json", return_value=state), patch.object(service, "save_json"),
              patch.object(service, "wait_before_request"),
              patch.object(service, "_iter_amazon_product_watch_offers", side_effect=OutOfStockHermesError("Stokta yok", "iPhone", watch.url)) as fetch,
              patch.object(service, "save_incremental_price_summary"), patch.object(service, "publish_price_summary") as publish,
              patch.object(service, "record_cycle_duration"), patch.object(service, "maybe_alert_summary_drop"),
              patch.object(service, "maybe_alert_search_failures")):
            service.check_once(config)
            service.check_once(config)
        fetch.assert_called_once()
        self.assertEqual(len(publish.call_args.args[1]), 1)
        self.assertEqual(publish.call_args.args[1][0].product_url, watch.url)

    def test_amazon_no_offer_probe_is_bounded_without_hiding_priced_siblings(self):
        root = "https://www.amazon.com.tr/dp/B000000001"
        child = "https://www.amazon.com.tr/dp/B000000002"
        watch = WatchRule("iPhone", "amazon", root, Decimal("1000"), include_variations=True)
        config = SimpleNamespace(request_timeout_seconds=10)
        pages = {root: '<span id="productTitle">iPhone</span><div id="availability">Şu anda mevcut değil.</div>',
                 child: '<span id="productTitle">iPhone</span><div id="corePrice_feature_div"><span class="a-price"><span class="a-offscreen">100,00 TL</span></span></div>'}
        variations = [service.amazon_provider.AmazonProductVariation("Gümüş", root),
                      service.amazon_provider.AmazonProductVariation("Turuncu", child)]
        fetched = []
        def read(_session, url, _timeout):
            fetched.append(url)
            return pages[url]
        with (http_client.AmazonClient() as client, patch.object(service, "fetch_amazon_page", side_effect=read),
              patch.object(service, "cleaned_html", side_effect=lambda value: value),
              patch.object(service, "wait_before_request"), patch.object(service.time, "monotonic", return_value=100),
              patch.object(service.amazon_provider, "extract_product_variations", return_value=variations)):
            for price in ("100,00", "101,00"):
                pages[child] = pages[child].replace("100,00", price)
                session = requests.Session()
                session._hermes_amazon_client = client
                offers = list(service._iter_amazon_product_watch_offers(session, watch, config))
            self.assertEqual(fetched, [root, child, child])
            self.assertEqual(offers[0].price, Decimal("101"))
            # Time passes: a new offer appears at the root. The expiry is fixed;
            # looking at the cached absence must never postpone its next probe.
            pages[root] = pages[child]
            with patch.object(service.time, "monotonic", return_value=401):
                session = requests.Session()
                session._hermes_amazon_client = client
                offers = list(service._iter_amazon_product_watch_offers(session, watch, config))
            self.assertEqual(fetched, [root, child, child, root, child])
            self.assertEqual({offer.url for offer in offers}, {root, child})
            self.assertFalse(client.unavailable_product_pages)

    def test_amazon_missing_price_probe_is_bounded_but_never_becomes_fake_stock(self):
        url = "https://www.amazon.com.tr/dp/B000000001"
        watch = WatchRule("iPhone", "amazon", url, Decimal("1000"))
        config = SimpleNamespace(request_timeout_seconds=10)
        with (http_client.AmazonClient() as client,
              patch.object(service, "fetch_amazon_page", return_value='<span id="productTitle">iPhone</span>') as fetch,
              patch.object(service, "cleaned_html", side_effect=lambda value: value), patch.object(service, "wait_before_request")):
            for _ in range(2):
                session = requests.Session()
                session._hermes_amazon_client = client
                with self.assertRaises(HermesError) as caught:
                    list(service._iter_amazon_product_watch_offers(session, watch, config))
                self.assertNotIsInstance(caught.exception, OutOfStockHermesError)
            fetch.assert_called_once()
            self.assertEqual(len(client.unavailable_product_pages), 1)

    def test_amazon_access_failure_never_enters_no_offer_cache(self):
        url = "https://www.amazon.com.tr/dp/B000000001"
        watch = WatchRule("iPhone", "amazon", url, Decimal("1000"))
        with (http_client.AmazonClient() as client, patch.object(service, "fetch_amazon_page", side_effect=HermesError("Amazon captcha"))):
            session = requests.Session()
            session._hermes_amazon_client = client
            with self.assertRaisesRegex(HermesError, "captcha"):
                list(service._iter_amazon_product_watch_offers(session, watch, SimpleNamespace(request_timeout_seconds=10)))
            self.assertFalse(client.unavailable_product_pages)

    def test_all_unavailable_high_priority_watch_waits_until_its_next_probe(self):
        watch = WatchRule("iPhone", "amazon", "https://www.amazon.com.tr/dp/B000000001", Decimal("1000"))
        state = {}
        config = SimpleNamespace(watches=[watch], interval_seconds=1, request_timeout_seconds=10,
                                 pushover_user_key="", pushover_api_token="")
        html = '<span id="productTitle">iPhone</span><div id="availability">Şu anda mevcut değil.</div>'
        with (http_client.AmazonClient() as client, patch.object(service, "load_json", return_value=state),
              patch.object(service, "save_json"), patch.object(service, "wait_before_request"),
              patch.object(service, "fetch_amazon_page", return_value=html) as fetch,
              patch.object(service, "cleaned_html", side_effect=lambda value: value),
              patch.object(service, "save_incremental_price_summary"), patch.object(service, "publish_price_summary") as publish,
              patch.object(service, "record_cycle_duration"), patch.object(service, "maybe_alert_summary_drop"),
              patch.object(service, "maybe_alert_search_failures")):
            service.check_once(config, amazon_client=client)
            service.check_once(config, amazon_client=client)
            fetch.assert_called_once()
            self.assertEqual(len(publish.call_args.args[1]), 1)
            key = service.normalize_item_key("watch", watch.site, watch.name, watch.url, watch.size)
            retry_at = service.parse_iso_datetime(state[key]["amazon_no_offer_retry_after"])
            self.assertGreater((retry_at - datetime.now(timezone.utc)).total_seconds(), 295)
            # A user-requested new read is allowed immediately.
            watch.check_now_token = "edited"
            # Real settings writes restart the client; don't reuse its old negative cache.
            with http_client.AmazonClient() as fresh_client:
                service.check_once(config, amazon_client=fresh_client)
            self.assertEqual(fetch.call_count, 2)

    def test_no_featured_amazon_offer_does_not_mean_stock_is_absent(self):
        html = '<span id="productTitle">Apple iPhone 17 Pro</span><div id="availability"></div><span id="buybox-see-all-buying-choices"><a href="/gp/offer-listing/B000000001/ref=dp_olp_unknown_mbc">Satın Alma Seçeneklerini Gör</a></span>'
        with self.assertRaises(HermesError) as caught:
            extract_amazon_offers(html)
        self.assertNotIsInstance(caught.exception, OutOfStockHermesError)
        self.assertIn("stok durumu doğrulanamadı", str(caught.exception))

    def test_explicit_empty_current_offer_keys_never_restore_a_legacy_price(self):
        watch = WatchRule("iPhone", "amazon", "https://www.amazon.com.tr/dp/B000000001", Decimal("1000"))
        self.assertEqual(service.cached_summary_rows_for_watch(watch, "watch", {
            "watch": {"offer_keys": [], "last_price": "100", "last_checked_at": utc_now()}
        }, "Amazon"), [])

    def test_amazon_depot_no_results_notice_ignores_all_category_fallback(self):
        html = '<div id="search"><h2>Tüm Kategoriler içindeki sonuçlar gösteriliyor</h2><h3>Amazon Depo içinde <b>juo 240w</b> için sonuç bulunamadı</h3><div class="s-main-slot"><div data-component-type="s-search-result" data-asin="B000000001"><h2><a href="/dp/B000000001"><span>Juo 240W</span></a></h2><span class="a-price"><span class="a-offscreen">100,00 TL</span></span><span>Kullanılmış Amazon Depo</span></div></div></div>'
        with self.assertRaises(service.EmptySearchResultsHermesError) as caught:
            extract_result_candidates(html, 60, primary_is_warehouse=True)
        self.assertTrue(caught.exception.no_results_notice)
        self.assertIn("sonuç bulunamadı", str(caught.exception))
        watch = WatchRule("Juo 240W", "amazon", "https://www.amazon.com.tr/s?k=juo+240w&i=warehouse-deals", Decimal("1000"))
        with (patch.object(service, "fetch_amazon_page", return_value=html),
              patch.object(service, "cleaned_html", side_effect=lambda value: value),
              patch.object(service, "_fetch_amazon_detail_offers") as detail):
            with self.assertRaises(service.EmptySearchResultsHermesError):
                service._fetch_amazon_search_watch_offers(requests.Session(), watch, SimpleNamespace(request_timeout_seconds=10))
        detail.assert_not_called()

    def test_amazon_turkish_all_categories_heading_cuts_fallback_cards(self):
        def card(asin):
            return f'<div data-component-type="s-search-result" data-asin="{asin}"><h2><a href="/dp/{asin}"><span>Juo 240W</span></a></h2><span class="a-price"><span class="a-offscreen">100,00 TL</span></span></div>'
        html = '<div class="s-main-slot">' + card('B000000001') + '<h2>Tüm Kategoriler içindeki sonuçlar gösteriliyor</h2>' + card('B000000002') + '</div>'
        self.assertEqual([item.url for item in extract_result_candidates(html, 60)], ['https://www.amazon.com.tr/dp/B000000001'])

    def test_amazon_no_results_notice_in_hidden_or_script_text_is_ignored(self):
        card = '<div data-component-type="s-search-result" data-asin="B000000001"><h2><a href="/dp/B000000001"><span>Juo 240W</span></a></h2><span class="a-price"><span class="a-offscreen">100,00 TL</span></span></div>'
        for extra in ('<script>"Amazon Depo içinde juo 240w için sonuç bulunamadı"</script>',
                      '<div aria-hidden="true">Amazon Depo içinde juo 240w için sonuç bulunamadı</div>',
                      '<!-- Amazon Depo içinde juo 240w için sonuç bulunamadı -->'):
            with self.subTest(extra=extra):
                self.assertEqual(len(extract_result_candidates(extra + card, 60)), 1)

    def test_amazon_genuine_empty_search_is_usable_without_fallback_products(self):
        html = '<html>Amazon<div id="search"><h3>Amazon Depo içinde juo 240w için sonuç bulunamadı</h3></div></html>'
        self.assertTrue(http_client._is_usable_amazon_response(http_client._HtmlResponse('https://www.amazon.com.tr/s?k=juo', html, 200), True))

    def test_amazon_no_results_notice_is_normal_stock_state_not_error_notification(self):
        watch = WatchRule("Juo 240W", "amazon", "https://www.amazon.com.tr/s?k=juo+240w&i=warehouse-deals", Decimal("1000"))
        config = SimpleNamespace(watches=[watch], interval_seconds=1, request_timeout_seconds=10,
                                 pushover_user_key="", pushover_api_token="")
        state = {}
        html = '<html>Amazon<div id="search"><h3>Amazon Depo içinde juo 240w için sonuç bulunamadı</h3></div></html>'
        with (patch.object(service, "load_json", return_value=state), patch.object(service, "save_json"),
              patch.object(service, "wait_before_request"), patch.object(service, "fetch_amazon_page", return_value=html) as fetch,
              patch.object(service, "cleaned_html", side_effect=lambda value: value),
              patch.object(service, "save_incremental_price_summary"), patch.object(service, "publish_price_summary") as publish,
              patch.object(service, "record_cycle_duration"), patch.object(service, "maybe_alert_summary_drop"),
              patch.object(service, "maybe_alert_search_failures") as failures, patch.object(service, "send_pushover") as notify):
            service.check_once(config)
            service.check_once(config)
        fetch.assert_called_once()
        notify.assert_not_called()
        self.assertEqual(failures.call_args.args[1], [])
        key = service.normalize_item_key("watch", watch.site, watch.name, watch.url, watch.size)
        self.assertIsNone(state[key]["last_error"])
        self.assertEqual(len(publish.call_args.args[1]), 1)
        self.assertIn("sonuç bulunamadı", publish.call_args.args[1][0].reason)

    def test_link_test_empty_search_is_a_normal_notice(self):
        error = service.EmptySearchResultsHermesError("Amazon Depo içinde juo 240w için sonuç bulunamadı", no_results_notice=True)
        with patch.object(link_test_ui, "inspect_link_now", side_effect=error):
            page = link_test_ui.render_link_test_from_request('', './link-test', './',
                b'url=https%3A%2F%2Fwww.amazon.com.tr%2Fs%3Fk%3Djuo%2B240w&name=Juo+240W').decode()
        self.assertIn("Ürün bulunamadı", page)
        self.assertNotIn("Bağlantı okunamadı", page)
        self.assertNotIn("class='notice notice-fail'", page)

    def test_amazon_product_title_cannot_declare_the_search_empty(self):
        html = '<div id="search"><h2>Sonuçlar</h2><div data-component-type="s-search-result" data-asin="B000000001"><h2><a href="/dp/B000000001"><span>Hata rehberi: sorgu için sonuç bulunamadı</span></a></h2><span class="a-price"><span class="a-offscreen">100,00 TL</span></span></div></div>'
        self.assertEqual(len(extract_result_candidates(html, 60)), 1)

    def test_cycle_interval_accepts_values_below_ten_seconds(self):
        for interval in (1, 5, 8, 35):
            with self.subTest(interval=interval), patch.object(
                config_loader,
                "load_json",
                return_value={
                    "interval_seconds": interval,
                    "pushover_user_key": "user",
                    "pushover_api_token": "token",
                    "takip_edilenler": [
                        {"name": "Test", "target_price": 100, "url_1": "https://www.amazon.com.tr/dp/B000000001"}
                    ],
                },
            ):
                config = config_loader.load_config()
                self.assertEqual(config.interval_seconds, interval)

    def test_cycle_interval_rejects_values_below_one_second(self):
        with patch.object(
            config_loader,
            "load_json",
            return_value={"interval_seconds": 0, "pushover_user_key": "user", "pushover_api_token": "token"},
        ):
            with self.assertRaisesRegex(HermesError, "1 ile 86400 arasında"):
                config_loader.load_config()

    def test_amazon_primary_seller_is_separate_from_verified_depot_seller(self):
        html = '''<span id="productTitle">iPhone</span>
        <div id="corePriceDisplay_desktop_feature_div"><span class="a-price">
          <span class="a-offscreen">123.058,99 TL</span></span></div>
        <div id="merchantInfoFeature_feature_div">Gönderici: Amazon Satıcı:
          <a id="sellerProfileTriggerId">Amazon.com.tr</a></div>
        <div id="usedBuySection">Kullanılmış ve yeni gibi Satıcı: Amazon Depo
          <span class="a-price"><span class="a-offscreen">89.040,87 TL</span></span></div>'''

        offers = extract_amazon_offers(html, "https://www.amazon.com.tr/dp/B000000001")

        self.assertEqual([(offer.seller, offer.is_warehouse) for offer in offers], [
            ("Amazon.com.tr", False), ("Amazon Depo", True),
        ])

    def test_amazon_sender_is_not_mistaken_for_marketplace_seller(self):
        html = '''<span id="productTitle">Apple iPhone 17 Pro Max 512 GB Gümüş</span>
        <div id="corePriceDisplay_desktop_feature_div"><span class="a-price">
          <span class="a-offscreen">131.624,00 TL</span></span></div>
        <div id="merchantInfoFeature_feature_div">
          <span>Gönderici</span><span>Amazon</span>
          <span>Satıcı</span><a id="sellerProfileTriggerId">Gürgençler Apple Premium Partner</a>
        </div>'''

        offers = extract_amazon_offers(html, "https://www.amazon.com.tr/dp/B000000002")

        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].seller, "Gürgençler Apple Premium Partner")
        watch = WatchRule(
            name="iPhone", site="amazon", url="https://www.amazon.com.tr/dp/B000000002",
            target_price=Decimal("140000"), official_seller_only=True,
        )
        self.assertEqual(list(service.filter_official_seller_offers(watch, offers)), [])

    def test_amazon_plain_text_official_seller_survives_extra_buybox_text(self):
        html = '''<span id="productTitle">Apple iPhone 17 Pro Max 256 GB</span>
        <div id="corePriceDisplay_desktop_feature_div"><span class="a-price">
          <span class="a-offscreen">123.058,99 TL</span></span></div>
        <div id="merchantInfoFeature_feature_div">
          <span>Gönderici / Satıcı:</span><span>Amazon.com.tr</span>
          <a href="/gp/help/customer/display.html">Satıcı bilgileri</a>
        </div>'''

        offers = extract_amazon_offers(html, "https://www.amazon.com.tr/dp/B000000003")

        self.assertEqual(offers[0].seller, "Amazon.com.tr")
        watch = WatchRule(
            name="iPhone", site="amazon", url="https://www.amazon.com.tr/dp/B000000003",
            target_price=Decimal("130000"), official_seller_only=True,
        )
        self.assertEqual(list(service.filter_official_seller_offers(watch, offers)), offers)

    def test_official_seller_filter_excludes_other_new_sellers_but_always_keeps_depot(self):
        watch = WatchRule(
            name="iPhone", site="amazon", url="https://www.amazon.com.tr/dp/B000000001",
            target_price=Decimal("100000"), official_seller_only=True,
        )
        offers = [
            OfferResult("Üçüncü taraf sıfır", Decimal("90000"), "Başka Satıcı", watch.url),
            OfferResult("Satıcısı okunmayan sıfır", Decimal("91000"), None, watch.url),
            OfferResult("Amazon sıfır", Decimal("100000"), "Amazon.com.tr", watch.url),
            OfferResult("Depo", Decimal("89000"), "Amazon Depo", watch.url, True),
        ]

        filtered = list(service.filter_official_seller_offers(watch, offers))

        self.assertEqual([offer.title for offer in filtered], ["Amazon sıfır", "Depo"])

    def test_watch_priorities_use_global_cycle_two_hours_and_six_hours(self):
        now = datetime.now(timezone.utc)
        checked = {"last_checked_at": now.isoformat()}
        high = WatchRule("H", "amazon", "https://www.amazon.com.tr/dp/B000000001", Decimal("1"), priority="high")
        medium = WatchRule("M", "amazon", "https://www.amazon.com.tr/dp/B000000002", Decimal("1"), priority="medium")
        low = WatchRule("L", "amazon", "https://www.amazon.com.tr/dp/B000000003", Decimal("1"), priority="low")

        with patch.object(service, "local_now", return_value=now + timedelta(seconds=59)):
            self.assertFalse(service.watch_check_due(high, checked, 60))
        with patch.object(service, "local_now", return_value=now + timedelta(seconds=60)):
            self.assertTrue(service.watch_check_due(high, checked, 60))
        with patch.object(service, "local_now", return_value=now + timedelta(hours=1, minutes=59)):
            self.assertFalse(service.watch_check_due(medium, checked, 60))
            self.assertFalse(service.watch_check_due(low, checked, 60))
        with patch.object(service, "local_now", return_value=now + timedelta(hours=2)):
            self.assertTrue(service.watch_check_due(medium, checked, 60))
            self.assertFalse(service.watch_check_due(low, checked, 60))
        with patch.object(service, "local_now", return_value=now + timedelta(hours=6)):
            self.assertTrue(service.watch_check_due(low, checked, 60))

    def test_due_watch_order_places_high_priority_before_medium_and_low(self):
        def task(priority, site):
            watch = WatchRule(priority, site, f"https://www.amazon.com.tr/dp/{priority + site}", Decimal("1"), priority=priority)
            return {"watch": watch, "site": site}

        ordered = service.priority_request_order([
            task("low", "amazon"), task("high", "hepsiburada"), task("medium", "zara"), task("high", "amazon")
        ])

        self.assertEqual([item["watch"].priority for item in ordered], ["high", "high", "medium", "low"])

    def test_watch_settings_save_generic_seller_filter_and_priority(self):
        watches = settings_ui._build_watches({
            "watches_count": ["1"],
            "watches_0_name": ["iPhone"],
            "watches_0_target_price": ["100000"],
            "watches_0_url_1": ["https://www.amazon.com.tr/dp/B000000001"],
            "watches_0_priority": ["low"],
            "watches_0_official_seller_only": ["1"],
        })

        self.assertEqual(watches[0]["priority"], "low")
        self.assertTrue(watches[0]["official_seller_only"])
        html = settings_ui._watch_form(watches[0], 0)
        self.assertIn("Yalnızca platformun kendi satıcısı", html)
        self.assertIn("Düşük · 6 saatte bir", html)
        self.assertIn("Orta · 2 saatte bir", html)
        self.assertIn("Yüksek · her çevrim", html)

    def test_amazon_variant_url_change_preserves_alert_suppression_and_history(self):
        watch = WatchRule(name="iPhone", site="amazon", url="https://www.amazon.com.tr/dp/B000000001",
                          target_price=Decimal("100000"), include_variations=True)
        watch_key = service.normalize_item_key("watch", "amazon", "iPhone", watch.url, "")
        state = {"_meta": {"warehouse_state_migration_version": service.WAREHOUSE_STATE_MIGRATION_VERSION},
                 watch_key: {"offer_keys": ["existing-offer"]},
                 "existing-offer": {"url": watch.url + "?th=1", "is_warehouse": True,
                     "last_alerted_price": "90000", "last_alerted_at": utc_now(),
                     "min_price": "85000", "max_price": "95000"}}
        config = SimpleNamespace(watches=[watch], interval_seconds=60, request_timeout_seconds=20,
                                 pushover_user_key="test", pushover_api_token="test")
        with (patch.object(service, "load_json", return_value=state),
              patch.object(service, "save_json"), patch.object(service, "wait_before_request"),
              patch.object(service, "_iter_amazon_product_watch_offers", return_value=iter([
                  OfferResult("iPhone", Decimal("90000"), "Amazon Depo", watch.url + "?psc=1", True)])),
              patch.object(service, "send_pushover") as notify,
              patch.object(service, "save_incremental_price_summary"),
              patch.object(service, "publish_price_summary"),
              patch.object(service, "maybe_alert_summary_drop"),
              patch.object(service, "maybe_alert_search_failures")):
            service.check_once(config)
        notify.assert_not_called()
        self.assertEqual(state[watch_key]["offer_keys"], ["existing-offer"])
        self.assertEqual(state["existing-offer"]["min_price"], "85000")

    def test_amazon_used_evidence_cannot_leak_across_offer_rows_or_asins(self):
        html = '''<div data-cy="all-offers"><div id="corePrice_feature_div">
        <span class="a-price"><span class="a-offscreen">123.058,99 TL</span></span></div>
        <div data-csa-c-slot-id="usedAccordionRow" role="button" data-csa-c-asin="B000000002">
        Kullanılmış ve yeni gibi Satıcı: Amazon Depo
        <span class="a-price"><span class="a-offscreen">89.040,87 TL</span></span></div></div>'''
        self.assertEqual(extract_verified_warehouse_offers_from_listing(
            html, "https://www.amazon.com.tr/dp/B000000001"), [])
        matched = extract_verified_warehouse_offers_from_listing(
            html, "https://www.amazon.com.tr/dp/B000000002")
        self.assertEqual([o.price for o in matched], [Decimal("89040.87")])

    def test_amazon_cycle_notifies_and_saves_before_scanning_next_variant(self):
        watch = WatchRule(name="iPhone", site="amazon", url="https://www.amazon.com.tr/dp/B000000001",
                          target_price=Decimal("100000"), include_variations=True)
        config = SimpleNamespace(watches=[watch], interval_seconds=60, request_timeout_seconds=20,
                                 pushover_user_key="test", pushover_api_token="test")
        events = []

        def stream(*args):
            events.append("first")
            yield OfferResult("iPhone Gümüş 256 GB", Decimal("89040.87"), "Amazon Depo", watch.url, True)
            self.assertIn("notify", events)
            self.assertIn("save", events)
            events.append("second")
            yield OfferResult("iPhone Abis 512 GB", Decimal("132000"), url="https://www.amazon.com.tr/dp/B000000002")

        with (patch.object(service, "load_json", return_value={}),
              patch.object(service, "save_json", side_effect=lambda *a: events.append("save")),
              patch.object(service, "_iter_amazon_product_watch_offers", side_effect=stream),
              patch.object(service, "wait_before_request"),
              patch.object(service, "send_pushover", side_effect=lambda *a: events.append("notify")) as notify,
              patch.object(service, "save_incremental_price_summary"),
              patch.object(service, "publish_price_summary"),
              patch.object(service, "maybe_alert_summary_drop"),
              patch.object(service, "maybe_alert_search_failures")):
            service.check_once(config)
        self.assertLess(events.index("notify"), events.index("second"))
        self.assertEqual(notify.call_count, 1)
        self.assertIn("Depo", notify.call_args.args[3])
        self.assertIn("Amazon Depo", notify.call_args.args[4])

    def test_amazon_live_used_accordion_does_not_contaminate_new_price(self):
        # Minimized structure observed on B0FQF9XY3L on 2026-09-17.
        html = '''<span id="productTitle">iPhone 17 Pro Max 2 TB Kozmik Turuncu</span>
        <div data-csa-c-slot-id="usedAccordionRow" role="button">
          <div id="usedAccordionCaption_feature_div">Kullanılmış ve yeni gibi</div>
          <div id="corePrice_feature_div" data-csa-c-is-in-initial-active-row="true">
            <input name="items[0.base][customerVisiblePrice][amount]" value="149742.75">
            <span class="a-price"><span class="a-price-whole">149.742</span>
            <span class="a-price-fraction">75</span></span>
          </div><span>Gönderen: Amazon</span><span>Satıcı: Amazon Depo</span>
        </div>
        <div id="corePriceDisplay_desktop_feature_div">
          <span class="a-price"><span class="a-offscreen">168.249,00 TL</span></span>
        </div>'''
        offers = extract_amazon_offers(html, "https://www.amazon.com.tr/dp/B0FQF9XY3L")
        self.assertEqual([(o.price, o.is_warehouse) for o in offers], [
            (Decimal("168249"), False), (Decimal("149742.75"), True)])

    def test_amazon_walks_color_capacity_graph_and_yields_depot_immediately(self):
        watch = WatchRule(name="iPhone", site="amazon", url="https://www.amazon.com.tr/dp/B000000001",
                          target_price=Decimal("100000"), include_variations=True)
        config = SimpleNamespace(request_timeout_seconds=20)
        fetched = []

        def page_for(_session, url, _timeout):
            asin = service.extract_asin_from_url(url)
            fetched.append(asin)
            index = int(asin[-1]) - 1
            color, capacity = divmod(index, 3)
            neighbors = {color * 3 + n for n in range(3)} | {n * 3 + capacity for n in range(3)}
            swatches = ''.join(f'<li data-asin="B00000000{n+1}" class="swatchUnavailable">Option {n}</li>'
                               for n in sorted(neighbors))
            return f'''<span id="productTitle">iPhone color {color} capacity {capacity}</span>
                <div id="variation_size_name"><ul>{swatches}</ul></div>
                <div id="corePriceDisplay_desktop_feature_div">
                <span class="a-price"><span class="a-offscreen">{120000+index},00 TL</span></span></div>
                <div id="usedBuySection">Kullanılmış ve yeni gibi Satıcı: Amazon Depo
                <span class="a-price"><span class="a-offscreen">{90000+index},87 TL</span></span></div>'''

        with (patch.object(service, "fetch_amazon_page", side_effect=page_for),
              patch.object(service, "cleaned_html", side_effect=lambda r:r),
              patch.object(service, "wait_before_request"),
              patch.object(service.amazon_provider, "extract_product_variations",
                           wraps=service.amazon_provider.extract_product_variations) as discover):
            stream = service._iter_amazon_product_watch_offers(SimpleNamespace(), watch, config)
            first = next(stream)
            self.assertTrue(first.is_warehouse)
            self.assertEqual(fetched, ["B000000001"])
            offers = [first, *stream]
        self.assertEqual(len(fetched), 9)
        self.assertEqual(discover.call_count, 9)
        self.assertEqual(len(set(fetched)), 9)
        self.assertEqual(len(offers), 18)
        for offer in offers:
            index = int(service.extract_asin_from_url(offer.url)[-1]) - 1
            expected = Decimal(90000 + index) + Decimal(".87") if offer.is_warehouse else Decimal(120000 + index)
            self.assertEqual(offer.price, expected)

    def test_amazon_exclusions_skip_offer_read_but_still_expand_and_can_upgrade_cache(self):
        url = "https://www.amazon.com.tr/dp/B000000001"
        urls = [f"https://www.amazon.com.tr/dp/B00000000{number}" for number in range(1, 4)]
        labels = {urls[0]: "256 GB", urls[1]: "1 TB", urls[2]: "512 GB"}
        variations = [service.amazon_provider.AmazonProductVariation(labels[item], item) for item in urls]
        filtered_watch = WatchRule(
            name="iPhone", site="amazon", url=url, target_price=Decimal("100000"),
            include_variations=True, excluded_terms=["1 TB"],
        )
        unfiltered_watch = WatchRule(
            name="iPhone full", site="amazon", url=url, target_price=Decimal("100000"),
            include_variations=True,
        )
        config = SimpleNamespace(request_timeout_seconds=20)
        session = SimpleNamespace()
        fetched = []

        def page_for(_session, page_url, _timeout):
            fetched.append(page_url)
            return f'<span id="productTitle">Apple iPhone {labels[page_url]}</span>'

        def offers_for(_session, page_url, _html, _config, soup=None):
            return [service.SearchResultItem(f"Apple iPhone {labels[page_url]}", page_url, Decimal("90000"))]

        with (
            patch.object(service, "fetch_amazon_page", side_effect=page_for),
            patch.object(service, "cleaned_html", side_effect=lambda response: response),
            patch.object(service, "wait_before_request"),
            patch.object(service.amazon_provider, "extract_product_variations", return_value=variations) as discover,
            patch.object(service, "_extract_amazon_page_offers", side_effect=offers_for) as offer_reader,
            patch.object(service, "log") as event_log,
        ):
            filtered_offers = list(service._iter_amazon_product_watch_offers(session, filtered_watch, config))
            self.assertEqual([offer.url for offer in filtered_offers], [urls[0], urls[2]])
            self.assertEqual(len(fetched), 3)
            self.assertEqual(offer_reader.call_count, 2)

            repeated_filtered_offers = list(
                service._iter_amazon_product_watch_offers(session, filtered_watch, config)
            )
            self.assertEqual([offer.url for offer in repeated_filtered_offers], [urls[0], urls[2]])
            self.assertEqual(len(fetched), 3)

            unfiltered_offers = list(service._iter_amazon_product_watch_offers(session, unfiltered_watch, config))

        self.assertEqual([offer.url for offer in unfiltered_offers], urls)
        self.assertEqual(fetched, [*urls, urls[1]])
        self.assertEqual(offer_reader.call_count, 3)
        self.assertEqual(discover.call_count, 4)
        self.assertTrue(any(
            "Amazon varyant fiyat okuması hariç tutuldu" in call.args[0]
            and "hariç tut filtresi: 1 TB" in call.args[0]
            for call in event_log.call_args_list
        ))
        summaries = [
            call.args[0] for call in event_log.call_args_list
            if call.args[0].startswith("Amazon varyasyon taraması:")
        ]
        self.assertIn("hariç_nedeniyle_fiyat_okuması_atlandı=1", summaries[0])

    def test_amazon_inspects_every_variant_page_even_with_collapsed_family_list(self):
        watch = WatchRule(
            name="iPhone", site="amazon", url="https://www.amazon.com.tr/dp/B000000001",
            target_price=Decimal("100000"), include_variations=True,
        )
        config = SimpleNamespace(request_timeout_seconds=20)
        urls = [f"https://www.amazon.com.tr/dp/B00000000{number}" for number in range(1, 4)]
        variations = [service.amazon_provider.AmazonProductVariation(str(number), url)
                      for number, url in enumerate(urls)]
        html = '''<script type="a-state"
        data-a-state='{"key":"twister-plus-desktop-inline-twister-collapse-view-asins-data"}'>
        {"asinsInCollapsedView":["B000000002","B000000003"]}</script>'''
        session = SimpleNamespace()
        fetched = []

        def page_for(_session, url, _timeout):
            fetched.append(url)
            return html

        def offer_for(_session, url, _html, _config, soup=None):
            return [service.SearchResultItem("iPhone", url, Decimal("90000"))]

        with (
            patch.object(service, "fetch_amazon_page", side_effect=page_for),
            patch.object(service, "cleaned_html", return_value=html),
            patch.object(service, "wait_before_request"),
            patch.object(service.amazon_provider, "extract_product_variations", return_value=variations) as discover,
            patch.object(service.amazon_provider, "selected_variation_label", return_value="Gümüş"),
            patch.object(service, "_extract_amazon_page_offers", side_effect=offer_for) as offers_reader,
            patch.object(service, "log") as scan_log,
        ):
            offers = list(service._iter_amazon_product_watch_offers(session, watch, config))

        self.assertEqual(fetched, urls)
        self.assertEqual(discover.call_count, 3)
        self.assertEqual(offers_reader.call_count, 3)
        self.assertEqual([offer.url for offer in offers], urls)
        summary = next(
            call.args[0] for call in scan_log.call_args_list
            if call.args[0].startswith("Amazon varyasyon taraması:")
        )
        self.assertIn("varyant_keşif_sayfası=3", summary)

    def test_amazon_excluded_variants_are_refetched_each_cycle(self):
        watch = WatchRule(
            name="iPhone", site="amazon", url="https://www.amazon.com.tr/dp/B000000001",
            target_price=Decimal("100000"), include_variations=True, excluded_terms=["1 TB"],
        )
        config = SimpleNamespace(request_timeout_seconds=20)
        fetched = []
        variants = {"B000000001": "256 GB", "B000000002": "1 TB", "B000000003": "512 GB"}

        def page_for(_session, url, _timeout):
            asin = service.extract_asin_from_url(url)
            fetched.append(asin)
            swatches = "".join(
                f'<li data-asin="{item_asin}" class="swatchUnavailable">{label}</li>'
                for item_asin, label in variants.items()
            )
            return f'''<div id="variation_size_name"><ul>{swatches}</ul>
                <span class="selection">{variants[asin]}</span></div>
                <span id="productTitle">iPhone {variants[asin]}</span>
                <div id="corePriceDisplay_desktop_feature_div"><span class="a-price">
                  <span class="a-offscreen">100.000,00 TL</span></span></div>'''

        with (patch.object(service, "fetch_amazon_page", side_effect=page_for),
              patch.object(service, "cleaned_html", side_effect=lambda response: response),
              patch.object(service, "wait_before_request"),
              patch.object(service, "log") as timing_log,
              patch.object(service.amazon_provider, "soup_from_html",
                           wraps=service.amazon_provider.soup_from_html) as html_parser):
            for _ in range(2):
                offers = list(service._iter_amazon_product_watch_offers(SimpleNamespace(), watch, config))
                self.assertEqual(len(offers), 2)

        self.assertEqual(fetched, list(variants) * 2)
        self.assertEqual(html_parser.call_count, len(variants) * 2)
        phase_messages = [call.args[0] for call in timing_log.call_args_list
                          if "Amazon varyasyon aşama süreleri:" in call.args[0]]
        self.assertEqual(len(phase_messages), 6)
        self.assertIn("ayrıştırma=", phase_messages[0])
        self.assertIn("sonuç=", phase_messages[0])

    def test_amazon_product_page_parse_is_shared_between_watches_in_one_cycle(self):
        url = "https://www.amazon.com.tr/dp/B000000001"
        watches = [
            WatchRule("iPhone", "amazon", url, Decimal("100000"), include_variations=True),
            WatchRule("Telefon fırsatı", "amazon", url, Decimal("95000"), include_variations=True),
        ]
        config = SimpleNamespace(request_timeout_seconds=20)
        session = SimpleNamespace()
        offer = service.SearchResultItem(
            "iPhone Gümüş", url, Decimal("90000"), is_warehouse=True
        )

        with (
            patch.object(service, "fetch_amazon_page", return_value="html") as fetch,
            patch.object(service, "cleaned_html", return_value="html"),
            patch.object(service.amazon_provider, "parse_product_page", return_value=object()) as parse,
            patch.object(service.amazon_provider, "extract_product_variations", return_value=[]) as variations,
            patch.object(service.amazon_provider, "selected_variation_label", return_value="Gümüş"),
            patch.object(service.amazon_provider, "extract_title", return_value="iPhone"),
            patch.object(service, "_extract_amazon_page_offers", return_value=[offer]) as extract,
            patch.object(service, "wait_before_request"),
        ):
            results = [
                list(service._iter_amazon_product_watch_offers(session, watch, config))
                for watch in watches
            ]

        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(parse.call_count, 1)
        self.assertEqual(variations.call_count, 1)
        self.assertEqual(extract.call_count, 1)
        self.assertEqual([len(result) for result in results], [1, 1])
        metrics = session._hermes_amazon_cycle_metrics
        self.assertEqual(metrics["product_parse_cache_misses"], 1)
        self.assertEqual(metrics["product_parse_cache_hits"], 1)

    def test_amazon_search_skips_excluded_cards_before_detail_requests(self):
        watch = WatchRule(
            name="iPhone", site="amazon", url="https://www.amazon.com.tr/s?k=iphone",
            target_price=Decimal("100000"), excluded_terms=["1 TB"],
        )
        candidates = [
            AmazonSearchCandidate("iPhone 1 TB", "https://www.amazon.com.tr/dp/B000000001", Decimal("100000")),
            AmazonSearchCandidate("iPhone 256 GB", "https://www.amazon.com.tr/dp/B000000002", Decimal("90000")),
        ]
        config = SimpleNamespace(request_timeout_seconds=20, watches=[watch])
        with (patch.object(service, "fetch_amazon_page", return_value="html"),
              patch.object(service, "cleaned_html", return_value="amazon search result"),
              patch.object(service, "extract_result_candidates", return_value=candidates),
              patch.object(service, "_fetch_amazon_detail_offers", return_value=[]) as detail_fetch):
            offers = service._fetch_amazon_search_watch_offers(object(), watch, config)

        self.assertEqual([offer.title for offer in offers], ["iPhone 256 GB"])
        self.assertEqual(detail_fetch.call_count, 1)
        self.assertEqual(detail_fetch.call_args.args[1].title, "iPhone 256 GB")

    def test_amazon_search_keeps_read_card_but_stops_details_on_captcha(self):
        watch = WatchRule("iPhone", "amazon", "https://www.amazon.com.tr/s?k=iphone", Decimal("100000"))
        candidates = [
            AmazonSearchCandidate("iPhone 256 GB", "https://www.amazon.com.tr/dp/B000000001", Decimal("90000")),
            AmazonSearchCandidate("iPhone 512 GB", "https://www.amazon.com.tr/dp/B000000002", Decimal("120000")),
        ]
        session = SimpleNamespace()
        config = SimpleNamespace(request_timeout_seconds=20, watches=[watch])
        with (patch.object(service, "fetch_amazon_page", return_value="html"),
              patch.object(service, "cleaned_html", return_value="amazon search result"),
              patch.object(service, "extract_result_candidates", return_value=candidates),
              patch.object(service, "_fetch_amazon_detail_offers", side_effect=HermesError("Amazon captcha")) as detail):
            offers = service._fetch_amazon_search_watch_offers(session, watch, config)
        self.assertEqual([offer.title for offer in offers], ["iPhone 256 GB"])
        detail.assert_called_once()
        self.assertTrue(service.is_amazon_protection_error(session._hermes_amazon_protection_error))

    def test_amazon_modern_family_asins_are_deduplicated_and_recommendations_ignored(self):
        html = '''<script type="a-state" data-a-state='{"key":"twister-plus-desktop-inline-twister-collapse-view-asins-data"}'>
        {"asinsInCollapsedView":["B000000001","B000000002","B000000002"]}</script>
        <div id="inline-twister-row-color_name"><li data-asin="B000000002"><img alt="Abis"></li></div>
        <div id="recommendations"><a href="/dp/B000000003">iPhone 18</a></div>'''
        variants = extract_product_variations(html, "https://www.amazon.com.tr/dp/B000000001?th=1", 60)
        self.assertEqual([service.extract_asin_from_url(v.url) for v in variants], ["B000000001", "B000000002"])

    def test_amazon_depot_only_page_does_not_require_a_new_offer(self):
        html = '''<span id="productTitle">iPhone</span><div id="usedBuySection">
        Kullanılmış ve yeni gibi Satıcı: Amazon Depo
        <span class="a-price"><span class="a-offscreen">89.040,87 TL</span></span></div>'''
        offers = extract_amazon_offers(html, "https://www.amazon.com.tr/dp/B000000001")
        self.assertEqual(len(offers), 1)
        self.assertTrue(offers[0].is_warehouse)

    def test_amazon_used_like_new_text_can_open_used_listing(self):
        url = "https://www.amazon.com.tr/dp/B000000001"
        html = '<div id="usedBuySection">Kullanılmış ve yeni gibi</div>'
        self.assertIn("condition=used", extract_used_offer_listing_url(html, url))

    def test_amazon_inline_depot_keeps_its_own_price_and_condition(self):
        html = '''<span id="productTitle">iPhone 17 Pro Max Gümüş 256 GB</span>
        <div id="corePriceDisplay_desktop_feature_div">
          <span class="a-price"><span class="a-offscreen">123.058,99 TL</span></span>
        </div>
        <div id="usedBuySection">Kullanılmış ve yeni gibi
          <span class="a-price"><span class="a-price-whole">89.040</span>
          <span class="a-price-fraction">87</span></span>
          Gönderen: Amazon Satıcı: Amazon Depo
        </div>'''
        offers = extract_amazon_offers(html, "https://www.amazon.com.tr/dp/B000000001")
        self.assertEqual([(o.price, o.is_warehouse) for o in offers], [
            (Decimal("123058.99"), False), (Decimal("89040.87"), True)])
        self.assertFalse(any(o.is_warehouse for o in extract_amazon_offers(
            html.replace("Satıcı: Amazon Depo", "Satıcı: Başka Satıcı"))))

    def test_amazon_variation_dimensions_include_capacity_and_unavailable_asins(self):
        html = '''<script type="a-state" data-a-state='{"key":"desktop-twister-sort-filter-data"}'>
        {"sortedDimValuesForAllDims":{
          "color_name":[{"defaultAsin":"B000000002","dimensionValueState":"AVAILABLE",
            "dimensionValueDisplayText":"Abis"}],
          "size_name":[{"defaultAsin":"B000000003","dimensionValueState":"UNAVAILABLE",
            "dimensionValueDisplayText":"512 GB"}]}}
        </script>'''
        variants = service.amazon_provider.extract_product_variations(
            html, "https://www.amazon.com.tr/dp/B000000001", 60)
        self.assertEqual({v.url for v in variants}, {
            "https://www.amazon.com.tr/dp/B000000001",
            "https://www.amazon.com.tr/dp/B000000002",
            "https://www.amazon.com.tr/dp/B000000003"})

    def test_bengurme_fetch_prefers_shopify_variant_json(self):
        class Response:
            status_code = 200
            headers = {"content-type": "application/json"}
            encoding = "utf-8"
            text = '{"title":"Kilis Karası Kan Üzümü","variants":[]}'
            content = text.encode("utf-8")

            def raise_for_status(self):
                return None

        class Session:
            def __init__(self):
                self.calls = []

            def get(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return Response()

        session = Session()
        response = fetch_bengurme_page(session, "https://bengurme.com/products/kilis-karasi-kan-uzumu", 10)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(session.calls[0][0], "https://bengurme.com/products/kilis-karasi-kan-uzumu.js")
        self.assertIn("Chrome/124", session.calls[0][1]["headers"]["User-Agent"])

    def test_bengurme_url_is_detected(self):
        self.assertEqual(
            detect_site_from_url("https://bengurme.com/products/kilis-karasi-kan-uzumu"),
            "bengurme",
        )

    def test_bengurme_reads_each_available_gram_variant(self):
        payload = {
            "title": "Kilis Karası Kan Üzümü",
            "variants": [
                {"title": "250 gram", "price": 27500, "available": True},
                {"title": "500 gram", "price": 45000, "available": True},
                {"title": "1000 gram", "price": 95000, "available": True},
                {"title": "2000 gram", "price": 180000, "available": False},
            ],
        }

        offers = extract_bengurme_offers(json.dumps(payload), "https://bengurme.com/products/uzum")

        self.assertEqual(
            [(offer.title, offer.price) for offer in offers],
            [
                ("Kilis Karası Kan Üzümü / 250 gram", Decimal("275")),
                ("Kilis Karası Kan Üzümü / 500 gram", Decimal("450")),
                ("Kilis Karası Kan Üzümü / 1000 gram", Decimal("950")),
            ],
        )

    def test_bengurme_unavailable_product_is_stock_state_not_page_error(self):
        payload = {
            "title": "Taş Kırma Çekirdeksiz Yeşil Zeytin",
            "variants": [{"title": "1 kg", "price": 69500, "available": False}],
        }

        with self.assertRaisesRegex(OutOfStockHermesError, "Ben Gurme ürünü stokta değil"):
            extract_bengurme_offers(json.dumps(payload), "https://bengurme.com/products/zeytin")

    def test_bengurme_requested_variant_matching_is_case_insensitive(self):
        payload = {
            "title": "Kilis Karası Kan Üzümü",
            "variants": [
                {"title": "500 gram", "price": 45000, "available": True},
                {"title": "1000 gram", "price": 95000, "available": True},
            ],
        }

        offers = extract_bengurme_offers(json.dumps(payload), size="500 GRAM")

        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].price, Decimal("450"))

    def test_beymenclub_fetch_uses_its_browser_shaped_headers(self):
        class Response:
            status_code = 200
            headers = {"content-type": "text/html; charset=utf-8"}
            text = "<script>BEYMEN.productMain = {}</script>"
            content = text.encode("utf-8")

            def raise_for_status(self):
                return None

        class Session:
            def __init__(self):
                self.calls = []

            def get(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return Response()

        session = Session()
        response = fetch_beymenclub_page(session, "https://www.beymenclub.com/tr/p_test", 10)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(session.calls), 1)
        headers = session.calls[0][1]["headers"]
        self.assertIn("Chrome/124", headers["User-Agent"])
        self.assertEqual(headers["Accept-Language"], "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7")

    def test_beymenclub_size_summary_uses_browser_session_headers(self):
        class Response:
            status_code = 200
            headers = {"content-type": "application/json"}

            def raise_for_status(self):
                return None

            def json(self):
                return {"result": {"sizes": [{"sizeName": "XL", "inStock": True}]}}

        class Session:
            def __init__(self):
                self.calls = []

            def post(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return Response()

        session = Session()
        payload = fetch_beymenclub_size_summary(
            session,
            "https://www.beymenclub.com/tr/p_test",
            1941298,
            10,
        )

        self.assertEqual(payload["result"]["sizes"][0]["sizeName"], "XL")
        request_url, kwargs = session.calls[0]
        self.assertEqual(request_url, "https://www.beymenclub.com/sf-api/api/product/1941298/productsummary")
        self.assertEqual(kwargs["headers"]["Origin"], "https://www.beymenclub.com")
        self.assertEqual(kwargs["headers"]["Sec-Fetch-Site"], "same-origin")

    def test_network_prefers_two_or_more_basket_price(self):
        html = """
        <html><head><title>Vizon Mini Elbise</title></head><body>
          <h1>Vizon Mini Elbise</h1>
          <div class="product-detail__price">8.499,00 TL</div>
          <div class="basket-campaign">2 ve üzeri 4.999,50 TL</div>
        </body></html>
        """

        offer = extract_network_offer(html)

        self.assertEqual(offer.title, "Vizon Mini Elbise")
        self.assertEqual(offer.price, Decimal("4999.50"))

    def test_network_prefers_three_or_more_basket_price(self):
        html = """
        <html><head><title>Örnek Network Ürünü</title></head><body>
          <h1>Örnek Network Ürünü</h1>
          <div class="product-detail__price">8.499,00 TL</div>
          <div class="basket-campaign">3 ve üzeri için 4.999,50 TL</div>
        </body></html>
        """

        self.assertEqual(extract_network_offer(html).price, Decimal("4999.50"))

    def test_beymenclub_prefers_sepette_price_without_decimal_cents(self):
        html = """
        <html><head><title>Beymen Club Bej Polo Yaka Triko</title></head><body>
          <h1>Beymen Club Bej Polo Yaka Triko</h1>
          <div class="product-price">5.495 TL</div>
          <div class="basket-campaign">Sepette 3.475 TL</div>
        </body></html>
        """

        offer = extract_beymenclub_offer(html)

        self.assertEqual(offer.title, "Beymen Club Bej Polo Yaka Triko")
        self.assertEqual(offer.price, Decimal("3475"))

    def test_beymenclub_prefers_multi_item_basket_price(self):
        html = """
        <html><head><title>Beymen Club Kırık Beyaz Hırka</title></head><body>
          <h1>Beymen Club Kırık Beyaz Hırka</h1>
          <div class="product-price">5.995 TL</div>
          <div class="basket-campaign">2 ve üzeri 4.475 TL</div>
        </body></html>
        """

        self.assertEqual(extract_beymenclub_offer(html).price, Decimal("4475"))

    def test_network_size_matching_is_case_insensitive(self):
        html = """
        <html><head><title>Network Gömlek</title></head><body>
          <div class="product-price">2.500 TL</div>
          <script>
            var product = {"DisplayName":"Network Gömlek","Sizes":[
              {"ValueText":"XS","NoStock":false},
              {"ValueText":"XL","NoStock":false}
            ]};
            var productModel = {};
          </script>
        </body></html>
        """

        offers = extract_network_offers(html, source_url="https://network.com.tr/urun", size="xl")

        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].price, Decimal("2500"))

    def test_network_unavailable_size_is_not_a_page_error(self):
        html = """
        <html><head><title>Network Gömlek</title></head><body>
          <div class="product-price">2.500 TL</div>
          <script>
            var product = {"Sizes":[{"ValueText":"XL","NoStock":true}]};
            var productModel = {};
          </script>
        </body></html>
        """

        with self.assertRaisesRegex(OutOfStockHermesError, "Network beden stokta değil: xl"):
            extract_network_offers(html, size="xl")

    def test_network_reads_authoritative_sizes_payload(self):
        html = """
        <script>
          var product = {
            "Sizes": [
              {"ValueText":"XS","NoStock":false},
              {"ValueText":"S","NoStock":false},
              {"ValueText":"M","NoStock":false},
              {"ValueText":"L","NoStock":true},
              {"ValueText":"XL","NoStock":true}
            ]
          };
          var productModel = {};
        </script>
        """

        self.assertEqual(_network_requested_size_state(html, "xs"), (True, True))
        self.assertEqual(_network_requested_size_state(html, "M"), (True, True))
        self.assertEqual(_network_requested_size_state(html, "l"), (True, False))
        self.assertEqual(_network_requested_size_state(html, "XL"), (True, False))

    def test_beymenclub_size_matching_ignores_parenthetical_size_labels(self):
        html = """
        <html><head><title>Beymen Club Hırka</title></head><body>
          <div class="product-price">4.475 TL</div>
          <select name="beden"><option value="XL (EU XL)">XL (EU XL)</option></select>
        </body></html>
        """

        offers = extract_beymenclub_offers(html, size="xl")

        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].price, Decimal("4475"))

    def test_beymenclub_missing_size_is_not_a_page_error(self):
        html = """
        <html><head><title>Beymen Club Hırka</title></head><body>
          <div class="product-price">4.475 TL</div>
          <select name="beden"><option value="M">M</option></select>
        </body></html>
        """

        with self.assertRaisesRegex(OutOfStockHermesError, "Beymen Club beden bulunamadı: XL"):
            extract_beymenclub_offers(html, size="XL")

    def test_beymenclub_reads_authoritative_size_summary(self):
        summary = {
            "result": {
                "sizes": [
                    {"sizeName": "S", "inStock": True, "stockQuantity": 27},
                    {"sizeName": "XL", "inStock": True, "stockQuantity": 6},
                    {"sizeName": "XXL", "inStock": False, "stockQuantity": 0},
                ]
            }
        }

        self.assertEqual(requested_size_state_from_summary(summary, "s"), (True, True))
        self.assertEqual(requested_size_state_from_summary(summary, "XL"), (True, True))
        self.assertEqual(requested_size_state_from_summary(summary, "xxl"), (True, False))

    def test_beymenclub_extracts_product_id_from_page_payload(self):
        html = '<script>BEYMEN.productMain = {"productId":1941303,"displayName":"Polo"};</script>'

        self.assertEqual(extract_beymenclub_product_id(html), 1941303)

    def test_amazon_variations_are_opt_in_for_each_watch(self):
        base_watch = {
            "name": "Tablet",
            "target_price": 20000,
            "url_1": "https://www.amazon.com.tr/dp/B000000001",
        }
        default_watch = _prepare_watches([base_watch])[0]
        opted_in_watch = _prepare_watches([{**base_watch, "include_variations": True}])[0]

        self.assertFalse(default_watch.include_variations)
        self.assertTrue(opted_in_watch.include_variations)

    def test_amazon_warehouse_offers_are_always_kept_for_normal_links(self):
        watch = WatchRule(
            name="Edifier M60",
            site="amazon",
            url="https://www.amazon.com.tr/dp/B0D95QG8W4?th=1",
            target_price=Decimal("9000"),
        )
        offers = [
            OfferResult("Edifier M60", Decimal("8899"), url=watch.url),
            OfferResult("Edifier M60", Decimal("8787.77"), url=watch.url, is_warehouse=True),
        ]
        with patch.object(service, "_fetch_amazon_product_watch_offers", return_value=offers):
            result = service._fetch_watch_offers(object(), watch, SimpleNamespace())

        self.assertEqual([offer.is_warehouse for offer in result], [False, True])

    def test_amazon_warehouse_search_keeps_used_results_without_opt_in(self):
        watch = WatchRule(
            name="Edifier M60",
            site="amazon",
            url="https://www.amazon.com.tr/s?k=edifier+m60&i=warehouse-deals",
            target_price=Decimal("9000"),
        )
        offers = [OfferResult("Edifier M60", Decimal("8787.77"), url=watch.url, is_warehouse=True)]
        with patch.object(service, "_fetch_amazon_search_watch_offers", return_value=offers):
            result = service._fetch_watch_offers(object(), watch, SimpleNamespace())

        self.assertTrue(is_warehouse_search_url(watch.url))
        self.assertEqual([offer.is_warehouse for offer in result], [True])

    def test_amazon_search_deep_scan_always_adds_verified_used_price(self):
        search_url = "https://www.amazon.com.tr/s?k=edifier+m60"
        search_html = """
        <div class="s-main-slot">
          <div data-component-type="s-search-result" data-asin="B0D95QG8W4">
            <h2><a href="/dp/B0D95QG8W4"><span>Edifier M60 Compact Masa Hoparlörü - Siyah</span></a></h2>
            <span class="a-price"><span class="a-offscreen">8.899,00 TL</span></span>
          </div>
        </div>
        """
        detail_html = """
        <html><head><title>Edifier M60 Compact Masa Hoparlörü - Siyah</title></head><body>
          <div id="corePriceDisplay_desktop_feature_div">
            <span class="a-price"><span class="a-offscreen">8.899,00 TL</span></span>
          </div>
          <a href="/gp/offer-listing/B0D95QG8W4?condition=used">Yeni & İkinci El Ürün</a>
        </body></html>
        """
        used_listing_html = """
        <html><head><title>Edifier M60 Compact Masa Hoparlörü - Siyah</title></head><body>
          <div class="aod-offer">
            <span>İkinci El - Çok İyi</span><a>Amazon Depo</a>
            <span class="a-price"><span class="a-offscreen">8.787,77 TL</span></span>
          </div>
        </body></html>
        """
        watch = WatchRule(
            name="Edifier M60",
            site="amazon",
            url=search_url,
            target_price=Decimal("9000"),
        )
        config = SimpleNamespace(request_timeout_seconds=20, request_delay_min_seconds=0, request_delay_max_seconds=0)

        def page_for_url(_session, url, _timeout, **_kwargs):
            if url == search_url:
                return search_html
            if "offer-listing" in url:
                return used_listing_html
            return detail_html

        with (
            patch.object(service, "fetch_amazon_page", side_effect=page_for_url),
            patch.object(service, "cleaned_html", side_effect=lambda value: value),
            patch.object(service, "wait_before_request"),
        ):
            offers = service._fetch_amazon_search_watch_offers(SimpleNamespace(), watch, config)

        self.assertEqual([offer.price for offer in offers], [Decimal("8899.00"), Decimal("8787.77")])
        self.assertEqual([offer.is_warehouse for offer in offers], [False, True])

    def test_link_test_renders_provider_results_without_writing_tracking_state(self):
        offer = OfferResult(
            title="Örnek ürün / Mavi",
            price=Decimal("18999"),
            seller="Amazon",
            url="https://www.amazon.com.tr/dp/B000000001",
        )
        with patch.object(link_test_ui, "inspect_link_now", return_value=("amazon", [offer])) as inspect_link:
            payload = link_test_ui.render_link_test_from_request(
                "",
                "./link-test",
                "./",
                b"url=https%3A%2F%2Fwww.amazon.com.tr%2Fdp%2FB000000001&name=Ornek&size=XL&exclude_terms=kilif%2Ckoruyucu&include_variations=1",
            ).decode("utf-8")

        inspect_link.assert_called_once_with(
            "https://www.amazon.com.tr/dp/B000000001",
            name="Ornek",
            size="XL",
            include_variations=True,
            excluded_terms=["kilif", "koruyucu"],
        )
        self.assertIn("Test sonuçları", payload)
        self.assertIn("Örnek ürün / Mavi", payload)
        self.assertIn("18.999 TL", payload)
        self.assertIn("Geçici sonuçlar. Kayıt ve bildirim oluşturmaz.", payload)

    def test_amazon_product_color_variations_keep_concrete_urls_and_labels(self):
        html = """
        <div id="variation_color_name"><ul>
          <li data-defaultasin="B000000001"><a href="/dp/B000000001?th=1"><img alt="Renk: Antrasit"></a></li>
          <li data-defaultasin="B000000002"><a href="/dp/B000000002?psc=1"><img alt="Renk: Mavi"></a></li>
          <li class="swatchUnavailable" data-defaultasin="B000000003"><a href="/dp/B000000003"><img alt="Renk: Pembe"></a></li>
        </ul></div>
        """
        variations = extract_product_variations(html, "https://www.amazon.com.tr/dp/B000000001?th=1", 60)
        self.assertEqual(
            [(item.label, item.url) for item in variations],
            [
                ("Antrasit", "https://www.amazon.com.tr/dp/B000000001?th=1"),
                ("Mavi", "https://www.amazon.com.tr/dp/B000000002?psc=1"),
                ("Pembe", "https://www.amazon.com.tr/dp/B000000003"),
            ],
        )
        self.assertEqual(title_with_variation("Örnek ürün", "Mavi"), "Örnek ürün / Mavi")
        self.assertEqual(title_with_variation("Örnek ürün Mavi", "Mavi"), "Örnek ürün Mavi")

    def test_amazon_product_color_variations_read_modern_twister_state(self):
        html = '''
        <script type="a-state" data-a-state='{"key":"desktop-twister-sort-filter-data"}'>
        {"sortedDimValuesForAllDims":{"color_name":[
          {"defaultAsin":"B000000001","dimensionValueState":"SELECTED","dimensionValueDisplayText":"ANTRASİT"},
          {"defaultAsin":"B000000002","dimensionValueState":"AVAILABLE","dimensionValueDisplayText":"BEYAZ","pageLoadURL":"/dp/B000000002/ref=twister?psc=1"},
          {"defaultAsin":"B000000003","dimensionValueState":"UNAVAILABLE","dimensionValueDisplayText":"PEMBE","pageLoadURL":"/dp/B000000003?psc=1"}
        ]}}
        </script>
        '''
        variations = extract_product_variations(
            html,
            "https://www.amazon.com.tr/dp/B000000001?smid=A1&th=1",
            60,
        )
        self.assertEqual(
            [(item.label, item.url) for item in variations],
            [
                ("ANTRASİT", "https://www.amazon.com.tr/dp/B000000001?smid=A1&th=1"),
                ("BEYAZ", "https://www.amazon.com.tr/dp/B000000002?psc=1"),
                ("PEMBE", "https://www.amazon.com.tr/dp/B000000003?psc=1"),
            ],
        )

    def test_real_amazon_watch_reads_all_enabled_color_variations(self):
        """The saved watch path must use the same variation behavior as Link Test."""
        watch = WatchRule(
            name="Tablet",
            site="amazon",
            url="https://www.amazon.com.tr/dp/B000000001?th=1",
            target_price=Decimal("20000"),
            include_variations=True,
        )
        config = SimpleNamespace(request_timeout_seconds=20)
        source_url = watch.url
        variation_urls = [
            source_url,
            "https://www.amazon.com.tr/dp/B000000002?psc=1",
            "https://www.amazon.com.tr/dp/B000000003?psc=1",
        ]
        variations = [
            SimpleNamespace(label="Antrasit", url=variation_urls[0]),
            SimpleNamespace(label="Mavi", url=variation_urls[1]),
            SimpleNamespace(label="Pembe", url=variation_urls[2]),
        ]

        def offers_for_url(html, source_url, soup=None):
            return [
                OfferResult(
                    title="Örnek tablet",
                    price=Decimal("18999"),
                    seller="Amazon",
                    url=source_url,
                )
            ]

        with (
            patch.object(service, "fetch_amazon_page", side_effect=lambda _session, url, _timeout: url) as fetch_page,
            patch.object(service, "cleaned_html", side_effect=lambda value: value),
            patch.object(service, "wait_before_request"),
            patch.object(service.amazon_provider, "extract_product_variations", return_value=variations),
            patch.object(service.amazon_provider, "extract_offers", side_effect=offers_for_url),
        ):
            offers = service._fetch_amazon_product_watch_offers(SimpleNamespace(), watch, config)

        self.assertEqual([offer.url for offer in offers], variation_urls)
        self.assertEqual([offer.title for offer in offers], [
            "Örnek tablet / Antrasit",
            "Örnek tablet / Mavi",
            "Örnek tablet / Pembe",
        ])
        self.assertEqual(fetch_page.call_args_list[0].args[1], source_url)
        self.assertEqual(fetch_page.call_count, 3)

    def test_amazon_variation_watch_rows_are_grouped_above_the_target(self):
        watch = WatchRule(
            name="Tablet",
            site="amazon",
            url="https://www.amazon.com.tr/dp/B000000001?th=1",
            target_price=Decimal("20000"),
            include_variations=True,
        )

        group, label = service.search_result_group_for_watch(watch)

        self.assertTrue(group)
        self.assertEqual(label, "Tablet")

    def test_updating_a_watch_keeps_the_variation_setting_for_the_real_check_loop(self):
        existing_options = {
            "takip_edilenler": [
                {
                    "name": "Tablet",
                    "target_price": 20000,
                    "url_1": "https://www.amazon.com.tr/dp/B000000001?th=1",
                    "include_variations": False,
                    "notify_once_in_24H": True,
                    "active": True,
                }
            ]
        }
        form = {
            "operation": ["update_watch"],
            "watch_index": ["0"],
            "update_watch_index": ["0"],
            "watches_0_name": ["Tablet"],
            "watches_0_target_price": ["20000"],
            "watches_0_url_1": ["https://www.amazon.com.tr/dp/B000000001?th=1"],
            "watches_0_include_variations": ["on"],
            "watches_0_notify_once_in_24H": ["on"],
            "watches_0_active": ["on"],
        }

        updated_options, _message = settings_ui._apply_settings_operation(existing_options, form)
        watches = _prepare_watches(updated_options["takip_edilenler"])

        self.assertEqual(len(watches), 1)
        self.assertTrue(watches[0].include_variations)
    def test_telegram_quick_add_extracts_a_supported_product_url(self):
        message = "Buna bakar mısın? https://www.amazon.com.tr/dp/B0B2PSDNV1?th=1"
        self.assertEqual(
            telegram_listener._extract_supported_url(message),
            "https://www.amazon.com.tr/dp/B0B2PSDNV1?th=1",
        )

    def test_telegram_quick_add_resolves_mobile_share_link(self):
        class ShortLinkResponse:
            url = "https://www.amazon.com.tr/dp/B0B2PSDNV1?th=1"

            def close(self):
                return None

        with patch.object(telegram_listener.requests, "get", return_value=ShortLinkResponse()):
            self.assertEqual(
                telegram_listener._extract_supported_url("https://amzn.eu/d/example"),
                "https://www.amazon.com.tr/dp/B0B2PSDNV1?th=1",
            )

    def test_telegram_quick_add_target_price_accepts_turkish_price_formats(self):
        self.assertEqual(telegram_listener._parse_target_price("40.000 TL"), Decimal("40000"))
        self.assertEqual(telegram_listener._parse_target_price("40000"), Decimal("40000"))
        self.assertIsNone(telegram_listener._parse_target_price("fiyat belli değil"))

    def test_telegram_quick_add_uses_shared_group_and_search_query_name(self):
        options = {"gruplar": ["Teknoloji"], "takip_edilenler": []}
        url = "https://www.amazon.com.tr/s?k=edifier+m60"
        with patch.object(telegram_listener, "load_json", return_value=options), patch.object(
            telegram_listener, "save_options_and_restart"
        ) as save_options:
            result = telegram_listener._quick_add_watch(url, Decimal("8700"))

        self.assertEqual(result, "Takip kaydı eklendi")
        saved_options = save_options.call_args.args[0]
        self.assertIn("Paylaşılanlar", saved_options["gruplar"])
        self.assertEqual(
            saved_options["takip_edilenler"][0],
            {
                "name": "edifier m60",
                "group": "Paylaşılanlar",
                "target_price": 8700.0,
                "url_1": url,
                "notify_once_in_24H": True,
                "active": True,
            },
        )

    def test_ingress_and_public_settings_share_the_same_save_handler(self):
        self.assertIs(dashboard_with_settings.handle_settings_save, settings_ui.handle_settings_save)
        self.assertIs(public_dashboard.handle_settings_save, settings_ui.handle_settings_save)

    def test_ingress_dashboard_uses_the_shared_public_layout_renderer(self):
        with patch.object(dashboard, "_render_dashboard_page", return_value=b"same-layout") as render_page:
            payload = dashboard._render_page("/", error_detail_limit=None)

        self.assertEqual(payload, b"same-layout")
        render_page.assert_called_once_with("/", ".", None)

    def test_dashboard_site_theme_classes_are_distinct_for_supported_providers(self):
        expected = {
            "Amazon": "site-amazon",
            "Hepsiburada": "site-hepsiburada",
            "Trendyol": "site-trendyol",
            "Network": "site-network",
            "Beymen Club": "site-beymenclub",
            "Nordbron": "site-nordbron",
            "Zara": "site-zara",
            "H&M": "site-hm",
        }
        self.assertEqual(
            {seller: dashboard._site_theme_class(seller) for seller in expected},
            expected,
        )

    def test_dashboard_summary_can_show_all_recent_errors_without_global_override(self):
        options = {"interval_seconds": 60, "takip_edilenler": []}
        state = {
            "first": {
                "site": "amazon",
                "last_checked_at": utc_now(),
                "last_error": "İlk hata",
            },
            "second": {
                "site": "hepsiburada",
                "last_checked_at": utc_now(),
                "last_error": "İkinci hata",
            },
        }

        def fake_load_json(path, default):
            if path == dashboard.OPTIONS_PATH:
                return options
            if path == dashboard.STATE_PATH:
                return state
            return default

        with patch.object(dashboard, "load_json", side_effect=fake_load_json):
            summary = dashboard._collect_summary(error_detail_limit=None)

        self.assertEqual(summary["errors"], 2)
        self.assertEqual(len(summary["error_details"]), 2)

    def test_summary_drop_alert_requires_a_meaningful_product_loss(self):
        self.assertEqual(service.summary_drop_threshold(18), 6)
        self.assertEqual(service.summary_drop_threshold(23), 8)
        self.assertFalse(18 - 14 >= service.summary_drop_threshold(18))
        self.assertTrue(18 - 11 >= service.summary_drop_threshold(18))

    def test_watches_always_use_the_fixed_search_scan_limit(self):
        watches = _prepare_watches(
            [
                {
                    "name": "Juo Q3",
                    "target_price": 2000,
                    "url_1": "https://www.amazon.com.tr/s?k=juo+q3",
                    "max_items_to_scan": 24,
                }
            ]
        )

        self.assertEqual(len(watches), 1)
        self.assertEqual(watches[0].max_items_to_scan, 60)

    def test_unsupported_watch_url_is_skipped_without_stopping_valid_watches(self):
        with patch("hermes.config_loader.log") as mocked_log:
            watches = _prepare_watches(
                [
                    {
                        "name": "iPad",
                        "target_price": 40000,
                        "url_1": "https://www.amazon.com.tr/dp/B000000001",
                        "url_2": "https://amzn.eu/d/example",
                    }
                ]
            )

        self.assertEqual(len(watches), 1)
        self.assertEqual(watches[0].site, "amazon")
        self.assertIn("amzn.eu", mocked_log.call_args.args[0])

    def test_watch_filters_support_minimum_price_and_comma_separated_terms(self):
        watches = _prepare_watches(
            [
                {
                    "name": "Samsung S11",
                    "target_price": 40000,
                    "minimum_price": "10.000",
                    "exclude_terms": "kılıf, koruyucu, çizilmez, temperli",
                    "url_1": "https://www.amazon.com.tr/s?k=samsung+s11",
                }
            ]
        )

        self.assertEqual(watches[0].minimum_price, Decimal("10000"))
        self.assertEqual(watches[0].excluded_terms, ["kılıf", "koruyucu", "çizilmez", "temperli"])
        self.assertIn(
            "minimum fiyat filtresi",
            service.skipped_offer_reason(
                watches[0], OfferResult("Samsung S11", Decimal("1000")), "Samsung S11"
            ),
        )
        self.assertIn(
            "hariç tut filtresi: kılıf",
            service.skipped_offer_reason(
                watches[0], OfferResult("Samsung S11 koruyucu kılıf", Decimal("20000")), "Samsung S11 koruyucu kılıf"
            ),
        )
        self.assertEqual(
            service.skipped_offer_reason(
                watches[0], OfferResult("Samsung S11 tablet", Decimal("20000")), "Samsung S11 tablet"
            ),
            "",
        )

    def test_summary_drop_alert_requires_five_consecutive_cycles(self):
        meta = {}
        for expected_streak in range(1, service.SUMMARY_DROP_CONSECUTIVE_CYCLES + 1):
            streak = service.next_summary_drop_streak(meta, True)
            self.assertEqual(streak, expected_streak)
            meta["summary_drop_consecutive_cycles"] = streak

        self.assertEqual(service.next_summary_drop_streak(meta, False), 0)

    def test_empty_amazon_search_result_is_not_an_operational_error(self):
        self.assertTrue(
            service.is_normal_amazon_search_result_absence(
                HermesError("Amazon arama sayfasında ürün adına uyan fiyatlı ürün bulunamadı.")
            )
        )
        self.assertTrue(
            service.is_normal_amazon_search_result_absence(
                HermesError("Amazon arama sayfasında okunabilir fiyat bulunamadı.")
            )
        )
        self.assertFalse(
            service.is_normal_amazon_search_result_absence(
                HermesError("Amazon bot korumasi nedeniyle captcha sayfasi dondu.")
            )
        )

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

    def test_dashboard_orders_open_rows_by_seller_then_price_difference(self):
        rows = [
            {"seller": "Hepsiburada", "product_title": "Uzak", "difference": "+900,00"},
            {"seller": "Amazon", "product_title": "Orta", "difference": "+600,00"},
            {"seller": "Amazon", "product_title": "Yakın", "difference": "+100,00"},
            {"seller": "Hepsiburada", "product_title": "Yakın", "difference": "+150,00"},
            {
                "seller": "Amazon",
                "product_title": "Varyasyon A",
                "difference": "+50,00",
                "search_group": "amazon_juo_q3",
                "search_group_label": "Juo Q3",
            },
            {
                "seller": "Amazon",
                "product_title": "Varyasyon B",
                "difference": "+75,00",
                "search_group": "amazon_juo_q3",
                "search_group_label": "Juo Q3",
            },
        ]

        open_rows, collapsed_groups = dashboard._split_search_result_groups(rows)

        self.assertEqual(
            [row["product_title"] for row in open_rows],
            ["Yakın", "Orta", "Yakın", "Uzak"],
        )
        self.assertEqual([row["seller"] for row in open_rows], ["Amazon", "Amazon", "Hepsiburada", "Hepsiburada"])
        self.assertEqual(
            [row["product_title"] for row in collapsed_groups[0][1]],
            ["Varyasyon A", "Varyasyon B"],
        )

    def test_dashboard_groups_inferred_variants_and_merges_state_source_groups(self):
        rows = [
            {
                "seller": "H&M",
                "product_title": "Fitilli pantolon / Mavi / XL",
                "product_url": "https://example.test/pants?color=blue",
                "difference": "+100,00",
                "target": "1.500,00",
            },
            {
                "seller": "H&M",
                "product_title": "Fitilli pantolon / Siyah / XL",
                "product_url": "https://example.test/pants?color=black",
                "difference": "+200,00",
                "target": "1.500,00",
            },
            {
                "seller": "Amazon",
                "product_title": "Apple tablet / Mavi",
                "product_url": "https://www.amazon.com.tr/dp/B000000001?th=1",
                "difference": "+100,00",
                "search_group": "old-first-source",
                "search_group_label": "Apple tablet",
            },
            {
                "seller": "Amazon",
                "product_title": "Apple tablet / Mor",
                "product_url": "https://www.amazon.com.tr/gp/product/B000000002?psc=1",
                "difference": "+200,00",
                "search_group": "old-second-source",
                "search_group_label": "Apple tablet",
            },
            {
                "seller": "Amazon",
                "product_title": "Apple tablet / Mavi (aynı link)",
                "product_url": "https://www.amazon.com.tr/dp/B000000001?smid=A1",
                "difference": "+300,00",
                "search_group": "old-third-source",
                "search_group_label": "Apple tablet",
            },
        ]

        open_rows, collapsed_groups = dashboard._split_search_result_groups(rows)

        self.assertEqual(open_rows, [])
        self.assertEqual([label for label, _ in collapsed_groups], ["Apple tablet", "Fitilli pantolon"])
        self.assertEqual([len(group_rows) for _, group_rows in collapsed_groups], [2, 2])

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

        enriched = dashboard._attach_state_search_groups(rows, state)

        self.assertTrue(all(row["search_group"] for row in enriched))
        self.assertEqual([row["search_group_label"] for row in enriched], ["Juo Q3", "Juo Q3"])

    def test_dashboard_groups_blank_name_variation_watch_from_configured_source(self):
        rows = [
            {
                "seller": "Amazon",
                "product_url": "https://www.amazon.com.tr/dp/BLUE",
                "product_title": "Decanox Katlanır Kasa / Mavi",
                "difference": "+100,00",
                "target": "600,00",
            },
            {
                "seller": "Amazon",
                "product_url": "https://www.amazon.com.tr/dp/GREY",
                "product_title": "Decanox Katlanır Kasa / Gri",
                "difference": "+150,00",
                "target": "600,00",
            },
        ]
        source_url = "https://www.amazon.com.tr/dp/SOURCE?th=1"
        state = {
            "blue": {
                "site": "amazon",
                "configured_url": source_url,
                "url": "https://www.amazon.com.tr/dp/BLUE",
                "watch_name": "",
            },
            "grey": {
                "site": "amazon",
                "configured_url": source_url,
                "url": "https://www.amazon.com.tr/dp/GREY",
                "watch_name": "",
            },
        }
        options = {
            "takip_edilenler": [
                {"url_1": source_url, "include_variations": True},
            ]
        }

        enriched = dashboard._attach_state_search_groups(rows, state, options)
        open_rows, collapsed_groups = dashboard._split_search_result_groups(enriched)

        self.assertEqual(open_rows, [])
        self.assertEqual(len(collapsed_groups), 1)
        self.assertEqual(collapsed_groups[0][0], "Decanox Katlanır Kasa")
        self.assertEqual(len(collapsed_groups[0][1]), 2)

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
                self.assertEqual(page.count("data-settings-save"), 1)
                self.assertIn("Değişiklikleri uygula", page)
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
                self.assertIn("data-delete-watch", page)
                self.assertIn("Değişiklikleri uygula", page)
                self.assertNotIn("name='delete_watch_index'", page)
                self.assertNotIn("name='update_watch_index'", page)
                self.assertNotIn("name='watch_index'", page)
                self.assertNotIn("Güncellemeleri Kaydet", page)
                self.assertNotIn("Arama linklerinde taranacak maksimum ürün", page)
                self.assertIn("value='100'", page)
            finally:
                settings_ui.OPTIONS_PATH = original_options_path
                settings_ui.STATE_PATH = original_state_path
                settings_ui.SUMMARY_PATH = original_summary_path

    def test_all_watch_cards_use_the_compact_three_row_layout(self):
        new_html = settings_ui._watch_form({}, 0, is_new=True, groups=["Moda"])
        existing_html = settings_ui._watch_form(
            {
                "name": "Mevcut ürün",
                "group": "Teknoloji",
                "target_price": 1500,
                "url_1": "https://www.amazon.com.tr/dp/B000000001",
            },
            1,
            groups=["Moda", "Teknoloji"],
        )

        for html in (new_html, existing_html):
            self.assertIn("watch-layout", html)
            self.assertIn("watch-top", html)
            self.assertIn("watch-links", html)
            self.assertIn("watch-bottom", html)
            self.assertLess(html.index(">Grup<"), html.index(">Ad<"))
            self.assertLess(html.index(">Ad<"), html.index(">Hedef Fiyat Maks<"))
            self.assertLess(html.index(">Hedef Fiyat Maks<"), html.index(">Beden<"))
            for link_number in range(1, 6):
                self.assertIn(f">Link {link_number}<", html)

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

    def test_mobile_delete_operation_does_not_depend_on_submit_button_value(self):
        """Mobile Safari may omit a submit button after client-side visual locking."""
        options, message = settings_ui._apply_settings_operation(
            {
                "takip_edilenler": [
                    {"name": "Silinecek", "target_price": 100, "url_1": "https://www.amazon.com.tr/dp/B000000001"},
                    {"name": "Kalacak", "target_price": 200, "url_1": "https://www.amazon.com.tr/dp/B000000002"},
                ]
            },
            {"operation": ["delete_watch"], "watch_index": ["0"]},
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

    def test_batch_settings_save_applies_edits_deletes_and_new_watches(self):
        options, message = settings_ui._apply_settings_operation(
            {
                "takip_edilenler": [
                    {"name": "Silinecek", "target_price": 100, "url_1": "https://www.amazon.com.tr/dp/B000000001"},
                    {"name": "Kalacak", "group": "Diğer", "target_price": 200, "url_1": "https://www.amazon.com.tr/dp/B000000002"},
                ],
                "telegram_enabled": False,
                "channels": ["@firsatz"],
            },
            {
                "operation": ["update_existing"],
                "watches_count": ["3"],
                "watches_0_delete": ["1"],
                "watches_0_name": ["Silinecek"],
                "watches_0_target_price": ["100"],
                "watches_0_url_1": ["https://www.amazon.com.tr/dp/B000000001"],
                "watches_1_name": ["Kalacak"],
                "watches_1_group": ["Teknoloji"],
                "watches_1_target_price": ["250"],
                "watches_1_url_1": ["https://www.amazon.com.tr/dp/B000000002"],
                "watches_1_active": ["1"],
                "watches_2_name": ["Yeni kayıt"],
                "watches_2_target_price": ["1.500"],
                "watches_2_url_1": ["https://www.hepsiburada.com/ara?q=yeni"],
                "watches_2_active": ["1"],
                "telegram_enabled": ["1"],
                "channels": ["@firsatz"],
            },
        )

        self.assertEqual(message, "Ayarlar kaydedildi.")
        self.assertEqual([item["name"] for item in options["takip_edilenler"]], ["Kalacak", "Yeni kayıt"])
        self.assertEqual(options["takip_edilenler"][0]["group"], "Teknoloji")
        self.assertEqual(options["takip_edilenler"][0]["target_price"], 250)
        self.assertEqual(options["takip_edilenler"][1]["target_price"], 1500)
        self.assertTrue(options["telegram_enabled"])

    def test_card_update_uses_hidden_index_when_submit_button_index_is_missing(self):
        options, message = settings_ui._apply_settings_operation(
            {
                "takip_edilenler": [
                    {"name": "İlk", "group": "Diğer", "target_price": 100, "url_1": "https://www.amazon.com.tr/dp/B000000001"},
                    {"name": "İkinci", "group": "Moda", "target_price": 200, "url_1": "https://www.zara.com/tr/tr/ornek-p03166301.html"},
                ]
            },
            {
                "operation": ["update_watch"],
                "watch_index": ["1"],
                "watches_1_name": ["İkinci"],
                "watches_1_group": ["Teknoloji"],
                "watches_1_target_price": ["1.500"],
                "watches_1_url_1": ["https://www.zara.com/tr/tr/ornek-p03166301.html"],
                "watches_1_notify_once_in_24H": ["1"],
                "watches_1_active": ["1"],
            },
        )

        self.assertIn("güncellendi", message)
        self.assertEqual(options["takip_edilenler"][0]["group"], "Diğer")
        self.assertEqual(options["takip_edilenler"][1]["group"], "Teknoloji")

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
        self.assertIn("data-delete-watch", interaction_script)
        self.assertIn("add-watch-card", interaction_script)
        self.assertNotIn("button.disabled = true", interaction_script)

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
            with patch.object(http_client, "log") as timing_log:
                first = fetch_amazon_page(session, url, 10)
                second = fetch_amazon_page(session, url, 10)
        finally:
            http_client.curl_requests = original_curl_requests

        self.assertIs(first, second)
        self.assertEqual(session.calls, 1)
        messages = [call.args[0] for call in timing_log.call_args_list]
        timing_messages = [message for message in messages if "Amazon ağ yanıt süresi:" in message]
        self.assertEqual(len(timing_messages), 1)
        self.assertIn("taşıma=requests", timing_messages[0])
        self.assertIn("süre=", timing_messages[0])
        self.assertEqual(session._hermes_amazon_cycle_metrics["page_fetch_calls"], 2)
        self.assertEqual(session._hermes_amazon_cycle_metrics["network_attempts"], 1)
        self.assertEqual(session._hermes_amazon_cycle_metrics["response_cache_hits"], 1)

    def test_amazon_protection_detection_ignores_scripts_and_product_words(self):
        html = '''<html><title>Amazon</title><body>
          <span id="productTitle">Robot süpürge</span>
          <script>var url="/errors/validateCaptcha"; var message="not a robot";</script>
          <style>.captcha { color:red; }</style><!-- Robot Check -->
          <img src="captcha-example.jpg"><p>CAPTCHA teknolojisi hakkında bilgi</p>
        </body></html>'''
        self.assertFalse(http_client.is_amazon_protection_page(html))
        self.assertFalse(service.is_bot_protection_page("amazon", html))

    def test_amazon_protection_detection_accepts_real_challenges(self):
        pages = [
            '<form action="/errors_page/validateCaptcha"><button>Alışverişe Devam Et</button></form>',
            '<form action="/errors/validateCaptcha"><input id="captchacharacters"></form>',
            '<title>Robot Check</title><body>Amazon</body>',
            '<body>Enter the characters you see below</body>',
            '<body>Robot olmadığınızı doğrulayın</body>',
            '<body>For automated access to Amazon data please contact us</body>',
        ]
        for html in pages:
            with self.subTest(html=html):
                self.assertTrue(http_client.is_amazon_protection_page(html))

    def test_amazon_block_is_terminal_without_rescue_or_cookie_reset(self):
        response = requests.Response()
        response.status_code = 200
        response._content = b'<form action="/errors_page/validateCaptcha">Amazon</form>'
        response.encoding = "utf-8"
        session = requests.Session()
        session.cookies.set("session-id", "keep-me", domain=".amazon.com.tr")
        with (patch.object(http_client, "curl_requests", None),
              patch.object(session, "get", return_value=response) as get,
              patch.object(http_client, "_get_amazon_response_with_browser") as browser):
            with self.assertRaisesRegex(HermesError, "koruma"):
                fetch_amazon_page(session, "https://www.amazon.com.tr/s?k=test", 10, True)
        get.assert_called_once()
        browser.assert_not_called()
        self.assertEqual(session.cookies.get("session-id"), "keep-me")
        self.assertEqual(session._hermes_amazon_cycle_metrics["network_attempts"], 1)

    def test_amazon_sessions_survive_cycles_but_prices_are_fetched_again(self):
        class FakeCurlSession:
            def __init__(self):
                self.calls = 0
                self.cookies = requests.cookies.RequestsCookieJar()
                self.closed = False

            def get(self, *_args, **_kwargs):
                self.calls += 1
                self.cookies.set("session-id", "keep-me")
                return http_client._HtmlResponse("https://www.amazon.com.tr/dp/B000000001", f"Amazon price {self.calls}")

            def close(self):
                self.closed = True

        curl = FakeCurlSession()
        with patch.object(http_client, "curl_requests", SimpleNamespace(Session=lambda: curl)):
            with http_client.AmazonClient() as client:
                first_cycle = requests.Session()
                first_cycle._hermes_amazon_client = client
                first = fetch_amazon_page(first_cycle, "https://www.amazon.com.tr/dp/B000000001", 10)
                self.assertIs(first, fetch_amazon_page(first_cycle, first.url, 10))
                second_cycle = requests.Session()
                second_cycle._hermes_amazon_client = client
                second = fetch_amazon_page(second_cycle, first.url, 10)
                self.assertIsNot(first, second)
                self.assertNotEqual(first.text, second.text)
                self.assertEqual(curl.calls, 2)
                self.assertEqual(curl.cookies.get("session-id"), "keep-me")
                self.assertEqual(first_cycle._hermes_amazon_cycle_metrics["network_attempts"], 1)
                self.assertEqual(second_cycle._hermes_amazon_cycle_metrics["network_attempts"], 1)
                self.assertEqual(client.total_attempts, 2)
        self.assertTrue(curl.closed)

    def test_amazon_monitor_reuses_only_amazon_client_between_cycles(self):
        seen = []
        with http_client.AmazonClient() as client:
            with patch.object(service, "_check_once", side_effect=lambda _config, session: seen.append(session)):
                service.check_once(SimpleNamespace(), amazon_client=client)
                service.check_once(SimpleNamespace(), amazon_client=client)
            self.assertIsNot(seen[0], seen[1])
            self.assertIs(seen[0]._hermes_amazon_client, client)
            self.assertIs(seen[1]._hermes_amazon_client, client)

    def test_amazon_http_block_stops_after_one_curl_request(self):
        for status in (429, 503):
            with self.subTest(status=status):
                response = requests.Response()
                response.status_code = status
                response._content = b"Amazon temporary error"
                response.encoding = "utf-8"
                curl = SimpleNamespace(cookies=requests.cookies.RequestsCookieJar(),
                                       get=lambda *_args, **_kwargs: response, close=lambda: None)
                with (http_client.AmazonClient() as client,
                      patch.object(http_client, "curl_requests", SimpleNamespace(Session=lambda: curl)),
                      patch.object(http_client, "_get_amazon_response_with_browser") as browser,
                      patch.object(client.requests_session, "get") as normal_get):
                    session = requests.Session()
                    session._hermes_amazon_client = client
                    with self.assertRaises(requests.HTTPError):
                        fetch_amazon_page(session, "https://www.amazon.com.tr/s?k=test", 10, True)
                    browser.assert_not_called()
                    normal_get.assert_not_called()
                    self.assertIs(client.curl_session, curl)
                    self.assertEqual(client.total_attempts, 1)
                    self.assertEqual(client.block_count, 1)
                    self.assertEqual(session._hermes_amazon_cycle_metrics["network_attempts"], 1)

    def test_amazon_non_protection_failure_has_one_browser_fallback_and_cache(self):
        session = requests.Session()
        response = http_client._HtmlResponse("https://www.amazon.com.tr/dp/B000000001", "Amazon product")
        with (patch.object(http_client, "curl_requests", None),
              patch.object(session, "get", side_effect=requests.Timeout("timeout")) as primary,
              patch.object(http_client, "_get_amazon_response_with_browser", return_value=response) as browser):
            first = fetch_amazon_page(session, response.url, 10)
            second = fetch_amazon_page(session, response.url, 10)
        self.assertIs(first, second)
        primary.assert_called_once()
        browser.assert_called_once()

    def test_amazon_browser_failure_does_not_start_more_transports(self):
        session = requests.Session()
        with (patch.object(http_client, "curl_requests", None),
              patch.object(session, "get", side_effect=requests.Timeout("timeout")) as primary,
              patch.object(http_client, "_get_amazon_response_with_browser", side_effect=HermesError("browser timeout")) as browser):
            with self.assertRaisesRegex(HermesError, "browser timeout"):
                fetch_amazon_page(session, "https://www.amazon.com.tr/s?k=test", 10, True)
        primary.assert_called_once()
        browser.assert_called_once()

    def test_amazon_request_measurement_spans_cycles_and_expires_old_attempts(self):
        with patch.object(http_client.time, "monotonic", return_value=100):
            with http_client.AmazonClient() as client:
                first, second = requests.Session(), requests.Session()
                first._hermes_amazon_client = client
                second._hermes_amazon_client = client
                with patch.object(http_client, "log") as logs:
                    http_client._note_amazon_request(first, "requests", "https://www.amazon.com.tr/dp/B000000001")
                    with patch.object(http_client.time, "monotonic", return_value=110):
                        http_client._note_amazon_request(second, "requests", "https://www.amazon.com.tr/dp/B000000001")
                        http_client._log_amazon_block(second, HermesError("Amazon captcha"))
                    with patch.object(http_client.time, "monotonic", return_value=171):
                        http_client._note_amazon_request(second, "requests", "https://www.amazon.com.tr/dp/B000000001")
                        http_client._log_amazon_block(second, HermesError("Amazon captcha"))
                messages = [call.args[0] for call in logs.call_args_list]
                self.assertIn("son_60sn_deneme=2", messages[1])
                self.assertIn("ara_ms=10000", messages[1])
                self.assertIn("ilk_engel=1", messages[2])
                self.assertIn("önceki_engelden_sonra_deneme=2", messages[2])
                self.assertIn("son_60sn_deneme=1", messages[3])
                self.assertIn("oturum_deneme=3", messages[3])
                self.assertIn("ilk_engel=0", messages[4])
                self.assertIn("önceki_engelden_sonra_deneme=1", messages[4])

    def test_amazon_browser_uses_installed_identity_and_disables_stale_cache(self):
        driver = Mock()
        with (http_client.AmazonClient() as client, patch.object(http_client.webdriver, "Chrome", return_value=driver) as launch,
              patch.object(http_client, "ChromeService"),
              patch.object(http_client, "_chromium_binary", return_value="/usr/bin/chromium"),
              patch.object(http_client.shutil, "which", return_value="/usr/bin/chromedriver")):
            self.assertIs(http_client._start_amazon_browser(client), driver)
            options = launch.call_args.kwargs["options"]
            self.assertEqual(options.binary_location, "/usr/bin/chromium")
            self.assertFalse(any(arg.startswith("--user-agent") for arg in options.arguments))
            self.assertIn("--remote-debugging-pipe", options.arguments)
            driver.execute_cdp_cmd.assert_any_call("Network.setCacheDisabled", {"cacheDisabled": True})
            client.browser_driver = driver

    def test_amazon_browser_driver_is_reused_between_cycles_and_cleaned_up(self):
        driver = Mock()
        driver.page_source = '<html>Amazon product</html>'
        driver.current_url = 'https://www.amazon.com.tr/dp/B000000001'
        driver.get_log.return_value = []
        driver.execute_cdp_cmd.return_value = {"frameTree": {"frame": {"id": "main"}}}
        def start(client):
            client.browser_profile = tempfile.TemporaryDirectory(prefix="hermes-test-browser-")
            return driver
        with http_client.AmazonClient() as client, patch.object(http_client, "_start_amazon_browser", side_effect=start) as launch:
            for _ in range(2):
                with requests.Session() as session:
                    session._hermes_amazon_client = client
                    response = http_client._get_amazon_response_with_browser(session, driver.current_url, 10, False)
                    cached = http_client._get_amazon_response_with_browser(session, driver.current_url, 10, False)
                    self.assertIs(response, cached)
            profile_path = Path(client.browser_profile.name)
            self.assertTrue(profile_path.exists())
            launch.assert_called_once()
            self.assertEqual(driver.get.call_count, 2)
        driver.quit.assert_called_once()
        self.assertFalse(profile_path.exists())

    def test_amazon_browser_http_status_uses_main_document_only(self):
        driver = Mock()
        driver.execute_cdp_cmd.return_value = {"frameTree": {"frame": {"id": "main"}}}
        def entry(frame, status, resource_type="Document"):
            return {"message": json.dumps({"message": {"method": "Network.responseReceived", "params": {
                "frameId": frame, "type": resource_type, "response": {"status": status}}}})}
        driver.get_log.return_value = [entry("iframe", 503), entry("main", 200), entry("main", 503, "Image")]
        self.assertEqual(http_client._browser_document_status(driver), 200)
        driver.get_log.return_value = []
        self.assertIsNone(http_client._browser_document_status(driver))

    def test_amazon_browser_comparison_never_falls_back_to_http(self):
        session = requests.Session()
        with http_client.AmazonClient(transport="browser") as client:
            session._hermes_amazon_client = client
            with (patch.object(http_client, "_get_amazon_response_with_browser", side_effect=HermesError("Amazon captcha")) as browser,
                  patch.object(http_client, "_get_amazon_response_with_curl") as curl,
                  patch.object(http_client, "_get_amazon_response") as plain):
                with self.assertRaisesRegex(HermesError, "captcha"):
                    fetch_amazon_page(session, "https://www.amazon.com.tr/dp/B000000001", 10)
        browser.assert_called_once()
        curl.assert_not_called()
        plain.assert_not_called()

    def test_amazon_diagnostics_keep_seven_days_and_preserve_price_state(self):
        state = {"price_history": "preserved", "_meta": {"amazon_request_diagnostics": [
            {"at": (datetime.now(timezone.utc) - timedelta(days=8)).isoformat(), "reason": "old"},
            {"at": "invalid"}, None,
        ]}}
        event = {"at": utc_now(), "reason": "bot_korumasi", "attempts_last_60_seconds": 5}
        service.append_amazon_diagnostics(state, [event])
        self.assertEqual(state["_meta"]["amazon_request_diagnostics"], [event])
        self.assertEqual(state["price_history"], "preserved")
        service.append_amazon_diagnostics(state, [event] * 1005)
        self.assertEqual(len(state["_meta"]["amazon_request_diagnostics"]), 1000)

    def test_amazon_cycle_persists_block_measurement_once_across_restart(self):
        state = {}
        watch = WatchRule("iPhone", "amazon", "https://www.amazon.com.tr/dp/B000000001", Decimal("1000"))
        config = SimpleNamespace(watches=[watch], interval_seconds=1, request_timeout_seconds=10,
                                 pushover_user_key="", pushover_api_token="")

        def blocked_fetch(session, *_args):
            http_client._note_amazon_request(session, "requests", watch.url)
            error = HermesError("Amazon captcha")
            http_client._log_amazon_block(session, error)
            raise error

        with (http_client.AmazonClient() as client,
              patch.object(service, "load_json", return_value=state),
              patch.object(service, "save_json") as save,
              patch.object(service, "wait_before_request"),
              patch.object(service, "_iter_amazon_product_watch_offers", side_effect=blocked_fetch) as fetch,
              patch.object(service, "publish_price_summary"),
              patch.object(service, "record_cycle_duration"),
              patch.object(service, "maybe_alert_summary_drop"),
              patch.object(service, "maybe_alert_search_failures")):
            service.check_once(config, amazon_client=client)
            self.assertEqual(len(state["_meta"]["amazon_request_diagnostics"]), 1)
            self.assertEqual(len(client.block_events), 0)
            service.check_once(config, amazon_client=client)
            self.assertEqual(len(state["_meta"]["amazon_request_diagnostics"]), 1)
            fetch.assert_called_once()
            self.assertEqual(save.call_args.args[1]["_meta"]["amazon_request_diagnostics"][0]["session_attempts"], 1)

    def test_amazon_browser_captcha_and_service_failure_are_not_stock_states(self):
        for status, html in ((503, '<html>Amazon Service Unavailable</html>'),
                             (200, '<html>Amazon<form action="/errors/validateCaptcha"><input name="captchacharacters"></form></html>')):
            driver = Mock()
            driver.page_source = html
            driver.current_url = "https://www.amazon.com.tr/dp/B000000001"
            driver.get_log.return_value = []
            with (http_client.AmazonClient(transport="browser") as client,
                  patch.object(http_client, "_browser_document_status", return_value=status)):
                client.browser_driver = driver
                session = requests.Session()
                session._hermes_amazon_client = client
                with self.assertRaises(HermesError) as caught:
                    fetch_amazon_page(session, driver.current_url, 10)
                self.assertNotIsInstance(caught.exception, OutOfStockHermesError)
                self.assertEqual(http_client.amazon_error_status(caught.exception), 503 if status == 503 else None)

    def test_amazon_product_url_variants_start_with_clean_product_url(self):
        url = "https://www.amazon.com.tr/gp/product/B0B2PSDNV1?ref=ppx_yo2ov_dt_b_fed_asin_title&th=1"
        variants = amazon_url_variants(url)
        self.assertEqual(variants[0], "https://www.amazon.com.tr/dp/B0B2PSDNV1?th=1")
        self.assertIn(url, variants)

    def test_amazon_protection_pauses_one_watch_then_retries_with_backoff(self):
        state = {}
        error = service.HermesError("Amazon bot korumasi nedeniyle captcha/koruma sayfasi dondu.")
        now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)

        self.assertTrue(service.is_amazon_protection_error(error))
        with patch.object(service, "local_now", return_value=now):
            service.note_amazon_protection(state, "amazon-a", "test", error)
            self.assertEqual(service.amazon_protection_remaining_seconds(state, "amazon-a"), 15 * 60)
            self.assertEqual(service.amazon_protection_remaining_seconds(state, "amazon-b"), 0)
        with patch.object(service, "local_now", return_value=now + timedelta(minutes=15, seconds=1)):
            self.assertEqual(service.amazon_protection_remaining_seconds(state, "amazon-a"), 0)
            service.note_amazon_protection(state, "amazon-a", "test", error)
            self.assertEqual(service.amazon_protection_remaining_seconds(state, "amazon-a"), 30 * 60)
        service.clear_amazon_protection(state, "amazon-a")
        self.assertNotIn("amazon-a", state["_meta"]["amazon_protection"])

    def test_amazon_search_http_503_uses_response_status_for_cooldown(self):
        response = requests.Response()
        response.status_code = 503
        response.url = "https://www.amazon.com.tr/s?k=hue"
        error = requests.HTTPError("503 Server Error: Service Unavailable", response=response)
        self.assertEqual(http_client.amazon_error_status(error), 503)
        self.assertTrue(service.is_amazon_protection_error(error))

        watch = WatchRule("Hue", "amazon", response.url, Decimal("1000"))
        watch_key = service.normalize_item_key("watch", watch.site, watch.tracking_id or watch.name, watch.url, watch.size)
        state = {}
        config = SimpleNamespace(watches=[watch], interval_seconds=1, request_timeout_seconds=20,
                                 pushover_user_key="", pushover_api_token="")
        with (patch.object(service, "load_json", return_value=state),
              patch.object(service, "save_json"),
              patch.object(service, "wait_before_request"),
              patch.object(service, "_fetch_watch_offers", side_effect=error) as fetch,
              patch.object(service, "save_incremental_price_summary"),
              patch.object(service, "publish_price_summary"),
              patch.object(service, "record_cycle_duration"),
              patch.object(service, "maybe_alert_summary_drop"),
              patch.object(service, "maybe_alert_search_failures")):
            service.check_once(config)
        fetch.assert_called_once()
        self.assertGreater(service.amazon_protection_remaining_seconds(state, watch_key), 0)

        state[watch_key]["last_checked_at"] = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        with (patch.object(service, "load_json", return_value=state),
              patch.object(service, "save_json"),
              patch.object(service, "_fetch_watch_offers") as fetch,
              patch.object(service, "publish_price_summary"),
              patch.object(service, "record_cycle_duration"),
              patch.object(service, "maybe_alert_summary_drop"),
              patch.object(service, "maybe_alert_search_failures")):
            service.check_once(config)
        fetch.assert_not_called()

    def test_amazon_protection_skip_does_not_republish_stale_price(self):
        watch = WatchRule("iPhone", "amazon", "https://www.amazon.com.tr/dp/B000000001", Decimal("100000"))
        watch_key = service.normalize_item_key("watch", watch.site, watch.tracking_id or watch.name, watch.url, watch.size)
        state = {watch_key: {"last_price": "90000", "last_checked_at":
                             (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(), "url": watch.url}}
        service.note_amazon_protection(state, watch_key, "iPhone", HermesError("Amazon captcha"))
        config = SimpleNamespace(watches=[watch], interval_seconds=1, request_timeout_seconds=20,
                                 pushover_user_key="", pushover_api_token="")
        with (patch.object(service, "load_json", return_value=state),
              patch.object(service, "save_json"),
              patch.object(service, "_iter_amazon_product_watch_offers") as fetch,
              patch.object(service, "publish_price_summary") as publish,
              patch.object(service, "record_cycle_duration"),
              patch.object(service, "maybe_alert_summary_drop"),
              patch.object(service, "maybe_alert_search_failures")):
            service.check_once(config)
        fetch.assert_not_called()
        self.assertEqual(publish.call_args.args[0], [])

        state["_meta"]["amazon_protection"][watch_key]["retry_after"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        with (patch.object(service, "load_json", return_value=state),
              patch.object(service, "save_json"),
              patch.object(service, "wait_before_request"),
              patch.object(service, "_iter_amazon_product_watch_offers", return_value=iter([
                  OfferResult("iPhone", Decimal("120000"), "Amazon.com.tr", watch.url)
              ])) as fetch,
              patch.object(service, "publish_price_summary") as publish,
              patch.object(service, "record_cycle_duration"),
              patch.object(service, "maybe_alert_summary_drop"),
              patch.object(service, "maybe_alert_search_failures")):
            service.check_once(config)
        fetch.assert_called_once()
        self.assertEqual(len(publish.call_args.args[0]), 1)
        self.assertNotIn(watch_key, state["_meta"]["amazon_protection"])

    def test_deferred_amazon_watch_does_not_restore_price_after_captcha(self):
        watch = WatchRule("iPhone", "amazon", "https://www.amazon.com.tr/dp/B000000001", Decimal("100000"),
                          priority="medium")
        watch_key = service.normalize_item_key("watch", watch.site, watch.tracking_id or watch.name, watch.url, watch.size)
        state = {watch_key: {"last_price": "90000", "last_checked_at": utc_now(), "url": watch.url}}
        service.note_amazon_protection(state, watch_key, "iPhone", HermesError("Amazon captcha"))
        config = SimpleNamespace(watches=[watch], interval_seconds=1, request_timeout_seconds=20,
                                 pushover_user_key="", pushover_api_token="")
        with (patch.object(service, "load_json", return_value=state),
              patch.object(service, "save_json"),
              patch.object(service, "_iter_amazon_product_watch_offers") as fetch,
              patch.object(service, "publish_price_summary") as publish,
              patch.object(service, "record_cycle_duration"),
              patch.object(service, "maybe_alert_summary_drop"),
              patch.object(service, "maybe_alert_search_failures")):
            service.check_once(config)
        fetch.assert_not_called()
        self.assertEqual(publish.call_args.args[0], [])

        state["_meta"]["amazon_protection"][watch_key]["retry_after"] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).isoformat()
        with (patch.object(service, "load_json", return_value=state),
              patch.object(service, "save_json"),
              patch.object(service, "wait_before_request"),
              patch.object(service, "_iter_amazon_product_watch_offers", return_value=iter([
                  OfferResult("iPhone", Decimal("120000"), "Amazon.com.tr", watch.url)
              ])) as fetch,
              patch.object(service, "publish_price_summary"),
              patch.object(service, "record_cycle_duration"),
              patch.object(service, "maybe_alert_summary_drop"),
              patch.object(service, "maybe_alert_search_failures")):
            service.check_once(config)
        fetch.assert_called_once()
        self.assertNotIn(watch_key, state["_meta"]["amazon_protection"])

    def test_amazon_partial_variant_result_keeps_offer_and_pauses_next_scan(self):
        watch = WatchRule("iPhone", "amazon", "https://www.amazon.com.tr/dp/B000000001", Decimal("100000"),
                          include_variations=True)
        watch_key = service.normalize_item_key("watch", watch.site, watch.tracking_id or watch.name, watch.url, watch.size)
        state = {}
        config = SimpleNamespace(watches=[watch], interval_seconds=1, request_timeout_seconds=20,
                                 pushover_user_key="", pushover_api_token="")

        def partial_stream(session, *_args):
            yield OfferResult("iPhone Gümüş", Decimal("120000"), "Amazon.com.tr", watch.url)
            service.remember_amazon_protection(session, HermesError("Amazon captcha"))

        with (patch.object(service, "load_json", return_value=state),
              patch.object(service, "save_json"),
              patch.object(service, "wait_before_request"),
              patch.object(service, "_iter_amazon_product_watch_offers", side_effect=partial_stream),
              patch.object(service, "publish_price_summary") as publish,
              patch.object(service, "record_cycle_duration"),
              patch.object(service, "maybe_alert_summary_drop"),
              patch.object(service, "maybe_alert_search_failures")):
            service.check_once(config)
        self.assertEqual(len(publish.call_args.args[0]), 1)
        self.assertGreater(service.amazon_protection_remaining_seconds(state, watch_key), 0)
        self.assertIn("captcha", state[watch_key]["last_error"])

        state[watch_key]["last_checked_at"] = utc_now()
        with (patch.object(service, "load_json", return_value=state),
              patch.object(service, "save_json"),
              patch.object(service, "_iter_amazon_product_watch_offers") as fetch,
              patch.object(service, "publish_price_summary") as publish,
              patch.object(service, "record_cycle_duration"),
              patch.object(service, "maybe_alert_summary_drop"),
              patch.object(service, "maybe_alert_search_failures")):
            service.check_once(config)
        fetch.assert_not_called()
        self.assertEqual(len(publish.call_args.args[0]), 1)

    def test_amazon_variant_scan_stops_after_protection_page(self):
        watch = WatchRule("iPhone", "amazon", "https://www.amazon.com.tr/dp/B000000001", Decimal("100000"),
                          include_variations=True)
        config = SimpleNamespace(request_timeout_seconds=20)
        with patch.object(service, "fetch_amazon_page", side_effect=HermesError("Amazon captcha")) as fetch:
            with self.assertRaisesRegex(HermesError, "captcha"):
                list(service._iter_amazon_product_watch_offers(requests.Session(), watch, config))
        fetch.assert_called_once()

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
        self.assertFalse(item.is_warehouse)

    def test_amazon_search_secondary_used_offer_is_marked_as_warehouse(self):
        html = """
        <div class="s-main-slot">
          <div data-component-type="s-search-result" data-asin="B000000001">
            <h2><a href="/dp/B000000001"><span>İkinci el ürün</span></a></h2>
            <div data-cy="secondary-offer-recipe">
              Diğer satın alma seçenekleri 12.999,00 TL (1 İkinci El ürün)
            </div>
          </div>
        </div>
        """
        item = extract_result_candidates(html, 10)[0]
        self.assertEqual(item.price, Decimal("12999.00"))
        self.assertTrue(item.is_warehouse)

    def test_amazon_search_reads_plain_card_used_offer_wording(self):
        """Used search cards may not include Amazon's secondary-offer wrapper."""
        html = """
        <div class="s-main-slot">
          <div data-component-type="s-search-result" data-asin="B0D95QG8W4">
            <h2><a href="/dp/B0D95QG8W4"><span>Edifier M60 Siyah</span></a></h2>
            <span class="a-price"><span class="a-offscreen">8.899,00 TL</span></span>
            <p>Diğer satın alma seçenekleri 8.787,77 TL (1 İkinci El ürün)</p>
          </div>
        </div>
        """

        items = extract_result_candidates(html, 10)

        self.assertEqual([item.price for item in items], [Decimal("8899.00"), Decimal("8787.77")])
        self.assertEqual([item.is_warehouse for item in items], [False, True])

    def test_amazon_search_keeps_normal_and_used_prices_on_one_card(self):
        html = """
        <div class="s-main-slot">
          <div data-component-type="s-search-result" data-asin="B0D95QG8W4">
            <h2><a href="/dp/B0D95QG8W4?th=1"><span>Edifier M60 Siyah</span></a></h2>
            <span class="a-price"><span class="a-offscreen">8.899,00 TL</span></span>
            <div data-cy="secondary-offer-recipe">
              Diğer satın alma seçenekleri 8.787,77 TL (1 İkinci El ürün)
            </div>
          </div>
        </div>
        """
        items = extract_result_candidates(html, 10)
        self.assertEqual([item.price for item in items], [Decimal("8899.00"), Decimal("8787.77")])
        self.assertEqual([item.is_warehouse for item in items], [False, True])

    def test_amazon_search_does_not_create_used_offer_when_price_is_identical(self):
        html = """
        <div class="s-main-slot">
          <div data-component-type="s-search-result" data-asin="B0D95QG8W4">
            <h2><a href="/dp/B0D95QG8W4?th=1"><span>Edifier M60 Siyah</span></a></h2>
            <span class="a-price"><span class="a-offscreen">8.899,00 TL</span></span>
            <div data-cy="secondary-offer-recipe">
              Diğer satın alma seçenekleri 8.899,00 TL (1 İkinci El ürün)
            </div>
          </div>
        </div>
        """

        items = extract_result_candidates(html, 10)

        self.assertEqual(len(items), 1)
        self.assertFalse(items[0].is_warehouse)

    def test_amazon_normal_search_primary_offer_stays_normal_with_used_text(self):
        """A normal search must not relabel its new price from card-wide used text."""
        html = """
        <div class="s-main-slot">
          <div data-component-type="s-search-result" data-asin="B0D95QG8W4">
            <h2><a href="/dp/B0D95QG8W4?th=1"><span>Edifier M60 Siyah</span></a></h2>
            <span class="a-price"><span class="a-offscreen">8.899,00 TL</span></span>
            <span>Amazon Depo 1 İkinci El ürün</span>
            <div data-cy="secondary-offer-recipe">
              Diğer satın alma seçenekleri 8.787,77 TL (1 İkinci El ürün)
            </div>
          </div>
        </div>
        """

        items = extract_result_candidates(html, 10, primary_is_warehouse=False)

        self.assertEqual([item.price for item in items], [Decimal("8899.00"), Decimal("8787.77")])
        self.assertEqual([item.is_warehouse for item in items], [False, True])

    def test_amazon_warehouse_search_primary_offer_is_warehouse(self):
        html = """
        <div class="s-main-slot">
          <div data-component-type="s-search-result" data-asin="B0D95QG8W4">
            <h2><a href="/dp/B0D95QG8W4?th=1"><span>Edifier M60 Siyah</span></a></h2>
            <span class="a-price"><span class="a-offscreen">8.787,77 TL</span></span>
            <span>Diğer satın alma seçenekleri 8.787,77 TL (1 İkinci El ürün)</span>
          </div>
        </div>
        """

        items = extract_result_candidates(html, 10, primary_is_warehouse=True)

        self.assertEqual(len(items), 1)
        self.assertTrue(items[0].is_warehouse)

    def test_amazon_warehouse_search_does_not_relabel_plain_cards_as_warehouse(self):
        """Fallback cards in a Depot search are not necessarily used offers."""
        html = """
        <div class="s-main-slot">
          <div data-component-type="s-search-result" data-asin="B0D95QG8W4">
            <h2><a href="/dp/B0D95QG8W4?th=1"><span>Edifier M60 Beyaz</span></a></h2>
          </div>
        </div>
        """

        items = extract_result_candidates(html, 10, primary_is_warehouse=True)

        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0].price)
        self.assertFalse(items[0].is_warehouse)

    def test_amazon_search_deduplication_keeps_normal_and_used_conditions(self):
        rows = dedupe_results(
            [
                SearchResultItem("Edifier M60", "https://www.amazon.com.tr/dp/B0D95QG8W4", Decimal("8899")),
                SearchResultItem(
                    "Edifier M60",
                    "https://www.amazon.com.tr/dp/B0D95QG8W4",
                    Decimal("8787.77"),
                    is_warehouse=True,
                ),
            ]
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual([row.is_warehouse for row in rows], [False, True])

    def test_amazon_primary_price_stays_normal_when_page_mentions_used_offer(self):
        html = """
        <html><head><title>Depo ürünü</title></head><body>
          <div id="corePriceDisplay_desktop_feature_div">
            <span class="a-price"><span class="a-offscreen">12.999,00 TL</span></span>
          </div>
          <div>Amazon Depo - 1 İkinci El ürün</div>
        </body></html>
        """
        offer = extract_amazon_offer(html, "https://www.amazon.com.tr/dp/B000000001?condition=used")
        self.assertEqual(offer.price, Decimal("12999.00"))
        self.assertFalse(offer.is_warehouse)

    def test_amazon_search_context_parameters_do_not_mark_a_product_as_warehouse(self):
        html = """
        <html><head><title>Normal ürün</title></head><body>
          <div id="corePriceDisplay_desktop_feature_div">
            <span class="a-price"><span class="a-offscreen">18.999,00 TL</span></span>
          </div>
          <div id="merchantInfo">Gürgençler Apple Premium Partner</div>
        </body></html>
        """
        url = "https://www.amazon.com.tr/dp/B0GQVC369W?th=1&condition=used&srs=44219324031&bbn=44219324031"
        offer = extract_amazon_offer(html, url)
        self.assertFalse(offer.is_warehouse)

    def test_amazon_warehouse_search_requires_explicit_category(self):
        self.assertTrue(
            is_warehouse_search_url("https://www.amazon.com.tr/s?k=edifier+m60&i=warehouse-deals")
        )
        self.assertTrue(
            is_warehouse_search_url("https://www.amazon.com.tr/s?k=edifier+m60&s=warehouse-deals")
        )
        self.assertFalse(
            is_warehouse_search_url("https://www.amazon.com.tr/dp/B0D95QG8W4?condition=used")
        )

    def test_amazon_reads_only_explicit_numeric_low_stock_message(self):
        low_stock_html = """
        <html><head><title>Amazon ürünü</title></head><body>
          <div id="corePriceDisplay_desktop_feature_div">
            <span class="a-price"><span class="a-offscreen">18.999,00 TL</span></span>
          </div>
          <div id="availability">Stokta sadece 20 adet kaldı.</div>
        </body></html>
        """
        generic_stock_html = '<div id="availability">Stokta var.</div>'

        self.assertEqual(extract_low_stock_quantity(low_stock_html), 20)
        self.assertIsNone(extract_low_stock_quantity(generic_stock_html))
        self.assertEqual(extract_amazon_offer(low_stock_html).stock_quantity, 20)

    def test_amazon_search_deduplication_prefers_low_stock_metadata(self):
        url = "https://www.amazon.com.tr/dp/B000000001"
        rows = dedupe_results(
            [
                SearchResultItem("Amazon ürünü", url, Decimal("18999")),
                SearchResultItem("Amazon ürünü", url, Decimal("18999"), stock_quantity=20),
            ]
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].stock_quantity, 20)

    def test_amazon_product_page_never_relabels_primary_price_as_warehouse(self):
        html = """
        <html><head><title>Çoklu teklif ürünü</title></head><body>
          <div id="corePriceDisplay_desktop_feature_div">
            <span class="a-price"><span class="a-offscreen">8.899,00 TL</span></span>
          </div>
          <div data-cy="secondary-offer-recipe">
            Diğer satın alma seçenekleri 8.787,77 TL (1 İkinci El ürün)
          </div>
        </body></html>
        """
        offers = extract_amazon_offers(html, "https://www.amazon.com.tr/dp/B0D95QG8W4?th=1")
        self.assertEqual([offer.price for offer in offers], [Decimal("8899.00")])
        self.assertEqual([offer.is_warehouse for offer in offers], [False])

    def test_amazon_used_offer_listing_requires_amazon_depo_and_distinct_price(self):
        product_html = """
        <a href="/gp/offer-listing/B0D95QG8W4?condition=used">Yeni & İkinci El Ürün</a>
        """
        listing_html = """
        <html><head><title>Edifier M60 Compact Masa Hoparlörü - Siyah</title></head><body>
          <div class="aod-offer"><span>İkinci El - Çok İyi</span><a>Amazon Depo</a>
            <span class="a-price"><span class="a-offscreen">8.787,77 TL</span></span></div>
          <div class="aod-offer"><span>İkinci El - Çok İyi</span><a>Başka Satıcı</a>
            <span class="a-price"><span class="a-offscreen">8.600,00 TL</span></span></div>
        </body></html>
        """
        source_url = "https://www.amazon.com.tr/dp/B0D95QG8W4?th=1"

        self.assertIn("condition=used", extract_used_offer_listing_url(product_html, source_url))
        offers = extract_verified_warehouse_offers_from_listing(listing_html, source_url)

        self.assertEqual([offer.price for offer in offers], [Decimal("8787.77")])
        self.assertEqual([offer.seller for offer in offers], ["Amazon Depo"])
        self.assertTrue(offers[0].is_warehouse)

    def test_amazon_product_page_omits_same_price_used_duplicate(self):
        html = """
        <html><head><title>Çoklu teklif ürünü</title></head><body>
          <div id="corePriceDisplay_desktop_feature_div">
            <span class="a-price"><span class="a-offscreen">8.899,00 TL</span></span>
          </div>
          <div data-cy="secondary-offer-recipe">
            Diğer satın alma seçenekleri 8.899,00 TL (1 İkinci El ürün)
          </div>
        </body></html>
        """

        offers = extract_amazon_offers(html, "https://www.amazon.com.tr/dp/B0D95QG8W4?th=1")

        self.assertEqual(len(offers), 1)
        self.assertFalse(offers[0].is_warehouse)

    def test_incremental_summary_keeps_normal_and_warehouse_offer_rows(self):
        normal = PriceSummaryRow(
            seller="Amazon",
            product_title="Edifier M60 Siyah",
            product_url="https://www.amazon.com.tr/dp/B0D95QG8W4?th=1",
            price=Decimal("8899"),
            target_price=Decimal("9000"),
            min_price=Decimal("8899"),
            max_price=Decimal("8899"),
            priority="low",
        )
        warehouse = PriceSummaryRow(
            seller="Amazon",
            product_title="Edifier M60 Siyah",
            product_url="https://www.amazon.com.tr/dp/B0D95QG8W4?th=1",
            price=Decimal("8787.77"),
            target_price=Decimal("9000"),
            min_price=Decimal("8787.77"),
            max_price=Decimal("8787.77"),
            is_warehouse=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            summary_path = Path(temp_dir) / "summary.json"
            with patch.object(service, "SUMMARY_PATH", summary_path):
                service.save_price_summary([normal])
                service.save_incremental_price_summary([warehouse])
                payload = json.loads(summary_path.read_text(encoding="utf-8"))

        self.assertEqual(len(payload["rows"]), 2)
        self.assertEqual(sorted(row["is_warehouse"] for row in payload["rows"]), [False, True])
        self.assertEqual(next(row["priority"] for row in payload["rows"] if not row["is_warehouse"]), "low")

    def test_dashboard_keeps_normal_and_warehouse_rows_for_the_same_amazon_asin(self):
        rows = [
            {
                "seller": "Amazon",
                "product_title": "Edifier M60 Siyah",
                "product_url": "https://www.amazon.com.tr/dp/B0D95QG8W4?th=1",
                "price": "8.899 TL",
                "target": "9.000 TL",
                "difference": "-101 TL",
                "is_warehouse": False,
            },
            {
                "seller": "Amazon",
                "product_title": "Edifier M60 Siyah",
                "product_url": "https://www.amazon.com.tr/dp/B0D95QG8W4?condition=used",
                "price": "8.787 TL",
                "target": "9.000 TL",
                "difference": "-213 TL",
                "is_warehouse": True,
            },
        ]

        visible_rows = dashboard._deduplicate_dashboard_rows(rows)

        self.assertEqual(len(visible_rows), 2)
        self.assertEqual(sorted(row["is_warehouse"] for row in visible_rows), [False, True])

    def test_incremental_removal_keeps_the_other_amazon_condition(self):
        normal = PriceSummaryRow(
            "Amazon", "Edifier M60 Siyah", "https://www.amazon.com.tr/dp/B0D95QG8W4",
            Decimal("8899"), Decimal("9000"), Decimal("8899"), Decimal("8899"),
        )
        warehouse = PriceSummaryRow(
            "Amazon", "Edifier M60 Siyah", "https://www.amazon.com.tr/dp/B0D95QG8W4",
            Decimal("8787.77"), Decimal("9000"), Decimal("8787.77"), Decimal("8787.77"),
            is_warehouse=True,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            summary_path = Path(temp_dir) / "summary.json"
            with patch.object(service, "SUMMARY_PATH", summary_path):
                service.save_price_summary([normal, warehouse])
                service.save_incremental_price_summary(
                    [],
                    removed_price_ids={service._price_row_identity(warehouse)},
                )
                payload = json.loads(summary_path.read_text(encoding="utf-8"))

        self.assertEqual(len(payload["rows"]), 1)
        self.assertFalse(payload["rows"][0]["is_warehouse"])

    def test_amazon_product_page_ignores_unscoped_used_text(self):
        html = """
        <html><head><title>Normal ürün</title></head><body>
          <div id="corePriceDisplay_desktop_feature_div">
            <span class="a-price"><span class="a-offscreen">18.999,00 TL</span></span>
          </div>
          <div id="merchantInfo">Gürgençler Apple Premium Partner</div>
          <footer>Diğer satın alma seçenekleri 17.999,00 TL (1 İkinci El ürün)</footer>
        </body></html>
        """
        offers = extract_amazon_offers(html, "https://www.amazon.com.tr/dp/B0GQVC369W?th=1")
        self.assertEqual([offer.price for offer in offers], [Decimal("18999.00")])
        self.assertEqual([offer.is_warehouse for offer in offers], [False])

    def test_amazon_search_ignores_all_departments_fallback_section(self):
        html = """
        <div class="s-main-slot">
          <div data-component-type="s-search-result" data-asin="B000000001">
            <h2><a href="/dp/B000000001"><span>Depo sonucu iPad</span></a></h2>
            <span class="a-price"><span class="a-offscreen">30.000,00 TL</span></span>
          </div>
          <div class="fallback-section"><span>All Departments içindeki sonuçlar gösteriliyor</span></div>
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

    def test_amazon_search_assigns_overlapping_models_to_the_most_specific_card(self):
        results = [
            SearchResultItem(
                title="Apple iPhone 17 Pro 256 GB",
                url="https://www.amazon.com.tr/dp/B000000001",
                price=Decimal("100000"),
            ),
            SearchResultItem(
                title="Apple iPhone 17 Pro Max 256 GB",
                url="https://www.amazon.com.tr/dp/B000000002",
                price=Decimal("110000"),
            ),
        ]
        configured_names = ["Apple iPhone 17 Pro", "Apple iPhone 17 Pro Max"]

        pro_offers = service.offers_from_amazon_search_results(
            results, "Apple iPhone 17 Pro", configured_names
        )
        pro_max_offers = service.offers_from_amazon_search_results(
            results, "Apple iPhone 17 Pro Max", configured_names
        )

        self.assertEqual([offer.url for offer in pro_offers], ["https://www.amazon.com.tr/dp/B000000001"])
        self.assertEqual([offer.url for offer in pro_max_offers], ["https://www.amazon.com.tr/dp/B000000002"])

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

    def test_incremental_summary_keeps_rows_waiting_for_the_cycle(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_summary_path = service.SUMMARY_PATH
            try:
                service.SUMMARY_PATH = Path(tmpdir) / "latest_price_summary.json"
                service.save_price_summary(
                    [
                        PriceSummaryRow(
                            "Amazon",
                            "Daha once okunan urun",
                            "https://example.com/old",
                            Decimal("200"),
                            Decimal("180"),
                            Decimal("200"),
                            Decimal("220"),
                        )
                    ]
                )

                service.save_incremental_price_summary(
                    [
                        PriceSummaryRow(
                            "Hepsiburada",
                            "Bildirim gonderen urun",
                            "https://example.com/fresh",
                            Decimal("90"),
                            Decimal("100"),
                            Decimal("90"),
                            Decimal("120"),
                        )
                    ]
                )

                payload = json.loads(service.SUMMARY_PATH.read_text(encoding="utf-8"))
                self.assertEqual(payload["row_count"], 2)
                prices_by_url = {row["product_url"]: row["price"] for row in payload["rows"]}
                self.assertEqual(prices_by_url["https://example.com/old"], "200 TL")
                self.assertEqual(prices_by_url["https://example.com/fresh"], "90 TL")
            finally:
                service.SUMMARY_PATH = original_summary_path

    def test_cycle_keeps_last_prices_for_medium_and_low_watches_until_their_turn(self):
        now = datetime.now(timezone.utc)
        medium = WatchRule(
            "Orta öncelik", "nordbron", "https://nordbron.com/orta", Decimal("150"), priority="medium"
        )
        low = WatchRule(
            "Düşük öncelik", "nordbron", "https://nordbron.com/dusuk", Decimal("250"), priority="low"
        )
        high = WatchRule(
            "Yüksek öncelik", "nordbron", "https://nordbron.com/yuksek", Decimal("350"), priority="high"
        )
        watches = [medium, low, high]
        state = {"_meta": {"warehouse_state_migration_version": service.WAREHOUSE_STATE_MIGRATION_VERSION}}
        for watch, price in ((medium, "120"), (low, "220")):
            watch_key = service.normalize_item_key("watch", watch.site, watch.tracking_id or watch.name, watch.url, watch.size)
            offer_key = f"cached-{watch.priority}"
            state[watch_key] = {"offer_keys": [offer_key], "last_checked_at": now.isoformat()}
            state[offer_key] = {
                "last_price": price,
                "min_price": price,
                "max_price": price,
                "title": f"Önceden okunan {watch.name}",
                "url": watch.url,
                "configured_url": watch.url,
                "site": watch.site,
                "tracking_id": watch.tracking_id,
                "priority": watch.priority,
                "last_checked_at": now.isoformat(),
            }

        config = SimpleNamespace(
            watches=watches,
            interval_seconds=60,
            request_timeout_seconds=20,
            request_delay_min_seconds=0,
            request_delay_max_seconds=0,
            pushover_user_key="test",
            pushover_api_token="test",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            summary_path = Path(tmpdir) / "latest_price_summary.json"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            with (
                patch.object(service, "STATE_PATH", state_path),
                patch.object(service, "SUMMARY_PATH", summary_path),
                patch.object(service, "local_now", return_value=now),
                patch.object(service, "wait_before_request"),
                patch.object(service, "log") as cycle_log,
                patch.object(
                    service,
                    "_fetch_watch_offers",
                    return_value=[OfferResult("Yeni fırsat", Decimal("300"), url=high.url)],
                ) as fetch,
                patch.object(service, "send_pushover"),
                patch.object(service, "maybe_alert_summary_drop"),
                patch.object(service, "maybe_alert_search_failures"),
            ):
                service.check_once(config)

            fetch.assert_called_once()
            payload = json.loads(summary_path.read_text(encoding="utf-8"))

        rows_by_priority = {row["priority"]: row for row in payload["rows"]}
        self.assertEqual(set(rows_by_priority), {"high", "medium", "low"})
        self.assertEqual(rows_by_priority["medium"]["price"], "120 TL")
        self.assertEqual(rows_by_priority["low"]["price"], "220 TL")
        self.assertEqual(rows_by_priority["high"]["price"], "300 TL")
        self.assertEqual(rows_by_priority["medium"]["price_checked_at"], now.isoformat())
        self.assertEqual(rows_by_priority["low"]["price_checked_at"], now.isoformat())
        coverage_line = next(
            call.args[0]
            for call in cycle_log.call_args_list
            if call.args and "Çevrim öncelik kapsamı:" in call.args[0]
        )
        self.assertIn("yüksek=1 başladı, 1 sırası geldi, 0 ertelendi", coverage_line)
        self.assertIn("orta=0 başladı, 0 sırası geldi, 1 ertelendi", coverage_line)
        self.assertIn("düşük=0 başladı, 0 sırası geldi, 1 ertelendi", coverage_line)

    def test_incremental_summary_removes_stale_rows_for_a_failed_watch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            original_summary_path = service.SUMMARY_PATH
            try:
                service.SUMMARY_PATH = Path(tmpdir) / "latest_price_summary.json"
                stale_url = "https://nordbron.com/stark-sirt-cantasi"
                service.save_price_summary(
                    [
                        PriceSummaryRow(
                            "Nordbron",
                            "Stark Sırt Çantası",
                            stale_url,
                            Decimal("4850"),
                            Decimal("4500"),
                            Decimal("4850"),
                            Decimal("4850"),
                        ),
                        PriceSummaryRow(
                            "Amazon",
                            "Güncel kalan ürün",
                            "https://example.com/current",
                            Decimal("300"),
                            Decimal("250"),
                            Decimal("300"),
                            Decimal("300"),
                        ),
                    ]
                )

                stale_row = PriceSummaryRow(
                    "Nordbron",
                    "Stark Sırt Çantası",
                    stale_url,
                    Decimal("4850"),
                    Decimal("4500"),
                    Decimal("4850"),
                    Decimal("4850"),
                )
                service.save_incremental_price_summary(
                    [],
                    removed_price_ids={service._price_row_identity(stale_row)},
                )

                payload = json.loads(service.SUMMARY_PATH.read_text(encoding="utf-8"))
                self.assertEqual(payload["row_count"], 1)
                self.assertEqual(payload["rows"][0]["product_url"], "https://example.com/current")
            finally:
                service.SUMMARY_PATH = original_summary_path

    def test_public_dashboard_renders_recent_errors_after_the_summary(self):
        summary = {
            "errors": 1,
            "error_details": [
                {
                    "title": "Nordbron",
                    "meta": "Takip edilen link kontrol edilirken hata oluştu.",
                    "message": "Nordbron bot korumasi nedeniyle captcha sayfasi dondu.",
                    "url": "https://nordbron.com/stark-sirt-cantasi",
                    "failed_links": [],
                }
            ],
        }
        with (
            patch.object(dashboard, "_public_dashboard_allowed", return_value=True),
            patch.object(dashboard, "_collect_summary", return_value=summary),
            patch.object(dashboard, "load_json", return_value={}),
            patch.object(dashboard, "_render_table", return_value="<section id='summary-table'></section>"),
        ):
            status, payload = dashboard._render_public_page("/public/demo-token")

        html = payload.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn("Hata sayısı (son 24 saat)", html)
        self.assertIn("Nordbron bot korumasi nedeniyle captcha sayfasi dondu.", html)
        self.assertGreater(html.index("Hata sayısı (son 24 saat)"), html.index("summary-table"))

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

    def test_hepsiburada_product_url_prefers_visible_cart_special_price(self):
        html = """
        <html><head><title>Magly Manyetik Yapı Blokları Fiyatı</title></head>
        <body>
          <h1>Magly Manyetik Yapı Blokları</h1>
          <span>Satıcı: Hepsiburada</span>
          <div data-test-id="price-current-price">2.745,00 TL</div>
          <div class="cart-special-price">Sepete özel fiyat <strong>2.196 TL</strong></div>
          <button>Sepete ekle</button>
          <script>
            window.__HB_STATE__ = {
              "variants": [
                {"sku": "HBCV00007BHN4Z", "variantListing": [
                  {"aiBasedShipmentDay": null, "listingId": "listing-hb", "merchantName": "Hepsiburada",
                   "finalPriceOnSale": 2745,
                   "minimumPrices": [{"name": "non-segmented-price", "value": 2745}]}
                ]}
              ]
            };
          </script>
        </body></html>
        """

        offer = extract_hepsiburada_offer(
            html,
            source_url="https://www.hepsiburada.com/magly-manyetik-yapi-bloklari-p-HBCV00007BHN4Z",
        )

        self.assertEqual(offer.price, Decimal("2196"))

    def test_hepsiburada_product_url_reads_real_cart_special_mapping(self):
        html = """
        <html><head><title>Magly Manyetik Yapı Blokları Fiyatı</title></head>
        <body>
          <h1>Magly Manyetik Yapı Blokları</h1>
          <script>
            window.__HB_STATE__ = {
              "variants": [
                {"sku": "HBCV00007BHN4Z", "variantListing": [
                  {"listingId": "listing-magly", "merchantName": "Magly",
                   "finalPriceOnSale": 2745, "minimumPrice": 1921.5,
                   "minimumPrices": [
                     {"name": "10", "value": 1921.5},
                     {"name": "30", "value": 1921.5},
                     {"name": "non-segmented-price", "value": 2196}
                   ]}
                ]}
              ]
            };
          </script>
        </body></html>
        """
        source_url = (
            "https://www.hepsiburada.com/magly-manyetik-yapi-bloklari-cocuklar-icin-renkli-"
            "3-boyutlu-72-parca-manyetik-karo-oyun-seti-p-HBCV00007BHN4Z"
        )

        embedded_offer = extract_embedded_variant_offer(html, source_url)
        offer = extract_hepsiburada_offer(html, source_url=source_url)

        self.assertIsNotNone(embedded_offer)
        self.assertEqual(embedded_offer.price, Decimal("2196"))
        self.assertEqual(offer.price, Decimal("2196"))

    def test_hepsiburada_detail_ignores_cart_special_discount_amount(self):
        html = """
        <html><head><title>Magly Manyetik Yapı Blokları Fiyatı</title></head>
        <body>
          <h1>Magly Manyetik Yapı Blokları</h1>
          <span>Satıcı: Hepsiburada</span>
          <div>Sepete özel 250 TL indirim</div>
          <div data-test-id="price-current-price">2.745,00 TL</div>
          <button>Sepete ekle</button>
        </body></html>
        """

        offer = extract_hepsiburada_offer(html)

        self.assertEqual(offer.price, Decimal("2745"))

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

    def test_summary_keeps_identical_offers_for_distinct_tracking_cards(self):
        rows = [
            PriceSummaryRow(
                "Amazon", "Apple iPhone 17 Pro Max", "https://www.amazon.com.tr/dp/B000000001",
                Decimal("121499"), Decimal("100000"), Decimal("121499"), Decimal("121499"),
                tracking_id="iphone-17-pro",
            ),
            PriceSummaryRow(
                "Amazon", "Apple iPhone 17 Pro Max", "https://www.amazon.com.tr/dp/B000000001",
                Decimal("121499"), Decimal("110000"), Decimal("121499"), Decimal("121499"),
                tracking_id="iphone-17-pro-max",
            ),
        ]

        unique_rows = service.deduplicate_summary_rows(rows)

        self.assertEqual(len(unique_rows), 2)
        self.assertEqual({row.target_price for row in unique_rows}, {Decimal("100000"), Decimal("110000")})

    def test_tracking_card_id_is_shared_by_its_links_and_unique_per_card(self):
        watches = _prepare_watches(
            [
                {
                    "name": "Apple iPhone 17 Pro",
                    "target_price": 100000,
                    "url_1": "https://www.amazon.com.tr/dp/B000000001",
                    "url_2": "https://www.amazon.com.tr/dp/B000000002",
                },
                {
                    "name": "Apple iPhone 17 Pro Max",
                    "target_price": 110000,
                    "url_1": "https://www.amazon.com.tr/dp/B000000001",
                },
            ]
        )

        self.assertEqual(watches[0].tracking_id, watches[1].tracking_id)
        self.assertNotEqual(watches[0].tracking_id, watches[2].tracking_id)

    def test_summary_keeps_one_row_for_equivalent_amazon_product_urls(self):
        rows = [
            PriceSummaryRow(
                "Amazon",
                "Ürün",
                "https://www.amazon.com.tr/dp/B000000001?th=1",
                Decimal("100"),
                Decimal("90"),
                Decimal("100"),
                Decimal("100"),
            ),
            PriceSummaryRow(
                "Amazon",
                "Ürün",
                "https://www.amazon.com.tr/gp/product/B000000001?smid=A1",
                Decimal("95"),
                Decimal("90"),
                Decimal("95"),
                Decimal("100"),
            ),
        ]

        unique_rows = service.deduplicate_summary_rows(rows)

        self.assertEqual(len(unique_rows), 1)
        self.assertEqual(unique_rows[0].price, Decimal("95"))

    def test_summary_keeps_normal_and_warehouse_offers_separate(self):
        rows = [
            PriceSummaryRow(
                "Amazon", "Normal ürün", "https://www.amazon.com.tr/dp/B000000001",
                Decimal("100"), Decimal("90"), Decimal("100"), Decimal("100"), is_warehouse=False,
            ),
            PriceSummaryRow(
                "Amazon", "Depo ürün", "https://www.amazon.com.tr/dp/B000000001",
                Decimal("80"), Decimal("90"), Decimal("80"), Decimal("80"), is_warehouse=True,
            ),
        ]

        unique_rows = service.deduplicate_summary_rows(rows)

        self.assertEqual(len(unique_rows), 2)

    def test_summary_merges_same_amazon_offer_from_normal_and_warehouse_searches(self):
        rows = [
            PriceSummaryRow(
                "Amazon", "Edifier M60 Compact Masa Hoparlörü - Siyah (Stok 10)",
                "https://www.amazon.com.tr/dp/B000000001?th=1", Decimal("7799"), Decimal("9000"),
                Decimal("7799"), Decimal("9421"), is_warehouse=True, tracking_id="edifier-m60",
            ),
            PriceSummaryRow(
                "Amazon", "Edifier M60 Compact Masa Hoparlörü - Siyah",
                "https://www.amazon.com.tr/dp/B000000002", Decimal("7799"), Decimal("9000"),
                Decimal("7799"), Decimal("7799"), is_warehouse=True, tracking_id="edifier-m60",
            ),
        ]

        unique_rows = service.deduplicate_summary_rows(rows)

        self.assertEqual(len(unique_rows), 1)
        self.assertEqual(unique_rows[0].min_price, Decimal("7799"))
        self.assertEqual(unique_rows[0].max_price, Decimal("9421"))

    def test_dashboard_merges_same_offer_from_two_amazon_searches(self):
        rows = [
            {
                "seller": "Amazon", "product_title": "Edifier M60 - Siyah (Stok 10)",
                "product_url": "https://www.amazon.com.tr/dp/B000000001", "difference": "-1.201 TL",
                "is_warehouse": True, "tracking_id": "edifier-m60",
            },
            {
                "seller": "Amazon", "product_title": "Edifier M60 - Siyah",
                "product_url": "https://www.amazon.com.tr/dp/B000000002", "difference": "-1.201 TL",
                "is_warehouse": True, "tracking_id": "edifier-m60",
            },
        ]

        self.assertEqual(len(dashboard._deduplicate_dashboard_rows(rows)), 1)

    def test_dashboard_labels_warehouse_rows(self):
        row_html = dashboard._render_table_row(
            {
                "seller": "Amazon",
                "product_title": "Depo ürünü",
                "product_url": "https://www.amazon.com.tr/dp/B000000001",
                "price": "12.999 TL",
                "target": "13.000 TL",
                "difference": "-1 TL",
                "min_price": "12.999 TL",
                "max_price": "12.999 TL",
                "is_warehouse": True,
            }
        )
        self.assertIn('class="warehouse-tag">DEPO</strong>', row_html)
        self.assertNotIn("priority-dot", row_html)

    def test_dashboard_shows_priority_dots_for_normal_rows(self):
        for priority, label in (("high", "Yüksek"), ("medium", "Orta"), ("low", "Düşük")):
            with self.subTest(priority=priority):
                row_html = dashboard._render_table_row(
                    {
                        "seller": "Amazon",
                        "product_title": "iPhone 17 Pro Max",
                        "product_url": "https://www.amazon.com.tr/dp/B000000001",
                        "price": "131.624 TL",
                        "target": "130.000 TL",
                        "difference": "+1.624 TL",
                        "min_price": "131.624 TL",
                        "max_price": "131.624 TL",
                        "priority": priority,
                    }
                )
                self.assertIn(f"priority-{priority}", row_html)
                self.assertIn(f'title="{label} öncelik"', row_html)

    def test_dashboard_shortens_long_product_titles_to_60_characters_without_ellipsis(self):
        full_title = "Çok uzun ürün adı " * 12
        row_html = dashboard._render_table_row(
            {
                "seller": "Amazon",
                "product_title": full_title,
                "product_url": "https://www.amazon.com.tr/dp/B000000001",
                "price": "12.999 TL",
                "target": "13.000 TL",
                "difference": "-1 TL",
                "min_price": "12.999 TL",
                "max_price": "12.999 TL",
            }
        )
        visible_title, tooltip = dashboard._table_title(full_title)
        self.assertLessEqual(len(visible_title), 60)
        self.assertEqual(visible_title, full_title.strip()[:60].rstrip())
        self.assertFalse(visible_title.endswith("..."))
        self.assertEqual(tooltip, full_title.strip())
        self.assertIn(visible_title, row_html)
        self.assertIn(tooltip, row_html)

    def test_dashboard_shortens_long_collapsible_group_titles_to_70_characters(self):
        full_title = "Çok uzun grup başlığı " * 8

        group_html = dashboard._render_collapsible_search_group(full_title, [])
        visible_title, tooltip = dashboard._group_title(full_title)

        self.assertEqual(len(visible_title), 70)
        self.assertTrue(visible_title.endswith("..."))
        self.assertEqual(tooltip, full_title.strip())
        self.assertIn(visible_title, group_html)
        self.assertIn(f'title="{tooltip}"', group_html)

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
        html = settings_ui._watch_tools_section([], ["Moda", "Teknoloji", "Market"])

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
        self.assertIn("kaydedildi", message)

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

    def test_cached_new_watch_submission_cannot_overwrite_an_existing_watch(self):
        options, message = settings_ui._apply_settings_operation(
            {
                "takip_edilenler": [
                    {
                        "name": "Nordbron çanta",
                        "target_price": 4500,
                        "url_1": "https://nordbron.com/stark-sirt-cantasi",
                    }
                ]
            },
            {
                # This mirrors the faulty old browser script: the new-card
                # form is mislabeled as update_watch and has no watch_index.
                "operation": ["update_watch"],
                "watches_count": ["1"],
                "watches_0_name": ["Belkin şarj"],
                "watches_0_target_price": ["4000"],
                "watches_0_url_1": ["https://www.amazon.com.tr/dp/B000000001"],
            },
        )

        self.assertIn("eklendi", message)
        self.assertEqual([item["name"] for item in options["takip_edilenler"]], ["Nordbron çanta", "Belkin şarj"])

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


class StatisticsAndPriceAgeTests(unittest.TestCase):
    def test_legacy_summary_recovers_price_time_from_matching_offer_state(self):
        checked_at = (datetime.now(timezone.utc) - timedelta(minutes=125)).isoformat()
        url = "https://www.amazon.com.tr/dp/B000000001"
        row = {
            "product_url": url, "price": "90 TL", "tracking_id": "phone",
            "is_warehouse": False, "price_checked_at": "",
        }
        state = {
            "offer": {"url": url, "last_price": "90.50", "last_checked_at": checked_at,
                      "tracking_id": "phone", "is_warehouse": False},
            "other_price": {"url": url, "last_price": "91", "last_checked_at": datetime.now(timezone.utc).isoformat(),
                            "tracking_id": "phone", "is_warehouse": False},
            "warehouse": {"url": url, "last_price": "90", "last_checked_at": datetime.now(timezone.utc).isoformat(),
                          "tracking_id": "phone", "is_warehouse": True},
        }
        hydrated = dashboard._attach_state_price_times([row], state)
        self.assertEqual(hydrated[0]["price_checked_at"], checked_at)
        self.assertIn("125 dk önce", dashboard._render_table_row(hydrated[0]))
        self.assertEqual(row["price_checked_at"], "")

        with tempfile.TemporaryDirectory() as tmpdir:
            summary_path = Path(tmpdir) / "summary.json"
            state_path = Path(tmpdir) / "state.json"
            options_path = Path(tmpdir) / "options.json"
            summary_path.write_text(json.dumps({"rows": [{
                **row, "seller": "Amazon", "product_title": "Telefon",
                "target": "100 TL", "difference": "-10 TL", "min_price": "90 TL", "max_price": "90 TL",
            }]}), encoding="utf-8")
            state_path.write_text(json.dumps(state), encoding="utf-8")
            options_path.write_text("{}", encoding="utf-8")
            with patch.object(dashboard, "SUMMARY_PATH", summary_path), patch.object(
                dashboard, "STATE_PATH", state_path
            ), patch.object(dashboard, "OPTIONS_PATH", options_path):
                self.assertIn("125 dk önce", dashboard._render_table())

    def test_duplicate_equal_price_uses_latest_successful_read(self):
        older = PriceSummaryRow(
            "Amazon", "Aynı ürün", "https://www.amazon.com.tr/dp/B000000001",
            Decimal("90"), Decimal("100"), Decimal("80"), Decimal("90"),
            price_checked_at="2026-09-24T10:00:00+00:00",
        )
        newer = PriceSummaryRow(
            "Amazon", "Aynı ürün", "https://www.amazon.com.tr/dp/B000000001",
            Decimal("90"), Decimal("100"), Decimal("90"), Decimal("100"),
            price_checked_at="2026-09-24T11:00:00+00:00",
        )
        rows = service.deduplicate_summary_rows([older, newer])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].price_checked_at, newer.price_checked_at)
        self.assertEqual((rows[0].min_price, rows[0].max_price), (Decimal("80"), Decimal("100")))

    def test_completed_cycle_history_keeps_only_last_seven_days(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(service, "CYCLE_HISTORY_PATH", Path(tmpdir) / "cycles.json"):
                service.CYCLE_HISTORY_PATH.write_text(json.dumps([
                    {"checked_at": (now - timedelta(days=8)).isoformat(), "duration_seconds": 400},
                    {"checked_at": (now - timedelta(days=2)).isoformat(), "duration_seconds": 300},
                ]), encoding="utf-8")
                service.record_cycle_duration(180, now)
                history = json.loads(service.CYCLE_HISTORY_PATH.read_text(encoding="utf-8"))
                self.assertEqual([item["duration_seconds"] for item in history], [300, 180])

    def test_price_age_survives_incremental_summary_and_skipped_watch(self):
        checked_at = (datetime.now(timezone.utc) - timedelta(minutes=125)).isoformat()
        watch = WatchRule("Ürün", "amazon", "https://www.amazon.com.tr/dp/B000000001", Decimal("100"))
        state_row = service.summary_row_from_state(watch, {
            "last_price": "90", "last_price_checked_at": checked_at,
        }, "Amazon")
        self.assertEqual(state_row.price_checked_at, checked_at)
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(service, "SUMMARY_PATH", Path(tmpdir) / "summary.json"):
                service.save_price_summary([state_row])
                service.save_incremental_price_summary([PriceSummaryRow(
                    "Amazon", "Yeni fırsat", "https://www.amazon.com.tr/dp/B000000002",
                    Decimal("80"), Decimal("100"), Decimal("80"), Decimal("80"),
                    price_checked_at=datetime.now(timezone.utc).isoformat(),
                )])
                payload = json.loads(service.SUMMARY_PATH.read_text(encoding="utf-8"))
                old_row = next(row for row in payload["rows"] if row["product_url"].endswith("B000000001"))
                self.assertEqual(old_row["price_checked_at"], checked_at)
                html = dashboard._render_table_row(old_row)
                self.assertIn("125 dk önce", html)
                self.assertIn('data-label="Son güncelleme"', html)

    def test_statistics_page_shows_compact_daily_range_without_outliers(self):
        now = datetime.now(timezone.utc)
        same_day = now.replace(hour=12, minute=0, second=0, microsecond=0)
        history = [
            {"checked_at": (same_day - timedelta(days=1)).isoformat(), "duration_seconds": 120},
            {"checked_at": (same_day - timedelta(days=1, minutes=1)).isoformat(), "duration_seconds": 230},
            {"checked_at": (same_day - timedelta(days=1, minutes=2)).isoformat(), "duration_seconds": 240},
            {"checked_at": (same_day - timedelta(days=1, minutes=3)).isoformat(), "duration_seconds": 250},
            {"checked_at": (same_day - timedelta(days=1, minutes=4)).isoformat(), "duration_seconds": 900},
        ]
        with patch.object(dashboard, "load_json", return_value=history):
            html = dashboard._render_statistics_page("/statistics", ".").decode("utf-8")
        self.assertIn("Son 7 gün · 5 çevrim", html)
        self.assertIn("2 dk 0 sn", html)
        self.assertIn("4 dk 0 sn", html)
        self.assertIn("15 dk 0 sn", html)
        self.assertIn('class="cycle-line"', html)
        self.assertIn("Günlük çevrim özeti", html)
        self.assertEqual(html.count('<details class="statistics-day">'), 1)
        self.assertIn('<span class="statistics-day-typical"><strong>4:00</strong></span>', html)
        self.assertIn('<span><strong>3:50–4:10</strong></span>', html)
        self.assertIn('<span class="statistics-day-slow"><strong>1</strong></span>', html)
        self.assertIn("En uzun <strong>15 dk 0 sn</strong>", html)
        self.assertNotIn("statistics-day-bar", html)
        with patch.object(dashboard, "_public_dashboard_allowed", return_value=True), patch.object(
            dashboard, "load_json", return_value=history
        ):
            status, public_html = dashboard._render_public_page("/public/test-token/statistics")
        self.assertEqual(status, 200)
        self.assertIn(b"/public/test-token/settings", public_html)


if __name__ == "__main__":
    unittest.main()
