"""Release gates for durable delivery, migration, scoped prices and site isolation."""

import json
import sqlite3
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import requests

from support import TempData, config, monitor, notifier, watch
from hermes.app import HermesRuntime
from hermes.config import load_config, options_with_defaults, prepare_watches
from hermes.database import read_snapshot
from hermes.delivery import DeliveryQueue, MAX_ATTEMPTS
from hermes.diagnostics import Diagnostics, runtime_metrics
from hermes.errors import HermesError
from hermes.history import History, read_prices
from hermes.logging_utils import configure_secrets, redact
from hermes.models import OfferResult
from hermes.monitor.runner import MonitorService
from hermes.monitor.state import watch_key
from hermes.providers import nordbron, trendyol, network, beymenclub, bengurme, hm
from hermes.storage import load_json
from hermes.web.settings import apply_settings_operation


class DurableDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.data = TempData()
        self.transport = notifier()
        self.queue = DeliveryQueue(self.data.files.database, self.transport)
        self.state = {"offer": {"last_price": "90", "last_price_checked_at": datetime.now(timezone.utc).isoformat()}}
        self.payload = {"title": "Fiyat", "message": "Ürün", "offer_key": "offer", "price": "90", "target": "100"}

    def tearDown(self):
        self.queue.close()
        self.data.cleanup()

    def row(self):
        with self.queue.db.lock:
            return self.queue.db.connect().execute("SELECT state,attempts FROM outbox").fetchone()

    def enqueue(self):
        return self.queue.enqueue(self.payload, "event", self.state)

    def test_pending_survives_close_and_reopen(self):
        self.enqueue()
        self.queue.db.close()
        self.queue = DeliveryQueue(self.data.files.database, self.transport)
        self.assertTrue(self.queue.deliver_one())
        self.assertEqual(self.row(), ("sent", 0))
        self.transport.send.assert_called_once()

    def test_duplicate_intent_never_sends_twice_after_acceptance(self):
        self.enqueue()
        self.enqueue()
        self.queue.drain()
        self.enqueue()
        self.queue.drain()
        self.transport.send.assert_called_once()

    def test_changed_price_expires_old_notification(self):
        self.enqueue()
        self.state["offer"]["last_price"] = "110"
        self.queue.db.snapshot("state.json", self.state)
        self.queue.drain()
        self.assertEqual(self.row()[0], "expired")
        self.transport.send.assert_not_called()

    def test_failed_read_invalidates_waiting_price(self):
        self.enqueue()
        self.state["offer"]["last_error"] = "Ürün okunamadı"
        self.queue.db.snapshot("state.json", self.state)
        self.queue.drain()
        self.transport.send.assert_not_called()

    def test_stale_observation_expires(self):
        self.state["offer"]["last_price_checked_at"] = (datetime.now(timezone.utc)-timedelta(minutes=6)).isoformat()
        self.enqueue()
        self.queue.drain()
        self.assertEqual(self.row()[0], "expired")

    def test_reset_generation_cancels_old_opportunity(self):
        self.enqueue()
        self.state["_meta"] = {"notification_generation": "new"}
        self.queue.db.snapshot("state.json", self.state)
        self.queue.drain()
        self.transport.send.assert_not_called()

    def test_fresh_observation_can_requeue_expired_intent(self):
        self.state["offer"]["last_error"] = "Geçici hata"
        self.enqueue()
        self.queue.drain()
        self.state["offer"].pop("last_error")
        self.enqueue()
        self.queue.drain()
        self.transport.send.assert_called_once()

    def test_transient_failure_has_a_due_time_and_bounded_retries(self):
        self.transport.send.side_effect = requests.ConnectionError("Bağlantı kesildi")
        self.enqueue()
        self.queue.deliver_one()
        self.assertFalse(self.queue.deliver_one())
        for _ in range(MAX_ATTEMPTS-1):
            self.queue.db.transaction([("UPDATE outbox SET due=0", ())])
            self.queue.deliver_one()
        self.assertEqual(self.row(), ("failed", MAX_ATTEMPTS))
        self.assertFalse(self.queue.deliver_one())

    def test_permanent_rejection_stops_on_first_attempt(self):
        response = requests.Response()
        response.status_code = 400
        self.transport.send.side_effect = requests.HTTPError("Geçersiz anahtar", response=response)
        self.enqueue()
        self.queue.drain()
        self.assertEqual(self.row(), ("failed", 1))
        self.transport.send.assert_called_once()

    def test_successful_manual_recovery_retries_failed_job(self):
        self.transport.send.side_effect = requests.ConnectionError("Kesinti")
        self.queue.send("Telegram", "Mesaj", event_id="telegram:1")
        self.queue.db.transaction([("UPDATE outbox SET attempts=5", ())])
        self.queue.drain()
        self.transport.send.side_effect = None
        self.queue.retry_failed()
        self.queue.drain()
        self.assertEqual(self.row(), ("sent", 0))

    def test_atomic_failure_keeps_snapshot_and_outbox_unchanged(self):
        self.queue.db.snapshot("state.json", {"old": True})
        with self.assertRaises(sqlite3.Error):
            self.queue.enqueue(self.payload, "event", self.state, [("INSERT INTO missing_table VALUES (?)", (1,))])
        self.assertEqual(read_snapshot(self.data.files.database, "state.json"), {"old": True})
        self.assertIsNone(self.row())

    def test_single_consumer_under_concurrent_producers(self):
        producers = [threading.Thread(target=lambda i=i: self.queue.send("Telegram", "Mesaj", event_id=f"message:{i}")) for i in range(20)]
        for thread in producers:
            thread.start()
        for thread in producers:
            thread.join()
        consumers = [threading.Thread(target=self.queue.drain) for _ in range(3)]
        for thread in consumers:
            thread.start()
        for thread in consumers:
            thread.join()
        self.assertEqual(self.transport.send.call_count, 20)

    def test_disabled_transport_does_not_mark_sent(self):
        self.transport.configured = False
        self.enqueue()
        self.queue.drain()
        self.assertEqual(self.row(), ("pending", 0))


class ScopedProductTests(unittest.TestCase):
    def test_recommendation_script_price_is_never_the_product_price(self):
        html = '<h1>Takip edilen ürün</h1><script>recommendations={"price":79.90}</script>'
        for extract in (nordbron.extract_offer, trendyol.extract_offer, network.extract_offer, beymenclub.extract_offer, bengurme.extract_offer):
            with self.subTest(provider=extract.__module__), self.assertRaises(HermesError):
                extract(html)

    def test_recommendation_jsonld_and_visible_price_are_rejected(self):
        html = '''<h1>Ürün</h1><div class="recommendations"><div class="product-detail_price">79,90 TL</div>
        <script type="application/ld+json">{"@type":"Product","name":"Ürün","offers":{"price":79.90}}</script></div>'''
        with self.assertRaises(HermesError):
            nordbron.extract_offer(html)

    def test_unrelated_jsonld_product_does_not_supply_price(self):
        html = '''<h1>Telefon</h1><script type="application/ld+json">{"@type":"Product","name":"Kılıf",
        "offers":{"price":79.90}}</script>'''
        with self.assertRaises(HermesError):
            trendyol.extract_offer(html)

    def test_campaign_in_recommendation_is_ignored(self):
        html = '<h1>Elbise</h1><div class="product-price">999 TL</div><aside class="related"><b>Sepette 79 TL</b></aside>'
        self.assertEqual(network.extract_offer(html).price, Decimal("999"))
        self.assertEqual(beymenclub.extract_offer(html).price, Decimal("999"))

    def test_hm_ambiguous_text_does_not_select_cheapest_price(self):
        self.assertIsNone(hm._fallback_text_price('<h1>Gömlek</h1><span>999 TL</span><span>79 TL</span>'))

    def test_wrong_currency_is_not_a_turkish_lira_offer(self):
        html = '''<h1>Telefon</h1><script type="application/ld+json">{"@type":"Product","name":"Telefon",
        "offers":{"price":90,"priceCurrency":"EUR"}}</script>'''
        with self.assertRaises(HermesError):
            trendyol.extract_offer(html)

    def test_visible_foreign_currency_is_not_assumed_try(self):
        with self.assertRaises(HermesError):
            nordbron.extract_offer('<h1>Ürün</h1><span class="product-detail_price">90 EUR</span>')

    def test_shopify_variant_ids_have_distinct_price_identity(self):
        payload = {"title": "Üzüm", "variants": [{"id": 11, "title": "500 g", "price": 9000, "available": True},
            {"id": 22, "title": "1 kg", "price": 15000, "available": True}]}
        offers = bengurme.extract_offers(json.dumps(payload), "https://bengurme.com/products/uzum")
        self.assertEqual([item.url for item in offers], ["https://bengurme.com/products/uzum?variant=11", "https://bengurme.com/products/uzum?variant=22"])
        self.assertEqual([item.price for item in offers], [Decimal("90"), Decimal("150")])


class RuntimeSafetyTests(unittest.TestCase):
    def setUp(self):
        self.data = TempData()

    def tearDown(self):
        self.data.cleanup()

    def test_legacy_json_state_and_history_are_preserved(self):
        legacy = {"old-offer": {"last_price": "90", "min_price": "80", "max_price": "100",
            "last_alerted_price": "90", "last_price_checked_at": datetime.now(timezone.utc).isoformat()}}
        self.data.files.state.write_text(json.dumps(legacy))
        instance = monitor(config([watch()]), self.data, notifier())
        try:
            self.assertEqual(instance.load_state(), legacy)
            self.assertEqual(read_snapshot(self.data.files.database, "state.json"), legacy)
            self.assertEqual(len(read_prices(self.data.files.database, "old-offer")), 1)
        finally:
            instance.close()

    def test_corrupt_json_recovers_from_authoritative_snapshot(self):
        self.data.write_state({"history": {"min_price": "70"}})
        self.data.files.state.write_text("broken")
        self.assertEqual(load_json(self.data.files.state, {}), {"history": {"min_price": "70"}})

    def test_corrupt_state_without_snapshot_is_never_replaced(self):
        self.data.files.state.write_text("broken")
        with self.assertRaises(RuntimeError):
            load_json(self.data.files.state, {})
        self.assertEqual(self.data.files.state.read_text(), "broken")

    def test_corrupt_database_preserves_panel_and_fails_health(self):
        self.data.files.database.write_bytes(b"broken database")
        runtime = HermesRuntime(config([watch()]), files=self.data.files)
        self.assertIsNone(runtime.service)
        self.assertFalse(runtime.health()[0])
        self.assertEqual(self.data.files.database.read_bytes(), b"broken database")

    def test_invalid_card_does_not_disable_valid_card(self):
        options = {"pushover_user_key": "user", "pushover_api_token": "token", "takip_edilenler": [
            {"name": "Hatalı", "target_price": "xx", "url_1": "https://nordbron.com/x"},
            {"name": "Doğru", "target_price": "100", "url_1": "https://nordbron.com/y"}]}
        result = load_config(options, tolerant=True)
        self.assertEqual([item.name for item in result.watches], ["Doğru"])
        self.assertEqual(len(result.config_errors), 1)
        with self.assertRaises(HermesError):
            load_config(options)

    def test_target_edit_keeps_legacy_tracking_identity(self):
        old = {"takip_edilenler": [{"name": "Ürün", "target_price": "100", "url_1": "https://nordbron.com/x"}]}
        expected = prepare_watches(old["takip_edilenler"])[0].tracking_id
        form = {"operation": ["update_watch"], "update_watch_index": ["0"], "watches_0_name": ["Ürün"],
            "watches_0_target_price": ["200"], "watches_0_url_1": ["https://nordbron.com/x"], "watches_0_active": ["on"]}
        changed, _ = apply_settings_operation(old, form)
        self.assertEqual(prepare_watches(changed["takip_edilenler"])[0].tracking_id, expected)
        self.assertEqual(options_with_defaults(old)["takip_edilenler"][0]["id"], expected)

    def test_incidents_survive_restart_and_close_when_recovered(self):
        diagnostic = Diagnostics(self.data.files.database)
        diagnostic.incident("site", "parse", "Sayfa okunamadı", "Diğer siteler devam ediyor")
        diagnostic.incident("site", "parse", "Sayfa okunamadı", "Diğer siteler devam ediyor")
        diagnostic.db.close()
        diagnostic = Diagnostics(self.data.files.database)
        self.assertEqual(diagnostic.active()[0]["count"], 2)
        diagnostic.recover("site", "Okuma düzeldi")
        self.assertEqual(diagnostic.active(), [])

    def test_secrets_are_removed_before_persistent_diagnostics(self):
        configure_secrets(["synthetic-private-value"])
        Diagnostics(self.data.files.database).incident("test", "parse", "api_hash=hidden /public/private/path synthetic-private-value")
        detail = Diagnostics(self.data.files.database).active()[0]["detail"]
        self.assertNotIn("synthetic-private-value", detail)
        self.assertNotIn("/public/private", detail)
        self.assertNotIn("hidden", redact("token=hidden"))

    def test_metrics_contain_counts_and_no_message_or_url(self):
        History.at(self.data.files.database).record_price("offer", "site", "Ürün", Decimal("90"))
        metrics = runtime_metrics(self.data.files.database)
        self.assertEqual(metrics["price_points"], 1)
        self.assertNotIn("Ürün", json.dumps(metrics))
        self.assertNotIn("http", json.dumps(metrics))

    def test_slow_site_does_not_delay_next_read_of_healthy_site(self):
        fast = watch("Hızlı", "https://www.network.com.tr/x", target="100")
        slow = watch("Yavaş", "https://nordbron.com/x", target="100")
        release = threading.Event()
        started = threading.Event()
        progressed = threading.Event()
        reads = []
        service = MonitorService(config([fast, slow]), self.data.files, notifier=notifier())
        def slow_read(*_):
            started.set()
            release.wait(5)
            return [OfferResult("Yavaş", Decimal("200"), url=slow.url)]
        def fast_read(*_):
            reads.append(time.monotonic())
            if len(reads) >= 2:
                progressed.set()
            return [OfferResult("Hızlı", Decimal("200"), url=fast.url)]
        with patch.object(network.NetworkProvider, "read", side_effect=fast_read), patch.object(nordbron.NordbronProvider, "read", side_effect=slow_read):
            thread = threading.Thread(target=service.run)
            thread.start()
            try:
                self.assertTrue(started.wait(2))
                self.assertTrue(progressed.wait(4))
                self.assertFalse(release.is_set())
                self.assertTrue(service.health()[0])
            finally:
                service.stop()
                release.set()
                thread.join(5)
            self.assertFalse(thread.is_alive())

    def test_async_notification_is_sent_before_blocked_next_variant_finishes(self):
        rule = watch("Ürün", "https://nordbron.com/x", target="100")
        instance = monitor(config([rule]), self.data, notifier())
        accepted = threading.Event()
        instance.delivery.transport.send.side_effect = lambda *_a, **_k: accepted.set()
        def stream(*_):
            yield OfferResult("Ürün", Decimal("90"), url=rule.url)
            self.assertTrue(accepted.wait(2))
            yield OfferResult("Pahalı", Decimal("110"), url=rule.url+"/v")
        instance.delivery.start()
        try:
            with patch.object(nordbron.NordbronProvider, "read", side_effect=stream):
                instance.run_cycle()
            self.assertTrue(accepted.is_set())
            state = load_json(self.data.files.state, {})
            self.assertEqual(state[state[watch_key(rule)]["offer_keys"][0]]["last_alerted_price"], "90")
        finally:
            instance.close()

    def test_failed_atomic_price_write_does_not_leave_suppression(self):
        rule = watch("Ürün", "https://nordbron.com/x", target="100")
        transport = notifier()
        instance = monitor(config([rule]), self.data, transport)
        instance.delivery.db.transaction([("CREATE TRIGGER fail_price BEFORE INSERT ON prices BEGIN SELECT RAISE(ABORT,'test disk failure'); END", ())])
        try:
            with patch.object(nordbron.NordbronProvider, "read", return_value=[OfferResult("Ürün", Decimal("90"), url=rule.url)]):
                instance.run_cycle()
            self.assertEqual(runtime_metrics(self.data.files.database)["outbox"], {})
            self.assertFalse(any(item.get("pending_alert_price") for item in instance._state.values() if isinstance(item, dict)))
            transport.send.assert_not_called()
        finally:
            instance.close()


if __name__ == "__main__":
    unittest.main()
