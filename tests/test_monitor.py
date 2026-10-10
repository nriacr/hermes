"""Monitoring cycle: scheduling, notifications, guards, summary and state."""

import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from support import LOG_LINES, TempData, config, key, monitor, notifier, watch

from hermes.config import prepare_watches
from hermes.constants import SEARCH_ERROR_NOTIFICATION_HOUR
from hermes.errors import BotProtectionHermesError, EmptySearchResultsHermesError, HermesError, HttpStatusHermesError, OutOfStockHermesError
from hermes.models import OfferResult, PriceSummaryRow, StockSummaryRow
from hermes.monitor import alerts, runner, scheduling, state as state_ops, summary
from hermes.monitor.results import skipped_offer_reason
from hermes.providers.amazon import AmazonProvider, WatchRhythm
from hermes.providers.bengurme import BenGurmeProvider
from hermes.providers.hepsiburada import HepsiburadaProvider
from hermes.providers.nordbron import NordbronProvider
from hermes.utils import format_duration, utc_now

AMAZON = "https://www.amazon.com.tr/dp/B000000001"


def reader(*results):
    """A provider `read` replacement: offers, a callable, or an exception per call."""
    calls = list(results)

    def read(rule, ctx, outcome):
        item = calls.pop(0) if len(calls) > 1 else calls[0]
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return item(rule, ctx, outcome)
        return list(item)

    return read


class CycleTestCase(unittest.TestCase):
    def setUp(self):
        self.data = TempData()
        self.notify = notifier()
        LOG_LINES.clear()

    def tearDown(self):
        self.data.cleanup()

    def run_cycle(self, cfg, times=1):
        hermes_monitor = monitor(cfg, self.data, self.notify)
        try:
            for _ in range(times):
                hermes_monitor.run_cycle()
        finally:
            hermes_monitor.close()
        return self.data.state()

    def run_later(self, cfg, seconds=5):
        """Another cycle once a high-priority watch is due again."""
        later = datetime.now().astimezone() + timedelta(seconds=seconds)
        with patch.object(scheduling, "local_now", return_value=later):
            return self.run_cycle(cfg)

    def published_rows(self):
        return self.data.summary().get("rows", [])

    def published_stock(self):
        return self.data.summary().get("stock_rows", [])

    def titles_sent(self):
        return [call.args[0] for call in self.notify.send.call_args_list]


class NotificationTests(CycleTestCase):
    def test_access_errors_are_silent_kinds(self):
        for error in (BotProtectionHermesError("Amazon bot koruması nedeniyle doğrulama (captcha) sayfası döndü."),
                      HttpStatusHermesError(503, "https://www.amazon.com.tr/s?k=test"),
                      requests.HTTPError("Service Unavailable", response=SimpleNamespace(status_code=503)),
                      BotProtectionHermesError("Hepsiburada bot koruması nedeniyle doğrulama (captcha) sayfası döndü.")):
            with self.subTest(error=str(error)):
                self.assertTrue(alerts.is_silent_access_error(error))
        self.assertFalse(alerts.is_silent_access_error(HermesError("Amazon varyantları okunamadı.")))

    def test_captcha_stays_silent_but_a_verified_depot_offer_notifies(self):
        searches = [watch("Arama 0", "https://www.amazon.com.tr/s?k=test0"),
                    watch("Arama 1", "https://www.amazon.com.tr/s?k=test1"),
                    watch("Arama 3", "https://www.hepsiburada.com/ara?q=test3")]
        depot = watch("Depo", AMAZON)
        cfg = config([depot] + searches)
        self.data.write_state({"_meta": {"summary_config_signature": alerts.summary_config_signature(cfg),
                                         "summary_expected_row_count": 20, "summary_drop_consecutive_cycles": 4}})
        depot_started = threading.Event()

        def amazon_read(rule, ctx, outcome):
            if rule is depot:
                depot_started.set()
                return [OfferResult("Depo ürünü", Decimal("500"), seller="Amazon Depo", url=depot.url, is_warehouse=True)]
            # The block comes after the Depo lane has begun its read (it runs beside the search queue).
            depot_started.wait(5)
            raise BotProtectionHermesError("Amazon bot koruması nedeniyle doğrulama (captcha) sayfası döndü.")

        now = datetime(2026, 10, 2, SEARCH_ERROR_NOTIFICATION_HOUR, tzinfo=timezone.utc)
        hepsiburada_error = BotProtectionHermesError("Hepsiburada bot koruması nedeniyle doğrulama (captcha) sayfası döndü.")
        with (patch.object(AmazonProvider, "read", side_effect=amazon_read),
              patch.object(HepsiburadaProvider, "read", side_effect=lambda rule, *_a: (_ for _ in ()).throw(hepsiburada_error)),
              patch.object(alerts, "local_now", return_value=now)):
            state = self.run_cycle(cfg)
        self.notify.send.assert_called_once()
        self.assertIn("Depo ürünü", self.notify.send.call_args.args[1])
        self.assertEqual(len(self.published_rows()), 1)
        self.assertTrue(self.published_rows()[0]["is_warehouse"])
        # The first Amazon search hit the block; the site is paused, so the second never ran.
        self.assertTrue(state[key(searches[0])]["last_error"])
        self.assertNotIn("last_error", state.get(key(searches[1]), {}))
        self.assertTrue(state[key(searches[2])]["last_error"])
        for rule in (searches[0], searches[2]):
            self.assertNotIn("last_error_notified_at", state[key(rule)])
        self.assertNotIn("last_search_failure_alert_at", state["_meta"])
        self.assertNotIn("last_summary_drop_alert_at", state["_meta"])

    def test_partial_depot_opportunity_notifies_even_when_a_sibling_hits_captcha(self):
        rule = watch("Depo", AMAZON)
        cfg = config([rule])

        def partial(rule, ctx, outcome):
            yield OfferResult("Depo ürünü", Decimal("500"), seller="Amazon Depo", url=rule.url, is_warehouse=True)
            outcome.blocked = BotProtectionHermesError("Amazon captcha")

        with patch.object(AmazonProvider, "read", side_effect=partial):
            state = self.run_cycle(cfg)
        self.notify.send.assert_called_once()
        self.assertTrue(state[key(rule)]["amazon_partial_result"])
        self.assertIn("captcha", state[key(rule)]["last_error"])
        self.assertGreater(state_ops.guard_remaining_seconds(state, state_ops.site_guard_key("amazon")), 0)

    def test_other_search_errors_send_individual_and_aggregate_notifications(self):
        searches = [watch(f"Arama {i}", f"https://www.amazon.com.tr/s?k=test{i}") for i in range(4)]
        now = datetime(2026, 10, 2, SEARCH_ERROR_NOTIFICATION_HOUR, tzinfo=timezone.utc)
        with (patch.object(AmazonProvider, "read", side_effect=reader(HttpStatusHermesError(500, "x"))),
              patch.object(alerts, "local_now", return_value=now)):
            self.run_cycle(config(searches))
        self.assertEqual(self.notify.send.call_count, 5)
        self.assertIn("Hermes arama erişim uyarısı", self.titles_sent())

    def test_opportunity_is_queued_and_saved_before_the_next_variant_is_read(self):
        rule = watch(url=AMAZON, target="100000", include_variations=True)
        events = []

        def stream(rule, ctx, outcome):
            yield OfferResult("iPhone Gümüş 256 GB", Decimal("89040.87"), "Amazon Depo", rule.url, True)
            events.append("second")
            saved = self.data.state()
            self.assertTrue(any(item.get("pending_alert_price") for item in saved.values() if isinstance(item, dict)))
            from hermes.database import Database
            with Database.at(self.data.files.database).lock:
                pending = Database.at(self.data.files.database).connect().execute(
                    "SELECT count(*) FROM outbox WHERE state='pending'").fetchone()[0]
            self.assertEqual(pending, 1)
            yield OfferResult("iPhone Abis 512 GB", Decimal("132000"), url="https://www.amazon.com.tr/dp/B000000002")

        self.notify.send.side_effect = lambda *_args, **_kwargs: events.append("notify")
        with patch.object(AmazonProvider, "read", side_effect=stream):
            self.run_cycle(config([rule]))
        self.assertEqual(events, ["second", "notify"])
        self.assertIn("Depo", self.notify.send.call_args.args[0])
        self.assertIn("Amazon Depo", self.notify.send.call_args.args[1])

    def test_a_failed_notification_keeps_the_price_and_retries_next_cycle(self):
        rule = watch(url=AMAZON, target="1000")
        self.notify.send.side_effect = requests.ConnectionError("pushover down")
        with patch.object(AmazonProvider, "read", side_effect=reader([OfferResult("iPhone", Decimal("900"), "Amazon.com.tr", AMAZON)])):
            state = self.run_cycle(config([rule]))
            self.assertIsNone(state[key(rule)]["last_error"])
            self.assertEqual(len(self.published_rows()), 1)
            offer_key = state[key(rule)]["offer_keys"][0]
            self.assertNotIn("last_alerted_price", state[offer_key])
            self.notify.send.side_effect = None
            from hermes.database import Database
            Database.at(self.data.files.database).transaction([("UPDATE outbox SET due=0", ())])
            state = self.run_later(config([rule]))
        self.assertEqual(self.notify.send.call_count, 2)
        self.assertEqual(state[offer_key]["last_alerted_price"], "900")

    def test_repeat_alerts_follow_the_24_hour_rule_and_lower_prices(self):
        rule = watch(url=AMAZON, target="1000")
        offers = [OfferResult("iPhone", Decimal("900"), url=AMAZON)]
        with patch.object(AmazonProvider, "read", side_effect=reader(offers)) as read:
            self.run_cycle(config([rule]))
            state = self.run_later(config([rule]))
        self.assertEqual(read.call_count, 2)
        self.notify.send.assert_called_once()
        entry = state[state[key(rule)]["offer_keys"][0]]
        self.assertFalse(state_ops.should_alert(entry, Decimal("900"), Decimal("1000"), True))
        self.assertTrue(state_ops.should_alert(entry, Decimal("850"), Decimal("1000"), True))
        entry["last_alerted_at"] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        self.assertTrue(state_ops.should_alert(entry, Decimal("900"), Decimal("1000"), True))

    def test_stock_return_notifies_once_and_still_records_the_offers(self):
        rule = watch("Kan üzümü", "https://bengurme.com/products/uzum", target="100")
        offers = [OfferResult("Kan üzümü 500 g", Decimal("80"), url=rule.url), OfferResult("Kan üzümü 1 kg", Decimal("150"), url=rule.url + "?v=2")]
        with patch.object(BenGurmeProvider, "read",
                          side_effect=reader(OutOfStockHermesError("Ben Gurme ürünü stokta değil.", "Kan üzümü", rule.url), offers)):
            state = self.run_cycle(config([rule]))
            self.assertTrue(state[key(rule)]["last_out_of_stock_at"])
            state = self.run_later(config([rule]))
        self.assertEqual(self.titles_sent(), ["Ben Gurme stok alarmı"])
        self.assertNotIn("last_out_of_stock_at", state[key(rule)])
        self.assertEqual(len(state[key(rule)]["offer_keys"]), 2)
        self.assertEqual(len(self.published_rows()), 2)

    def test_variant_url_change_preserves_alert_suppression_and_history(self):
        rule = watch(url=AMAZON, target="100000", include_variations=True)
        self.data.write_state({key(rule): {"offer_keys": ["existing-offer"]},
                               "existing-offer": {"url": AMAZON + "?th=1", "is_warehouse": True, "last_alerted_price": "90000",
                                                  "last_alerted_at": utc_now(), "min_price": "85000", "max_price": "95000"}})
        offers = [OfferResult("iPhone", Decimal("90000"), "Amazon Depo", AMAZON + "?psc=1", True)]
        with patch.object(AmazonProvider, "read", side_effect=reader(offers)):
            state = self.run_cycle(config([rule]))
        self.notify.send.assert_not_called()
        self.assertEqual(state[key(rule)]["offer_keys"], ["existing-offer"])
        self.assertEqual(state["existing-offer"]["min_price"], "85000")


class EmptyAndStockTests(CycleTestCase):
    def test_amazon_high_price_warning_clears_previous_read_error_and_publishes_stock(self):
        rule = watch(url=AMAZON, priority="cycle")
        cfg = config([rule])
        with patch.object(AmazonProvider, "fetch", return_value='<span id="productTitle">iPhone Gümüş</span>'):
            state = self.run_cycle(cfg)
        self.assertTrue(state[key(rule)]["last_error"])
        self.notify.reset_mock()
        html = '<span id="productTitle">iPhone Gümüş</span><div id="buybox">Normalden yüksek fiyat</div>'
        with patch.object(AmazonProvider, "fetch", return_value=html):
            state = self.run_later(cfg, seconds=60)
        self.assertIsNone(state[key(rule)]["last_error"])
        self.assertEqual(self.published_rows(), [])
        self.assertEqual([row["product_url"] for row in self.published_stock()], [AMAZON])
        self.assertIn("Normalden yüksek fiyat", self.published_stock()[0]["reason"])
        self.notify.send.assert_not_called()
        hermes_monitor = monitor(cfg, self.data, self.notify)
        try:
            self.assertEqual(hermes_monitor.diagnostics.active(), [])
        finally:
            hermes_monitor.close()

    def test_explicit_no_results_notice_is_a_normal_stock_row_read_at_most_every_five_minutes(self):
        rule = watch("Juo 240W", "https://www.amazon.com.tr/s?k=juo+240w&i=warehouse-deals")
        error = EmptySearchResultsHermesError("Aranan ürün bulunamadı: Amazon Depo içinde juo 240w için sonuç bulunamadı",
                                              no_results_notice=True)
        with patch.object(AmazonProvider, "read", side_effect=reader(error)) as read:
            self.run_cycle(config([rule]))
            state = self.run_later(config([rule]), seconds=60)
        read.assert_called_once()
        self.notify.send.assert_not_called()
        self.assertIsNone(state[key(rule)]["last_error"])
        self.assertEqual(len(self.published_stock()), 1)
        self.assertIn("sonuç bulunamadı", self.published_stock()[0]["reason"])

    def test_deferred_stock_row_keeps_its_last_classification(self):
        rule = watch(url=AMAZON, priority="60m")
        with patch.object(AmazonProvider, "read", side_effect=reader(OutOfStockHermesError("Stokta yok", "iPhone", AMAZON))) as read:
            self.run_cycle(config([rule]), times=2)
        read.assert_called_once()
        self.assertEqual([row["product_url"] for row in self.published_stock()], [AMAZON])

    def test_unavailable_family_waits_until_its_next_probe_unless_edited(self):
        rule = watch(url=AMAZON)
        retry_after = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()

        def unavailable(rule, ctx, outcome):
            outcome.retry_after = retry_after
            raise OutOfStockHermesError("Stokta yok", "iPhone", AMAZON)

        with patch.object(AmazonProvider, "read", side_effect=unavailable) as read:
            self.run_cycle(config([rule]))
            state = self.run_later(config([rule]), seconds=60)
            read.assert_called_once()
            self.assertEqual(state[key(rule)]["amazon_no_offer_retry_after"], retry_after)
            self.assertEqual(len(self.published_stock()), 1)
            rule.check_now_token = "edited"
            self.run_cycle(config([rule]))
        self.assertEqual(read.call_count, 2)

    def test_empty_offer_keys_never_restore_a_legacy_price(self):
        rule = watch(url=AMAZON)
        self.assertEqual(summary.cached_summary_rows(rule, "watch", {"watch": {"offer_keys": [], "last_price": "100",
                                                                               "last_checked_at": utc_now()}}, "Amazon"), [])

    def test_normal_empty_result_is_not_an_operational_error(self):
        self.assertTrue(isinstance(EmptySearchResultsHermesError("x"), HermesError))
        rule = watch("Arama", "https://www.amazon.com.tr/s?k=test")
        with patch.object(AmazonProvider, "read", side_effect=reader(EmptySearchResultsHermesError("Boş sonuç"))):
            state = self.run_cycle(config([rule]))
        self.assertIsNone(state[key(rule)]["last_error"])
        self.notify.send.assert_not_called()


class ProtectionGuardTests(CycleTestCase):
    SITE = state_ops.site_guard_key("amazon")

    def test_guard_pauses_5_10_20_then_30_minutes_and_clears(self):
        state = {}
        error = BotProtectionHermesError("Amazon captcha")
        now = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
        waited = []
        for _ in range(5):
            with patch.object(state_ops, "local_now", return_value=now):
                state_ops.note_guard(state, self.SITE, "test", error)
                waited.append(state_ops.guard_remaining_seconds(state, self.SITE))
                self.assertEqual(state_ops.guard_remaining_seconds(state, "site:other"), 0)
            now += timedelta(seconds=waited[-1] + 1)
        self.assertEqual(waited, [5 * 60, 10 * 60, 20 * 60, 30 * 60, 30 * 60])
        state_ops.clear_guard(state, self.SITE)
        self.assertNotIn(self.SITE, state["_meta"]["amazon_protection"])

    def test_guards_of_older_versions_are_forgotten(self):
        state = {}
        state_ops.note_guard(state, "amazon-a", "old", BotProtectionHermesError("captcha"))
        state_ops.note_guard(state, self.SITE, "new", BotProtectionHermesError("captcha"))
        state_ops.drop_watch_guards(state)
        self.assertEqual(list(state["_meta"]["amazon_protection"]), [self.SITE])

    def test_http_503_from_a_search_pauses_the_whole_site(self):
        response = requests.Response()
        response.status_code = 503
        error = requests.HTTPError("503 Server Error", response=response)
        rule = watch("Hue", "https://www.amazon.com.tr/s?k=hue")
        with patch.object(AmazonProvider, "read", side_effect=reader(error)) as read:
            state = self.run_cycle(config([rule]))
            self.assertEqual(state["_meta"]["amazon_protection"][self.SITE]["kind"], "http_503")
            self.run_later(config([rule]), seconds=120)
        read.assert_called_once()

    def test_one_blocked_page_pauses_the_whole_site_and_keeps_its_last_rows(self):
        first = watch("Bir", "https://www.amazon.com.tr/dp/B000000001", target="100000")
        second = watch("İki", "https://www.amazon.com.tr/dp/B000000002", target="100000")
        good = [OfferResult("Apple iPhone 17", Decimal("90000"), "Amazon.com.tr", first.url)]
        other = [OfferResult("Apple Watch Ultra", Decimal("80000"), "Amazon.com.tr", second.url)]

        def amazon_read(rule, ctx, outcome):
            return good if rule is first else other

        with patch.object(AmazonProvider, "read", side_effect=amazon_read):
            self.run_cycle(config([first, second]))
        self.assertEqual(len(self.published_rows()), 2)
        reads = []

        def amazon_read_blocked(rule, ctx, outcome):
            reads.append(rule.name)
            if rule is first:
                raise BotProtectionHermesError("Amazon captcha")
            return other

        with patch.object(AmazonProvider, "read", side_effect=amazon_read_blocked):
            state = self.run_later(config([first, second]))
        # 3.6.0: a block marks the visitor, so the first block pauses all of Amazon; both keep their last rows.
        self.assertEqual(reads, ["Bir"])
        self.assertEqual(state_ops.guard_remaining_seconds(state, self.SITE) > 0, True)
        self.assertEqual(len(self.published_rows()), 2)
        self.assertIn("captcha", state[key(first)]["last_error"])
        self.assertTrue(state[key(first)]["offer_keys"])

    def test_a_request_held_back_by_the_client_does_not_climb_the_ladder(self):
        # 2026-10-05: after the 3.6.0 restart the old 6-minute pause ended before the client's
        # 15-minute hold, and the held-back request climbed the ladder to 60 minutes.
        state = {}
        now = datetime(2026, 10, 5, 0, 17, tzinfo=timezone.utc)
        with patch.object(state_ops, "local_now", return_value=now):
            state_ops.note_guard(state, self.SITE, "Bir", BotProtectionHermesError("captcha"))
        now += timedelta(minutes=16)
        held = BotProtectionHermesError("molada", challenge_reason="mola", hold_seconds=2 * 60)
        with patch.object(state_ops, "local_now", return_value=now):
            state_ops.note_guard(state, self.SITE, "İki", held)
            self.assertEqual(state_ops.guard_remaining_seconds(state, self.SITE), 2 * 60)
        self.assertEqual(state["_meta"]["amazon_protection"][self.SITE]["consecutive_blocks"], 1)
        # The probe after the hold that fails climbs one step from there: 10 minutes.
        now += timedelta(minutes=3)
        with patch.object(state_ops, "local_now", return_value=now):
            state_ops.note_guard(state, self.SITE, "Üç", BotProtectionHermesError("captcha"))
            self.assertEqual(state_ops.guard_remaining_seconds(state, self.SITE), 10 * 60)

    def test_a_read_that_answers_during_the_pause_does_not_end_it(self):
        state = {}
        state_ops.note_guard(state, self.SITE, "Bir", BotProtectionHermesError("captcha"))
        state_ops.clear_guard(state, self.SITE)
        self.assertGreater(state_ops.guard_remaining_seconds(state, self.SITE), 0)
        # Another block during the pause neither extends it nor climbs the ladder.
        before = dict(state["_meta"]["amazon_protection"][self.SITE])
        state_ops.note_guard(state, self.SITE, "İki", BotProtectionHermesError("captcha"))
        self.assertEqual(state["_meta"]["amazon_protection"][self.SITE], before)

    def test_a_site_wide_block_keeps_the_blocked_watchs_last_rows_too(self):
        rule = watch("Bir", "https://www.amazon.com.tr/dp/B000000001", target="100000")
        good = [OfferResult("Apple iPhone 17", Decimal("90000"), "Amazon.com.tr", rule.url)]
        with patch.object(AmazonProvider, "read", side_effect=reader(good)):
            self.run_cycle(config([rule]))
        with patch.object(AmazonProvider, "read", side_effect=reader(BotProtectionHermesError("Amazon captcha"))):
            state = self.run_later(config([rule]))
        self.assertIn(self.SITE, state["_meta"]["amazon_protection"])
        self.assertEqual(len(self.published_rows()), 1)

    def test_a_block_stops_every_other_amazon_watch_in_the_same_cycle(self):
        first = watch("Bir", "https://www.amazon.com.tr/s?k=bir")
        second = watch("İki", "https://www.amazon.com.tr/s?k=iki")
        with patch.object(AmazonProvider, "read", side_effect=reader(BotProtectionHermesError("Amazon captcha"))) as read:
            state = self.run_cycle(config([first, second]))
        read.assert_called_once()
        self.assertIn(self.SITE, state["_meta"]["amazon_protection"])
        self.assertTrue(any("erişim molasında" in line for line in LOG_LINES))

    def test_paused_site_keeps_the_last_price_and_recovers_after_expiry(self):
        rule = watch(url=AMAZON, target="100000")
        state = {key(rule): {"last_price": "90000", "url": AMAZON,
                             "last_checked_at": (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()}}
        state_ops.note_guard(state, self.SITE, "iPhone", BotProtectionHermesError("Amazon captcha"))
        self.data.write_state(state)
        offers = [OfferResult("iPhone", Decimal("120000"), "Amazon.com.tr", AMAZON)]
        with patch.object(AmazonProvider, "read", side_effect=reader(offers)) as read:
            state = self.run_cycle(config([rule]))
            read.assert_not_called()
            state["_meta"]["amazon_protection"][self.SITE]["retry_after"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            self.data.write_state(state)
            state = self.run_cycle(config([rule]))
        read.assert_called_once()
        self.assertEqual(len(self.published_rows()), 1)
        self.assertNotIn(self.SITE, state["_meta"]["amazon_protection"])

    def test_after_the_pause_the_first_read_is_the_probe_and_a_failed_probe_climbs_a_step(self):
        rule = watch("Juo", "https://www.amazon.com.tr/s?k=Juo")
        state = {}
        state_ops.note_guard(state, self.SITE, "Juo", BotProtectionHermesError("Amazon captcha"))
        state["_meta"]["amazon_protection"][self.SITE]["retry_after"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        self.data.write_state(state)
        with patch.object(AmazonProvider, "read", side_effect=reader(BotProtectionHermesError("Amazon captcha"))) as read:
            state = self.run_cycle(config([rule]), times=2)
        read.assert_called_once()
        guard = state["_meta"]["amazon_protection"][self.SITE]
        self.assertEqual(guard["consecutive_blocks"], 2)
        self.assertGreater(state_ops.guard_remaining_seconds(state, self.SITE), 5 * 60)

    def test_a_probe_that_finds_nothing_ends_the_pause(self):
        rule = watch("Juo", "https://www.amazon.com.tr/s?k=Juo")
        state = {}
        state_ops.note_guard(state, self.SITE, "Juo", BotProtectionHermesError("Amazon captcha"))
        state["_meta"]["amazon_protection"][self.SITE]["retry_after"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        self.data.write_state(state)
        with patch.object(AmazonProvider, "read", side_effect=reader(EmptySearchResultsHermesError("Boş sonuç"))):
            state = self.run_cycle(config([rule]))
        self.assertNotIn(self.SITE, state["_meta"]["amazon_protection"])
        self.assertIsNone(state[key(rule)]["last_error"])

    def test_partial_family_keeps_its_offer_while_paused(self):
        rule = watch(url=AMAZON, target="100000", include_variations=True)

        def partial(rule, ctx, outcome):
            yield OfferResult("iPhone Gümüş", Decimal("120000"), "Amazon.com.tr", rule.url)
            outcome.blocked = BotProtectionHermesError("Amazon captcha")

        with patch.object(AmazonProvider, "read", side_effect=partial) as read:
            state = self.run_cycle(config([rule]))
            self.assertEqual(len(self.published_rows()), 1)
            self.assertGreater(state_ops.guard_remaining_seconds(state, self.SITE), 0)
            self.run_later(config([rule]), seconds=120)
        read.assert_called_once()
        self.assertEqual(len(self.published_rows()), 1)

    def test_other_sites_never_receive_a_guard(self):
        rule = watch("Çanta", "https://nordbron.com/canta")
        with patch.object(NordbronProvider, "read", side_effect=reader(BotProtectionHermesError("Nordbron captcha"))) as read:
            self.run_cycle(config([rule]))
            state = self.run_later(config([rule]))
        self.assertEqual(read.call_count, 2)
        self.assertNotIn(key(rule), state["_meta"].get("amazon_protection", {}))


class DepoLaneTests(CycleTestCase):
    """Amazon's single-page products are read on their own thread beside the variant families (3.9)."""

    def setUp(self):
        super().setUp()
        self.sweep_rule = watch("Tarama", "https://www.amazon.com.tr/dp/B000000002", target="110000", include_variations=True)
        self.quick_rule = watch("Hızlı", "https://www.amazon.com.tr/dp/B000000003", target="110000")

    def prepared_monitor(self):
        hermes_monitor = monitor(config([self.sweep_rule, self.quick_rule], interval_seconds=0), self.data, self.notify)
        provider = hermes_monitor.providers["amazon"]
        # The quick watch was read a long time ago, in an earlier round.
        provider.rhythms[provider._rhythm_key(self.quick_rule)] = WatchRhythm(main_at=time.monotonic() - 500)
        return hermes_monitor, provider

    def test_a_red_product_is_read_once_per_round_beside_a_long_family_sweep(self):
        hermes_monitor, _provider = self.prepared_monitor()
        release = threading.Event()
        reads, threads = [], {}

        def iter_product(_self, rule, ctx, outcome):
            threads[rule.name] = threading.current_thread().name
            if rule is self.sweep_rule:
                release.wait(10)
                time.sleep(0.4)  # the Depo lane keeps looking meanwhile; it must not read the product again
                return [OfferResult("Apple Watch Ultra", Decimal("80000"), "Amazon.com.tr", rule.url)]
            reads.append(rule.name)
            release.set()
            return [OfferResult("Apple iPhone 17", Decimal("90000"), "Amazon.com.tr", rule.url)]

        try:
            with patch.object(AmazonProvider, "iter_product", iter_product):
                hermes_monitor.run_cycle()
        finally:
            hermes_monitor.close()
        self.assertEqual(reads, ["Hızlı"])
        self.assertEqual(threads["Tarama"], "hermes-amazon-tarama")
        self.assertEqual(threads["Hızlı"], "hermes-amazon-depo")

    def test_the_depo_lane_leaves_watches_that_need_a_sweep_to_the_sweep_lane(self):
        hermes_monitor, _provider = self.prepared_monitor()
        reads = []

        def amazon_read(rule, ctx, outcome):
            reads.append((rule.name, threading.current_thread().name))
            return [OfferResult("Apple iPhone 17", Decimal("90000"), "Amazon.com.tr", rule.url)]

        try:
            with patch.object(AmazonProvider, "read", side_effect=amazon_read):
                hermes_monitor.run_cycle()
        finally:
            hermes_monitor.close()
        sweeps = [name for name, thread in reads if thread == "hermes-amazon-tarama"]
        self.assertEqual(sweeps, ["Tarama"])
        self.assertTrue(all(thread == "hermes-amazon-depo" for name, thread in reads if name == "Hızlı"))

    def test_both_lanes_stop_while_the_site_is_paused(self):
        hermes_monitor, _provider = self.prepared_monitor()
        state = {}
        state_ops.note_guard(state, state_ops.site_guard_key("amazon"), "iPhone", BotProtectionHermesError("Amazon captcha"))
        self.data.write_state(state)
        try:
            with patch.object(AmazonProvider, "read", side_effect=reader([])) as read:
                hermes_monitor.run_cycle()
        finally:
            hermes_monitor.close()
        read.assert_not_called()

    def test_a_block_on_one_lane_pauses_the_other_too(self):
        hermes_monitor, _provider = self.prepared_monitor()
        release = threading.Event()

        def amazon_read(rule, ctx, outcome):
            if rule is self.quick_rule:
                release.set()
                raise BotProtectionHermesError("Amazon captcha")
            release.wait(5)
            return [OfferResult("Apple Watch Ultra", Decimal("80000"), "Amazon.com.tr", rule.url)]

        try:
            with patch.object(AmazonProvider, "read", side_effect=amazon_read):
                hermes_monitor.run_cycle()
            state = self.data.state()
        finally:
            hermes_monitor.close()
        self.assertIn(state_ops.site_guard_key("amazon"), state["_meta"]["amazon_protection"])

    def test_a_paused_site_keeps_the_rows_of_the_watches_only_the_depo_lane_reads(self):
        rule = watch("Tek", "https://www.amazon.com.tr/dp/B000000004", target="100000")
        offer = [OfferResult("Apple iPhone 17", Decimal("90000"), "Amazon.com.tr", rule.url)]
        with patch.object(AmazonProvider, "read", side_effect=reader(offer)):
            self.run_cycle(config([rule], interval_seconds=0))
        self.assertEqual(len(self.published_rows()), 1)
        state = self.data.state()
        state_ops.note_guard(state, state_ops.site_guard_key("amazon"), "x", BotProtectionHermesError("Amazon captcha"))
        self.data.write_state(state)
        with patch.object(AmazonProvider, "read", side_effect=reader([])) as read:
            self.run_cycle(config([rule], interval_seconds=0))
        read.assert_not_called()
        self.assertEqual(len(self.published_rows()), 1)

    def test_the_sweep_lane_waits_while_the_depo_lane_is_reading_the_same_watch(self):
        hermes_monitor, _provider = self.prepared_monitor()
        asked = []

        def busy_twice(_self, watch):
            asked.append(watch.name)
            return len(asked) <= 2

        try:
            with (patch.object(AmazonProvider, "is_watch_busy", busy_twice),
                  patch.object(AmazonProvider, "read", side_effect=reader([OfferResult("A", Decimal("1"), None, self.sweep_rule.url)])) as read):
                hermes_monitor.run_cycle()
        finally:
            hermes_monitor.close()
        # Asked twice (busy), then a third time (free) before the read began.
        self.assertEqual(asked[:3], ["Tarama"] * 3)
        self.assertTrue(read.called)

    def test_a_site_without_a_depo_lane_keeps_its_single_queue(self):
        rule = watch("Çanta", "https://nordbron.com/canta")
        names = []

        def nordbron_read(rule, ctx, outcome):
            names.append(threading.current_thread().name)
            return [OfferResult("Çanta", Decimal("100"), None, rule.url)]

        with patch.object(NordbronProvider, "read", side_effect=nordbron_read):
            self.run_cycle(config([rule]))
        self.assertEqual(names, ["hermes-nordbron"])


class ReplayedOfferTests(CycleTestCase):
    """Offers a provider replays from its memory are shown, but are not new readings."""

    def test_replayed_offer_keeps_its_read_time_and_is_neither_a_price_point_nor_an_alert(self):
        rule = watch(url=AMAZON, target="100000", include_variations=True)
        fresh = OfferResult("Apple iPhone 17", Decimal("90000"), "Amazon.com.tr", AMAZON)
        replayed = OfferResult("Apple Watch Ultra", Decimal("80000"), "Amazon.com.tr", AMAZON + "2",
                               checked_at="2026-10-03T10:00:00+00:00")
        with patch.object(AmazonProvider, "read", side_effect=reader([fresh, replayed])):
            state = self.run_cycle(config([rule]))
        entries = {entry["url"]: entry for entry in state.values() if isinstance(entry, dict) and entry.get("url")}
        self.assertEqual(set(entries), {AMAZON, AMAZON + "2"})
        self.assertEqual(entries[AMAZON + "2"]["last_price_checked_at"], "2026-10-03T10:00:00+00:00")
        self.assertNotEqual(entries[AMAZON]["last_price_checked_at"], "2026-10-03T10:00:00+00:00")
        # The summary shows the replayed offer with the time it was really read.
        self.assertEqual([row["price_checked_at"] for row in self.published_rows()], ["2026-10-03T10:00:00+00:00"])
        # Only the fresh offer notified; the replayed one is below target too but was not read now.
        self.notify.send.assert_called_once()
        self.assertIn("Apple iPhone 17", self.notify.send.call_args.args[1])

    def test_replayed_offer_adds_no_price_history_point(self):
        rule = watch(url=AMAZON, target="100000", include_variations=True)
        fresh = OfferResult("Apple iPhone 17", Decimal("90000"), "Amazon.com.tr", AMAZON)
        replayed = OfferResult("Apple Watch Ultra", Decimal("80000"), "Amazon.com.tr", AMAZON + "2",
                               checked_at="2026-10-03T10:00:00+00:00")
        hermes_monitor = monitor(config([rule]), self.data, self.notify)
        try:
            with patch.object(AmazonProvider, "read", side_effect=reader([fresh, replayed])):
                with patch.object(hermes_monitor.history, "price_statement", wraps=hermes_monitor.history.price_statement) as record_price:
                    hermes_monitor.run_cycle()
        finally:
            hermes_monitor.close()
        self.assertEqual(record_price.call_count, 1)

    def test_watch_not_due_for_the_provider_keeps_its_last_rows_and_is_not_read(self):
        rule = watch(url=AMAZON, target="100000")
        hermes_monitor = monitor(config([rule]), self.data, self.notify)
        try:
            with patch.object(AmazonProvider, "read", side_effect=reader([OfferResult("iPhone", Decimal("90000"), "Amazon.com.tr", AMAZON)])) as read:
                hermes_monitor.run_cycle()
                self.assertEqual(read.call_count, 1)
                with patch.object(AmazonProvider, "read_due", return_value=False):
                    hermes_monitor.run_cycle()
                self.assertEqual(read.call_count, 1)
        finally:
            hermes_monitor.close()
        self.assertEqual(len(self.published_rows()), 1)


class ReadOrderTests(CycleTestCase):
    def test_quick_reads_come_before_long_ones_inside_a_priority_tier(self):
        long_read = watch("Uzun", "https://www.amazon.com.tr/dp/B000000001", include_variations=True)
        quick = watch("Hızlı", "https://www.amazon.com.tr/dp/B000000002", include_variations=True)
        medium_quick = watch("Orta", "https://www.amazon.com.tr/dp/B000000003", priority="60m")
        ranks = {long_read.url: 1, quick.url: 0, medium_quick.url: 0}
        ordered = scheduling.priority_order([medium_quick, long_read, quick], lambda rule: ranks[rule.url])
        self.assertEqual([rule.name for rule in ordered], ["Hızlı", "Uzun", "Orta"])


class SchedulingTests(CycleTestCase):
    def test_priorities_use_the_cycle_one_hour_and_three_hours(self):
        now = datetime.now(timezone.utc)
        checked = {"last_checked_at": now.isoformat()}
        high, medium, low = (watch(name, f"https://www.amazon.com.tr/dp/B00000000{i}", priority=name)
                             for i, name in enumerate(("cycle", "60m", "3h"), start=1))
        cases = ((timedelta(seconds=59), high, False), (timedelta(seconds=60), high, True),
                 (timedelta(minutes=59), medium, False), (timedelta(hours=2, minutes=59), low, False),
                 (timedelta(hours=1), medium, True), (timedelta(hours=1), low, False), (timedelta(hours=3), low, True))
        for elapsed, rule, due in cases:
            with self.subTest(elapsed=elapsed, priority=rule.priority), patch.object(scheduling, "local_now", return_value=now + elapsed):
                self.assertEqual(scheduling.watch_check_due(rule, checked, 60), due)

    def test_each_site_reads_high_priority_before_medium_and_low(self):
        rules = [watch(p, f"https://www.amazon.com.tr/dp/B00000000{i}", priority=p)
                 for i, p in enumerate(("3h", "cycle", "60m", "cycle"), start=1)]
        self.assertEqual([rule.priority for rule in scheduling.priority_order(rules)], ["cycle", "cycle", "60m", "3h"])
        self.assertEqual([rule.url for rule in scheduling.priority_order(rules)][:2], [rules[1].url, rules[3].url])

    def test_deferred_medium_and_low_watches_keep_their_last_prices(self):
        now = datetime.now(timezone.utc)
        medium = watch("Orta öncelik", "https://nordbron.com/orta", target="150", priority="60m")
        low = watch("Düşük öncelik", "https://nordbron.com/dusuk", target="250", priority="3h")
        high = watch("Yüksek öncelik", "https://nordbron.com/yuksek", target="350", priority="cycle")
        state = {}
        for rule, price in ((medium, "120"), (low, "220")):
            offer_key = f"cached-{rule.priority}"
            state[key(rule)] = {"offer_keys": [offer_key], "last_checked_at": now.isoformat()}
            state[offer_key] = {"last_price": price, "min_price": price, "max_price": price, "title": f"Önceden {rule.name}",
                                "url": rule.url, "configured_url": rule.url, "site": rule.site, "priority": rule.priority,
                                "last_checked_at": now.isoformat()}
        self.data.write_state(state)
        with patch.object(NordbronProvider, "read", side_effect=reader([OfferResult("Yeni fırsat", Decimal("300"), url=high.url)])) as read:
            self.run_cycle(config([medium, low, high], interval_seconds=60))
        read.assert_called_once()
        rows = {row["priority"]: row for row in self.published_rows()}
        self.assertEqual({p: rows[p]["price"] for p in rows}, {"60m": "120 TL", "3h": "220 TL", "cycle": "300 TL"})
        self.assertEqual(rows["60m"]["price_checked_at"], now.isoformat())
        coverage = next(line for line in LOG_LINES if "Çevrim öncelik kapsamı:" in line)
        self.assertIn("Her çevrim=1 başladı, 1 sırası geldi, 0 ertelendi", coverage)
        self.assertIn("60 dk=0 başladı, 0 sırası geldi, 1 ertelendi", coverage)

    def test_stopping_hermes_ends_the_cycle_between_watches(self):
        rules = [watch(f"Ürün {i}", f"https://nordbron.com/{i}") for i in range(3)]
        hermes_monitor = monitor(config(rules), self.data, self.notify)
        calls = []

        def read(rule, ctx, outcome):
            calls.append(rule)
            hermes_monitor.should_stop = lambda: True
            return [OfferResult(rule.name, Decimal("1"), url=rule.url)]

        with patch.object(NordbronProvider, "read", side_effect=read):
            hermes_monitor.run_cycle()
        hermes_monitor.close()
        self.assertEqual(len(calls), 1)
        self.assertIn(key(rules[0]), self.data.state())


class SiteQueueTests(CycleTestCase):
    def test_a_slow_amazon_read_does_not_delay_other_sites(self):
        amazon = watch(url=AMAZON)
        nordbron = watch("Çanta", "https://nordbron.com/canta")
        other_site_done = threading.Event()

        def slow_amazon(rule, ctx, outcome):
            # Sequential reading would deadlock here: Nordbron could never run.
            self.assertTrue(other_site_done.wait(5), "Nordbron waited for Amazon")
            return [OfferResult("iPhone", Decimal("2000"), url=rule.url)]

        def nordbron_read(rule, ctx, outcome):
            other_site_done.set()
            return [OfferResult("Çanta", Decimal("2000"), url=rule.url)]

        with (patch.object(AmazonProvider, "read", side_effect=slow_amazon),
              patch.object(NordbronProvider, "read", side_effect=nordbron_read)):
            state = self.run_cycle(config([amazon, nordbron]))
        self.assertEqual(len(self.published_rows()), 2)
        self.assertTrue(state[key(amazon)]["offer_keys"] and state[key(nordbron)]["offer_keys"])

    def test_one_site_keeps_a_single_sequential_queue(self):
        rules = [watch(f"Ürün {i}", f"https://www.amazon.com.tr/dp/B00000000{i}") for i in range(1, 5)]
        active, peak = [0], [0]
        lock = threading.Lock()

        def read(rule, ctx, outcome):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.02)
            with lock:
                active[0] -= 1
            return [OfferResult(rule.name, Decimal("2000"), url=rule.url)]

        with patch.object(AmazonProvider, "read", side_effect=read):
            self.run_cycle(config(rules))
        self.assertEqual(peak[0], 1)
        self.assertEqual(len(self.published_rows()), 4)

    def test_parallel_sites_record_every_offer_and_notification(self):
        rules = [watch(f"Ürün {site}{i}", url, target="1000")
                 for i in range(3)
                 for site, url in (("a", f"https://www.amazon.com.tr/dp/B00000000{i}"), ("n", f"https://nordbron.com/{i}"),
                                   ("t", f"https://www.trendyol.com/x-p-{i}"), ("z", f"https://www.zara.com/tr/tr/x-p0{i}.html"))]

        def read(rule, ctx, outcome):
            time.sleep(0.005)
            return [OfferResult(rule.name, Decimal("900"), url=rule.url)]

        from hermes.providers.trendyol import TrendyolProvider
        from hermes.providers.zara import ZaraProvider
        with (patch.object(AmazonProvider, "read", side_effect=read), patch.object(NordbronProvider, "read", side_effect=read),
              patch.object(TrendyolProvider, "read", side_effect=read), patch.object(ZaraProvider, "read", side_effect=read)):
            state = self.run_cycle(config(rules))
        self.assertEqual(len(self.published_rows()), 12)
        self.assertEqual(self.notify.send.call_count, 12)
        self.assertTrue(all(state[key(rule)]["offer_keys"] for rule in rules))
        self.assertEqual(sum(1 for entry in state.values() if isinstance(entry, dict) and entry.get("last_alerted_price")), 12)

    def test_an_unexpected_error_in_a_site_queue_reaches_the_cycle(self):
        rule = watch("Çanta", "https://nordbron.com/canta")
        hermes_monitor = monitor(config([rule]), self.data, self.notify)
        with (patch.object(NordbronProvider, "read", side_effect=reader([OfferResult("x", Decimal("1"))])),
              patch.object(hermes_monitor.results, "_record_success", side_effect=OSError("disk full")),
              patch.object(hermes_monitor.results, "_record_failure", side_effect=OSError("disk full"))):
            with self.assertRaises(OSError):
                hermes_monitor.run_cycle()
        hermes_monitor.close()


class RequestSpacingTests(CycleTestCase):
    def test_spacing_waits_only_for_the_rest_of_the_gap(self):
        from hermes.providers.base import RequestSpacing

        clock, sleeps = [100.0], []

        def sleep(seconds):
            sleeps.append(round(seconds, 2))
            clock[0] += seconds

        spacing = RequestSpacing(5, sleep=sleep, clock=lambda: clock[0])
        self.assertEqual(spacing.wait(), 0)  # first request never waits
        clock[0] += 2
        spacing.wait()
        clock[0] += 7
        self.assertEqual(spacing.wait(), 0)  # gap already passed
        self.assertEqual(sleeps, [3.0])

    def test_amazon_client_waits_a_random_decimal_delay_before_every_network_request_but_not_cached_pages(self):
        from hermes.providers.amazon import client as amazon_client
        from hermes.providers.amazon.client import AmazonClient

        sleeps = []
        with AmazonClient(delay_range=(1, 4), sleep=sleeps.append, clock=lambda: 0.0) as amazon, \
                patch.object(amazon_client, "curl_requests", None), \
                patch.object(amazon.access, "gap_multiplier", return_value=1.0), \
                patch.object(amazon, "_http_read", return_value="<html>Amazon</html>"):
            cache = {}
            for _ in range(30):
                cache.clear()
                amazon.fetch(AMAZON, 10, cache=cache)
                amazon.fetch(AMAZON, 10, cache=cache)  # cached: no request, no wait
                amazon.fetch("https://www.amazon.com.tr/gp/offer-listing/B000000001?condition=used", 10, cache=cache)
        self.assertEqual(len(sleeps), 60)  # two network requests per round, the cached page never waits
        self.assertTrue(all(1 <= seconds <= 4 for seconds in sleeps))
        self.assertTrue(any(seconds != int(seconds) for seconds in sleeps))  # decimals, not whole seconds
        self.assertGreater(len({round(seconds, 2) for seconds in sleeps}), 20)  # and really random

    def test_the_monitor_passes_the_configured_delay_to_amazon_and_amazon_has_no_fixed_minimum_gap(self):
        from hermes.constants import SITE_MIN_REQUEST_GAP_SECONDS as gaps

        cfg = config([watch("iPhone", AMAZON)])
        cfg.request_delay_min_seconds, cfg.request_delay_max_seconds = 1, 4
        hermes_monitor = monitor(cfg, self.data, self.notify)
        self.assertEqual(hermes_monitor.providers["amazon"].client.delay_range, (1.0, 4.0))
        self.assertNotIn("amazon", gaps)
        hermes_monitor.close()

    def test_other_sites_wait_their_gap_between_watch_reads(self):
        rules = [watch(f"Çanta {i}", f"https://nordbron.com/{i}") for i in range(3)]
        sleeps = []
        hermes_monitor = monitor(config(rules), self.data, self.notify)
        hermes_monitor.sleep = sleeps.append
        with (patch.dict("hermes.monitor.cycle.SITE_MIN_REQUEST_GAP_SECONDS", {"nordbron": 30}),
              patch.object(NordbronProvider, "read", side_effect=lambda rule, *_a: [OfferResult(rule.name, Decimal("1"), url=rule.url)])):
            hermes_monitor.run_cycle()
        hermes_monitor.close()
        self.assertEqual(len(sleeps), 2)  # second and third read wait for the gap
        self.assertTrue(all(25 < seconds <= 30 for seconds in sleeps))


class HomeAssistantTests(CycleTestCase):
    def test_cycle_publishes_sensors_and_an_event_per_opportunity(self):
        bridge = Mock(enabled=True)
        rules = [watch("iPhone", AMAZON, target="1000"), watch("Çanta", "https://nordbron.com/canta", target="100")]
        hermes_monitor = monitor(config(rules), self.data, self.notify)
        hermes_monitor.home_assistant = bridge
        with (patch.object(AmazonProvider, "read", side_effect=reader([OfferResult("iPhone", Decimal("900"), url=AMAZON)])),
              patch.object(NordbronProvider, "read", side_effect=reader([OfferResult("Çanta", Decimal("150"), url=rules[1].url)]))):
            hermes_monitor.run_cycle()
        hermes_monitor.close()
        bridge.publish_opportunity.assert_called_once()
        self.assertEqual(bridge.publish_opportunity.call_args.args[1].price, Decimal("900"))
        rows = bridge.publish_cycle.call_args.args[0]
        self.assertEqual(len(rows), 2)

    def test_home_assistant_failures_never_break_monitoring(self):
        from hermes.homeassistant import HomeAssistantBridge

        rule = watch("iPhone", AMAZON, target="1000")
        hermes_monitor = monitor(config([rule]), self.data, self.notify)
        hermes_monitor.home_assistant = HomeAssistantBridge(token="token")
        with (patch.object(AmazonProvider, "read", side_effect=reader([OfferResult("iPhone", Decimal("900"), url=AMAZON)])),
              patch("hermes.homeassistant.requests.post", side_effect=requests.ConnectionError("down")) as post):
            hermes_monitor.run_cycle()
        hermes_monitor.close()
        self.assertEqual(post.call_count, 4)  # one event + three sensors, all failed quietly
        self.assertEqual(len(self.published_rows()), 1)
        self.notify.send.assert_called_once()


class FilterTests(unittest.TestCase):
    def test_minimum_price_and_comma_separated_exclusions(self):
        rule = prepare_watches([{"name": "Samsung S11", "target_price": 40000, "minimum_price": "10.000",
                                 "exclude_terms": "kılıf, koruyucu, çizilmez, temperli",
                                 "url_1": "https://www.amazon.com.tr/s?k=samsung+s11"}])[0]
        self.assertEqual(rule.minimum_price, Decimal("10000"))
        self.assertEqual(rule.excluded_terms, ["kılıf", "koruyucu", "çizilmez", "temperli"])
        self.assertIn("minimum fiyat filtresi", skipped_offer_reason(rule, OfferResult("Samsung S11", Decimal("1000")), "Samsung S11"))
        self.assertIn("hariç tut filtresi: kılıf", skipped_offer_reason(
            rule, OfferResult("x", Decimal("20000")), "Samsung S11 koruyucu kılıf"))
        self.assertEqual(skipped_offer_reason(rule, OfferResult("x", Decimal("20000")), "Samsung S11 tablet"), "")

    def test_absurd_current_price_does_not_overwrite_history(self):
        bounds = state_ops.sanitized_price_bounds({"last_price": "10448.99", "min_price": "10448.99", "max_price": "12398.40"},
                                                  Decimal("3210448.99"), Decimal("9500"))
        self.assertEqual(bounds, (Decimal("10448.99"), Decimal("12398.40")))


class SummaryAlertTests(CycleTestCase):
    def test_drop_threshold_requires_a_meaningful_loss(self):
        self.assertEqual(alerts.summary_drop_threshold(18), 6)
        self.assertEqual(alerts.summary_drop_threshold(23), 8)

    def test_drop_needs_five_cycles_and_stays_silent_during_captcha_or_503(self):
        rule = watch("Arama", "https://www.amazon.com.tr/s?k=test")
        cfg = config([rule])
        now = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
        for message, status in (("Amazon captcha", None), ("Service Unavailable", 503)):
            with self.subTest(status=status), patch.object(alerts, "local_now", return_value=now):
                notify = notifier()
                state = {key(rule): {"last_error": message, "last_error_status": status},
                         "_meta": {"summary_config_signature": alerts.summary_config_signature(cfg),
                                   "summary_expected_row_count": 20, "summary_drop_consecutive_cycles": 4}}
                for _ in range(6):
                    alerts.maybe_alert_summary_drop(state, [], cfg, notify)
                notify.send.assert_not_called()
                state[key(rule)].update(last_error=None, last_error_status=None)
                for _ in range(4):
                    alerts.maybe_alert_summary_drop(state, [], cfg, notify)
                notify.send.assert_not_called()
                alerts.maybe_alert_summary_drop(state, [], cfg, notify)
                notify.send.assert_called_once()

    def test_drop_warning_respects_quiet_hours(self):
        rule = watch("Arama", "https://www.amazon.com.tr/s?k=test")
        cfg = config([rule])
        state = {"_meta": {"summary_config_signature": alerts.summary_config_signature(cfg),
                           "summary_expected_row_count": 20, "summary_drop_consecutive_cycles": 4}}
        notify = notifier()
        with patch.object(alerts, "local_now", return_value=datetime(2026, 10, 2, 23, tzinfo=timezone.utc)):
            alerts.maybe_alert_summary_drop(state, [], cfg, notify)
        notify.send.assert_not_called()


class SummaryFileTests(CycleTestCase):
    def row(self, title, url, price, target="90", **fields):
        return PriceSummaryRow("Amazon", title, url, Decimal(price), Decimal(target), Decimal(price), Decimal(price), **fields)

    def test_early_save_keeps_the_previous_cycle_duration(self):
        self.data.write_summary({"cycle_duration_seconds": 90, "scan_duration_seconds": 30, "rows": []})
        rows = [self.row("Test ürün", "https://example.com", "100")]
        summary.save_price_summary(self.data.files.summary, rows)
        self.assertEqual((self.data.summary()["cycle_duration_seconds"], self.data.summary()["scan_duration_seconds"]), (90, 30))
        summary.publish_price_summary(self.data.files.summary, rows, cycle_duration_seconds=180, scan_duration_seconds=120)
        self.assertEqual(self.data.summary()["cycle_duration_seconds"], 180)

    def test_incremental_save_keeps_rows_waiting_for_the_cycle(self):
        summary.save_price_summary(self.data.files.summary, [self.row("Önce okunan", "https://example.com/old", "200", "180")])
        summary.save_incremental_summary(self.data.files.summary, [self.row("Bildirim", "https://example.com/fresh", "90", "100")])
        prices = {row["product_url"]: row["price"] for row in self.data.summary()["rows"]}
        self.assertEqual(prices, {"https://example.com/old": "200 TL", "https://example.com/fresh": "90 TL"})

    def test_incremental_save_removes_only_the_failed_watch_rows(self):
        stale = PriceSummaryRow("Nordbron", "Stark", "https://nordbron.com/stark", Decimal("4850"), Decimal("4500"),
                                Decimal("4850"), Decimal("4850"))
        summary.save_price_summary(self.data.files.summary, [stale, self.row("Güncel", "https://example.com/current", "300")])
        summary.save_incremental_summary(self.data.files.summary, [], removed_price_ids={summary.price_row_identity(stale)})
        self.assertEqual([row["product_url"] for row in self.data.summary()["rows"]], ["https://example.com/current"])

    def test_normal_and_warehouse_rows_of_one_asin_stay_separate(self):
        url = "https://www.amazon.com.tr/dp/B0D95QG8W4?th=1"
        normal = self.row("Edifier M60 Siyah", url, "8899", "9000", priority="3h")
        warehouse = self.row("Edifier M60 Siyah", url, "8787.77", "9000", is_warehouse=True)
        summary.save_price_summary(self.data.files.summary, [normal])
        summary.save_incremental_summary(self.data.files.summary, [warehouse])
        rows = self.data.summary()["rows"]
        self.assertEqual(sorted(row["is_warehouse"] for row in rows), [False, True])
        summary.save_incremental_summary(self.data.files.summary, [], removed_price_ids={summary.price_row_identity(warehouse)})
        self.assertEqual([row["is_warehouse"] for row in self.data.summary()["rows"]], [False])

    def test_stock_rows_are_saved_separately(self):
        summary.save_price_summary(self.data.files.summary, [], [StockSummaryRow("Zara", "Polo / M", "https://example.com/zara",
                                                                                 Decimal("1290"), "Zara beden stokta değil: M")])
        payload = self.data.summary()
        self.assertEqual((payload["row_count"], payload["stock_row_count"]), (0, 1))
        self.assertEqual(payload["stock_rows"][0]["reason"], "Zara beden stokta değil: M")

    def test_stock_rows_keep_their_last_check_time(self):
        entry = {"unavailable_variants": [{"product_title": "Polo / M", "product_url": "https://example.com/zara"}],
                 "unavailable_checked_at": "2026-10-07T10:00:00+00:00", "last_checked_at": "2026-10-01T10:00:00+00:00"}
        class W:
            target_price = Decimal("1290")
        row = summary.cached_stock_rows(W, entry, "Zara")[0]
        self.assertEqual(row.checked_at, "2026-10-07T10:00:00+00:00")
        legacy = {**entry, "last_out_of_stock_at": "2026-10-05T10:00:00+00:00"}
        legacy.pop("unavailable_checked_at")
        self.assertEqual(summary.cached_stock_rows(W, legacy, "Zara")[0].checked_at, "2026-10-05T10:00:00+00:00")
        summary.save_price_summary(self.data.files.summary, [], [row])
        self.assertEqual(self.data.summary()["stock_rows"][0]["checked_at"], row.checked_at)

    def test_deduplication_rules(self):
        same = [self.row("Ürün", "https://example.test/product", "100"), self.row("Ürün", "https://example.test/product", "95"),
                self.row("Farklı", "https://example.test/product?color=blue", "96")]
        unique = summary.deduplicate_summary_rows(same)
        self.assertEqual((len(unique), unique[0].price), (2, Decimal("95")))
        cards = [self.row("iPhone", AMAZON, "121499", "100000", tracking_id="a"), self.row("iPhone", AMAZON, "121499", "110000", tracking_id="b")]
        self.assertEqual(len(summary.deduplicate_summary_rows(cards)), 2)
        equivalent = [self.row("Ürün", AMAZON + "?th=1", "100"), self.row("Ürün", "https://www.amazon.com.tr/gp/product/B000000001?smid=A1", "95")]
        self.assertEqual([row.price for row in summary.deduplicate_summary_rows(equivalent)], [Decimal("95")])
        conditions = [self.row("Normal", AMAZON, "100"), self.row("Depo", AMAZON, "80", is_warehouse=True)]
        self.assertEqual(len(summary.deduplicate_summary_rows(conditions)), 2)

    def test_same_offer_from_normal_and_depot_searches_is_merged(self):
        rows = [PriceSummaryRow("Amazon", "Edifier M60 - Siyah (Stok 10)", AMAZON + "?th=1", Decimal("7799"), Decimal("9000"),
                                Decimal("7799"), Decimal("9421"), is_warehouse=True, tracking_id="m60"),
                PriceSummaryRow("Amazon", "Edifier M60 - Siyah", "https://www.amazon.com.tr/dp/B000000002", Decimal("7799"),
                                Decimal("9000"), Decimal("7799"), Decimal("7799"), is_warehouse=True, tracking_id="m60")]
        unique = summary.deduplicate_summary_rows(rows)
        self.assertEqual((len(unique), unique[0].min_price, unique[0].max_price), (1, Decimal("7799"), Decimal("9421")))

    def test_equal_duplicate_keeps_the_latest_read(self):
        older = self.row("Aynı", AMAZON, "90", "100", price_checked_at="2026-09-24T10:00:00+00:00")
        older.min_price = Decimal("80")
        newer = self.row("Aynı", AMAZON, "90", "100", price_checked_at="2026-09-24T11:00:00+00:00")
        newer.max_price = Decimal("100")
        rows = summary.deduplicate_summary_rows([older, newer])
        self.assertEqual((rows[0].price_checked_at, rows[0].min_price, rows[0].max_price),
                         (newer.price_checked_at, Decimal("80"), Decimal("100")))

    def test_price_age_survives_incremental_saves_and_skipped_watches(self):
        checked_at = (datetime.now(timezone.utc) - timedelta(minutes=125)).isoformat()
        rule = watch("Ürün", AMAZON, target="100")
        row = summary.summary_row_from_state(rule, {"last_price": "90", "last_price_checked_at": checked_at}, "Amazon")
        summary.save_price_summary(self.data.files.summary, [row])
        summary.save_incremental_summary(self.data.files.summary, [self.row("Yeni", "https://www.amazon.com.tr/dp/B000000002", "80", "100",
                                                                            price_checked_at=utc_now())])
        old_row = next(item for item in self.data.summary()["rows"] if item["product_url"].endswith("B000000001"))
        self.assertEqual(old_row["price_checked_at"], checked_at)

    def test_variation_watch_rows_share_the_watch_group(self):
        group, label = summary.result_group_for_watch(watch("Tablet", AMAZON + "?th=1", include_variations=True))
        self.assertTrue(group)
        self.assertEqual(label, "Tablet")

    def test_duration_is_formatted_in_minutes(self):
        self.assertEqual(format_duration(75), "1 dk 15 sn")
        self.assertEqual(format_duration(600), "10 dk 0 sn")


class PanelCommandTests(CycleTestCase):
    def test_price_history_reset_keeps_alert_state_and_restarts_ranges(self):
        self.data.write_state({"product_a": {"last_price": "100", "min_price": "80", "max_price": "300", "last_alerted_price": "90"},
                               "_meta": {"keep": "yes"}})
        self.data.write_summary({"rows": [{"price": "100,00", "min_price": "80,00", "max_price": "300,00", "price_range": "80,00 / 300,00"}]})
        self.assertEqual(runner.reset_price_history(self.data.files), 2)
        state = self.data.state()
        self.assertEqual((state["product_a"]["last_price"], state["product_a"]["last_alerted_price"]), ("100", "90"))
        self.assertNotIn("min_price", state["product_a"])
        self.assertEqual(state["_meta"]["keep"], "yes")
        self.assertEqual(self.data.summary()["rows"][0]["price_range"], "100,00 / 100,00")

    def test_notification_reset_makes_every_watch_due(self):
        self.data.write_state({"offer": {"last_alerted_price": "90", "last_alerted_at": utc_now(), "last_price": "90"},
                               "watch": {"last_checked_at": utc_now()}, "_meta": {}})
        self.assertEqual(runner.reset_notifications(self.data.files), 2)
        state = self.data.state()
        self.assertNotIn("last_alerted_price", state["offer"])
        self.assertNotIn("last_checked_at", state["watch"])
        self.assertEqual(state["offer"]["last_price"], "90")

    def test_commands_are_applied_between_cycles_and_can_start_one_now(self):
        rule = watch("Ürün", "https://nordbron.com/x", target="100")
        service = runner.MonitorService(config([rule], interval_seconds=3600), self.data.files, notifier=self.notify)
        cycles = []

        def read(rule, ctx, outcome):
            cycles.append(1)
            if len(cycles) == 1:
                command = service.submit("reset_notifications")
                self.assertFalse(command.done.is_set())  # not applied during a running cycle
            else:
                service.stop()
            return [OfferResult("Ürün", Decimal("90"), url=rule.url)]

        with patch.object(NordbronProvider, "read", side_effect=read):
            service.run()
        self.assertEqual(len(cycles), 2)  # the reset started a new cycle immediately
        self.assertIn(self.notify.send.call_count, (1, 2))  # in-flight delivery may finish; new sends stop
        with service.monitor.diagnostics.db.lock:
            rows = service.monitor.diagnostics.db.connect().execute("SELECT state FROM outbox").fetchall()
        self.assertEqual(len(rows), 2)  # reset generated a distinct durable intent
        self.assertTrue(all(row[0] in {"sent", "pending"} for row in rows))
        self.assertTrue(service.finished)
        self.assertFalse(service.health()[0])

    def test_unexpected_cycle_error_does_not_stop_monitoring(self):
        rule = watch("Ürün", "https://nordbron.com/x")
        service = runner.MonitorService(config([rule], interval_seconds=1), self.data.files, notifier=self.notify)
        runs = []

        def run_cycle(site=None):
            runs.append(1)
            if len(runs) == 1:
                raise OSError("disk full")
            service.stop()

        with patch.object(service.monitor, "run_cycle", side_effect=run_cycle):
            service.run()
        self.assertEqual(len(runs), 2)


if __name__ == "__main__":
    unittest.main()
