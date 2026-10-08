"""Configuration, Telegram quick add and the application runtime."""

import asyncio
import json
import threading
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from support import LOG_LINES, TempData, config, watch

from hermes import app as hermes_app
from hermes import config as hermes_config
from hermes.errors import HermesError
from hermes.models import OfferResult
from hermes.monitor import runner
from hermes.providers.nordbron import NordbronProvider
from hermes.storage import load_json, save_json
from hermes.telegram import listener as telegram


class ConfigTests(unittest.TestCase):
    def options(self, **fields):
        base = {"pushover_user_key": "user", "pushover_api_token": "token",
                "takip_edilenler": [{"name": "Test", "target_price": 100, "url_1": "https://www.amazon.com.tr/dp/B000000001"}]}
        base.update(fields)
        return base

    def test_cycle_interval_accepts_one_to_86400_seconds(self):
        for interval in (1, 5, 8, 35):
            self.assertEqual(hermes_config.load_config(self.options(interval_seconds=interval)).interval_seconds, interval)
        with self.assertRaisesRegex(HermesError, "1 ile 86400 arasında"):
            hermes_config.load_config(self.options(interval_seconds=0))

    def test_missing_pushover_or_watches_is_a_clear_error(self):
        with self.assertRaisesRegex(HermesError, "Pushover"):
            hermes_config.load_config(self.options(pushover_api_token=""))
        with self.assertRaisesRegex(HermesError, "En az bir"):
            hermes_config.load_config(self.options(takip_edilenler=[]))

    def test_unsupported_link_is_skipped_without_stopping_the_card(self):
        LOG_LINES.clear()
        watches = hermes_config.prepare_watches([{"name": "iPad", "target_price": 40000, "url_1": "https://www.amazon.com.tr/dp/B000000001",
                                                  "url_2": "https://amzn.eu/d/example"}])
        self.assertEqual([item.site for item in watches], ["amazon"])
        self.assertTrue(any("amzn.eu" in line for line in LOG_LINES))

    def test_inactive_cards_skipped_and_old_or_unknown_priorities_read_every_6_hours(self):
        watches = hermes_config.prepare_watches([
            {"name": "Pasif", "target_price": 1, "url_1": "https://nordbron.com/a", "active": False},
            {"name": "Acil", "target_price": 1, "url_1": "https://nordbron.com/b", "priority": "acil"},
            # Cards saved before 3.12 move to the lowest priority, whatever they were.
            {"name": "Eski yüksek", "target_price": 1, "url_1": "https://nordbron.com/c", "priority": "high"},
            {"name": "Eski orta", "target_price": 1, "url_1": "https://nordbron.com/d", "priority": "medium"},
            {"name": "Önceliksiz", "target_price": 1, "url_1": "https://nordbron.com/e"},
            {"name": "Yeni", "target_price": 1, "url_1": "https://nordbron.com/f", "priority": "30m"}])
        self.assertEqual([(item.name, item.priority) for item in watches],
                         [("Acil", "6h"), ("Eski yüksek", "6h"), ("Eski orta", "6h"), ("Önceliksiz", "6h"), ("Yeni", "30m")])

    def test_minimum_above_target_is_rejected(self):
        with self.assertRaisesRegex(HermesError, "minimum fiyat"):
            hermes_config.prepare_watches([{"name": "x", "target_price": 100, "minimum_price": 200, "url_1": "https://nordbron.com/a"}])

    def test_tracking_card_identity_is_stable_across_releases(self):
        # The identity keys state.json; it must match what 2.x computed.
        card = {"name": "iPhone", "target_price": 100, "size": "", "url_1": "https://www.amazon.com.tr/dp/B000000001"}
        rule = hermes_config.prepare_watches([card])[0]
        self.assertEqual(rule.tracking_id, "tracking_card_iphone_100_https_www_amazon_com_tr_dp_b000000001")


class TelegramQuickAddTests(unittest.TestCase):
    def test_supported_links_are_extracted_and_share_links_resolved(self):
        self.assertEqual(telegram._extract_supported_url("Buna bakar mısın? https://www.amazon.com.tr/dp/B0B2PSDNV1?th=1"),
                         "https://www.amazon.com.tr/dp/B0B2PSDNV1?th=1")
        response = SimpleNamespace(url="https://www.amazon.com.tr/dp/B0B2PSDNV1?th=1", close=lambda: None)
        with patch.object(telegram.requests, "get", return_value=response):
            self.assertEqual(telegram._extract_supported_url("https://amzn.eu/d/example"), response.url)

    def test_target_price_accepts_turkish_formats(self):
        self.assertEqual(telegram._parse_target_price("40.000 TL"), Decimal("40000"))
        self.assertEqual(telegram._parse_target_price("40000"), Decimal("40000"))
        self.assertIsNone(telegram._parse_target_price("fiyat belli değil"))

    def test_quick_add_uses_the_shared_group_and_the_search_query_as_name(self):
        url = "https://www.amazon.com.tr/s?k=edifier+m60"
        with (patch.object(telegram, "read_options", return_value={"gruplar": ["Teknoloji"], "takip_edilenler": []}),
              patch.object(telegram, "save_options_and_restart") as save):
            self.assertEqual(telegram._quick_add_watch(url, Decimal("8700")), telegram.QUICK_ADD_DONE)
        saved = save.call_args.args[0]
        self.assertIn("Paylaşılanlar", saved["gruplar"])
        self.assertEqual(saved["takip_edilenler"][0], {"name": "edifier m60", "group": "Paylaşılanlar", "target_price": 8700.0,
                                                       "url_1": url, "notify_once_in_24H": True, "active": True,
                                                       "priority": "cycle"})

    def test_an_already_watched_link_is_not_added_twice(self):
        url = "https://www.amazon.com.tr/dp/B000000001"
        with (patch.object(telegram, "read_options", return_value={"takip_edilenler": [{"url_2": url}]}),
              patch.object(telegram, "save_options_and_restart") as save):
            self.assertIn("zaten takip ediliyor", telegram._quick_add_watch(url, Decimal("1")))
        save.assert_not_called()

    def test_keyword_matching_respects_exclusions(self):
        self.assertEqual(telegram._matching_keyword("Yeni AirPods Pro indirimde", ["airpods"]), "airpods")
        self.assertIsNone(telegram._matching_keyword("Yeni kulaklık", ["airpods"]))
        self.assertTrue(telegram._has_exclude_keyword("AirPods kılıfı", ["kılıf"]))


class TelegramConnectionTests(unittest.TestCase):
    def test_disabled_listener_only_records_its_state(self):
        data = TempData()
        try:
            with patch.object(telegram, "TELEGRAM_STATUS_PATH", data.root / "status.json"):
                telegram.run_telegram_listener(config([]))
                self.assertEqual(load_json(data.root / "status.json", {})["telegram_state"], "Pasif")
        finally:
            data.cleanup()

    def test_dropped_connection_is_reestablished(self):
        cfg = config([])
        cfg.telegram.enabled = True
        stop = threading.Event()
        attempts = []

        async def listen(_config, _notifier):
            attempts.append(1)
            if len(attempts) == 2:
                stop.set()
            raise ConnectionError("Telegram bağlantısı kapandı.")

        with (patch.object(telegram, "TelegramClient", object), patch.object(telegram, "events", object()),
              patch.object(telegram, "_listen", side_effect=listen), patch.object(telegram, "record_telegram_error") as record,
              patch.object(telegram, "RECONNECT_DELAY_SECONDS", 0)):
            telegram.run_telegram_listener(cfg, stop)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(record.call_count, 2)

    def test_waiting_for_a_login_code_does_not_retry(self):
        cfg = config([])
        cfg.telegram.enabled = True
        with (patch.object(telegram, "TelegramClient", object), patch.object(telegram, "events", object()),
              patch.object(telegram, "_listen", side_effect=telegram.WaitingForUser()) as listen):
            telegram.run_telegram_listener(cfg, threading.Event())
        listen.assert_called_once()

    def test_seen_messages_are_handled_once_across_restarts(self):
        data = TempData()
        try:
            with patch.object(telegram, "TELEGRAM_SEEN_MESSAGES_PATH", data.root / "seen.json"):
                self.assertTrue(telegram._first_time_seen("1:2"))
                self.assertFalse(telegram._first_time_seen("1:2"))
        finally:
            data.cleanup()

    def test_saved_messages_conversation_asks_for_a_price_then_adds_the_card(self):
        data = TempData()
        url = "https://www.amazon.com.tr/dp/B000000001"
        try:
            with (patch.object(telegram, "TELEGRAM_QUICK_ADD_PATH", data.root / "quick.json"),
                  patch.object(telegram, "_quick_add_watch", return_value=telegram.QUICK_ADD_DONE) as add):
                first = SimpleNamespace(raw_text=f"Şuna bak {url}", chat_id=7, id=10, message=None,
                                        reply=AsyncMock(return_value=SimpleNamespace(id=11)))
                self.assertTrue(asyncio.run(telegram._handle_saved_message_quick_add(first)))
                answer = SimpleNamespace(raw_text="40.000 TL", chat_id=7, id=12, message=SimpleNamespace(reply_to_msg_id=11),
                                         reply=AsyncMock())
                self.assertTrue(asyncio.run(telegram._handle_saved_message_quick_add(answer)))
            add.assert_called_once_with(url, Decimal("40000"))
            self.assertIn("Takip kaydı eklendi", answer.reply.call_args.args[0])
            self.assertEqual(json.loads((data.root / "quick.json").read_text())["pending"], [])
        finally:
            data.cleanup()


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.data = TempData()

    def tearDown(self):
        self.data.cleanup()

    def test_settings_error_keeps_the_panel_healthy_and_resets_apply_directly(self):
        save_json(self.data.files.state, {"offer": {"last_alerted_price": "1"}, "_meta": {}})
        runtime = hermes_app.HermesRuntime(None, "Pushover anahtarları zorunlu.", files=self.data.files)
        self.assertEqual(runtime.health(), (True, "ayar hatası"))
        ok, message = runtime.reset_notifications()
        self.assertTrue(ok)
        self.assertIn("1 kayıt", message)
        self.assertNotIn("last_alerted_price", self.data.state()["offer"])

    def test_a_running_monitor_applies_resets_itself(self):
        runtime = hermes_app.HermesRuntime(config([watch("x", "https://nordbron.com/x")]), files=self.data.files)
        command = runner.Command("reset_price_history", runner.reset_price_history)
        command.done.set()
        command.result = 3
        with patch.object(runtime.service, "submit", return_value=command) as submit:
            ok, message = runtime.reset_price_history()
        submit.assert_called_once_with("reset_price_history")
        self.assertIn("3", message)

    def test_a_reset_during_a_long_cycle_is_queued_with_an_explanation(self):
        runtime = hermes_app.HermesRuntime(config([watch("x", "https://nordbron.com/x")]), files=self.data.files)
        command = runner.Command("reset_notifications", runner.reset_notifications)
        with (patch.object(runtime.service, "submit", return_value=command), patch.object(hermes_app, "ACTION_WAIT_SECONDS", 0)):
            ok, message = runtime.reset_notifications()
        self.assertTrue(ok)
        self.assertIn("tarama sürüyor", message)

    def test_stuck_cycle_fails_health(self):
        service = runner.MonitorService(config([watch("x", "https://nordbron.com/x")]), self.data.files, notifier=Mock())
        self.assertTrue(service.health()[0])
        service.cycle_started_at = 0.0
        with patch.object(runner.time, "monotonic", return_value=runner.STUCK_CYCLE_SECONDS + 1):
            self.assertFalse(service.health()[0])


class EndToEndTests(unittest.TestCase):
    def test_monitor_panel_and_reset_work_together(self):
        import time
        import urllib.request

        from hermes.monitor.cycle import DataFiles
        from hermes.web import dashboard, server

        data = TempData()
        rule = watch("Çanta", "https://nordbron.com/canta", target="5000")
        cfg = config([rule], interval_seconds=3600)
        runtime = hermes_app.HermesRuntime(cfg, files=data.files)
        runtime.service.monitor.notifier = Mock(configured=True)
        reads = []

        def read(rule, ctx, outcome):
            reads.append(1)
            return [OfferResult("Stark Sırt Çantası", Decimal("4500"), url=rule.url)]

        with (patch.object(NordbronProvider, "read", side_effect=read),
              patch.object(dashboard, "SUMMARY_PATH", data.files.summary), patch.object(dashboard, "STATE_PATH", data.files.state)):
            httpd = server.start_server(server.Router(runtime), 0, public_only=False)
            monitor_thread = threading.Thread(target=runtime.service.run, daemon=True)
            monitor_thread.start()
            base = f"http://127.0.0.1:{httpd.server_address[1]}"
            try:
                deadline = time.monotonic() + 10
                while len(reads) < 1 and time.monotonic() < deadline:
                    time.sleep(0.05)
                while not data.summary().get("rows") and time.monotonic() < deadline:
                    time.sleep(0.05)
                with urllib.request.urlopen(f"{base}/", timeout=5) as response:
                    page = response.read().decode()
                self.assertIn("Stark Sırt Çantası", page)
                self.assertIn("Hedef Fiyat Altındaki Fırsatlar", page)
                runtime.service.monitor.notifier.send.assert_called_once()
                ok, message = runtime.reset_notifications()
                self.assertTrue(ok, message)
                while len(reads) < 2 and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertEqual(len(reads), 2)  # the reset started a new check immediately
                with urllib.request.urlopen(f"{base}/health", timeout=5) as response:
                    self.assertEqual(response.status, 200)
            finally:
                runtime.stop()
                monitor_thread.join(5)
                httpd.shutdown()
                httpd.server_close()
                data.cleanup()
        self.assertFalse(monitor_thread.is_alive())
        self.assertIsInstance(runtime.files, DataFiles)


class StorageTests(unittest.TestCase):
    def test_writes_are_atomic_and_leave_no_temporary_files(self):
        data = TempData()
        try:
            path = data.root / "state.json"
            save_json(path, {"a": 1})
            save_json(path, {"a": 2})
            self.assertEqual(load_json(path, {}), {"a": 2})
            self.assertEqual([item.name for item in data.root.iterdir()], ["state.json"])
            path.write_text("{broken", encoding="utf-8")
            self.assertEqual(load_json(path, {"default": True}), {"default": True})
        finally:
            data.cleanup()


if __name__ == "__main__":
    unittest.main()
