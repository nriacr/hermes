"""Confirmed network loss defers reads while real site failures stay visible."""

import json
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import Mock, patch

import requests

from support import TempData, config, key, monitor, watch

from hermes.errors import HttpStatusHermesError, PriceUnavailableHermesError
from hermes.homeassistant import HomeAssistantBridge
from hermes.models import OfferResult
from hermes.monitor.network import Access, NetworkPaused, failure_details
from hermes.web import dashboard

NOW = datetime(2026, 11, 13, 2, tzinfo=timezone.utc)
URL = "https://www.trendyol.com/philips/urun-p-123"


class ModemAccessTests(unittest.TestCase):
    def test_current_planned_operation_has_priority_and_expires(self):
        modem = {"planned": {"operation": "reboot", "seconds": 15},
                 "ping": {"at": NOW.isoformat(), "reachable": False}}
        self.assertEqual(Access.from_modem(modem, NOW).label, "Planlı modem yeniden başlatması")
        modem["planned"]["seconds"] = 300
        self.assertEqual(Access.from_modem(modem, NOW).state, "offline")
        self.assertEqual(Access.from_modem(modem, NOW + timedelta(minutes=1)).state, "unknown")

    def test_both_external_name_and_web_checks_failed(self):
        modem = {"planned": None, "ping": {"at": NOW.isoformat(), "net_checked_at": NOW.isoformat(),
                                          "reachable": True, "dns_ok": False, "http_ok": False}}
        self.assertEqual(Access.from_modem(modem, NOW).state, "offline")
        modem["ping"]["http_ok"] = True
        self.assertEqual(Access.from_modem(modem, NOW).state, "online")

    def test_reads_response_from_ha_and_rejects_missing_or_invalid_data(self):
        bridge = HomeAssistantBridge(token="secret")
        with patch("hermes.homeassistant.requests.post") as post:
            post.return_value.json.return_value = {"service_response": {"planned": {"operation": "reboot", "seconds": 1}, "ping": {}}}
            self.assertTrue(bridge.modem_access().paused)
            self.assertTrue(post.call_args.args[0].endswith("/services/h162_modem/status?return_response"))
            post.return_value.json.return_value = {"service_response": {}}
            self.assertEqual(bridge.modem_access().state, "unknown")

    def test_failure_phase_does_not_guess_generic_timeout(self):
        cases = [(requests.ConnectTimeout(), "tcp"), (requests.ReadTimeout(), "response_timeout"),
                 (requests.Timeout(), "timeout"), (requests.exceptions.SSLError(), "tls"),
                 (RuntimeError("curl: (6) Could not resolve host"), "dns"),
                 (PriceUnavailableHermesError("fiyat"), "price"), (HttpStatusHermesError(503, URL), "http")]
        for exc, expected in cases:
            with self.subTest(exc=exc):
                self.assertEqual(failure_details(exc)["stage"], expected)


class MonitoringAccessTests(unittest.TestCase):
    def setUp(self):
        self.data = TempData()
        self.addCleanup(self.data.cleanup)
        self.rule = watch("Lamba", URL, target="500")
        self.monitor = monitor(config([self.rule]), self.data)
        self.provider = self.monitor.providers[self.rule.site]

    def job(self):
        row = self.monitor.diagnostics.db.connect().execute(
            "SELECT outcome,evidence FROM jobs ORDER BY started DESC LIMIT 1").fetchone()
        return row[0], json.loads(row[1])

    def test_outage_preserves_last_price_and_allows_next_read(self):
        self.provider.read = Mock(return_value=[OfferResult("Lamba", Decimal("800"), url=URL)])
        self.monitor.run_cycle()
        before = self.data.summary()["rows"]
        self.assertEqual(len(before), 1)
        self.monitor.access = Access("planned", operation="reboot")
        self.provider.read.reset_mock()
        self.monitor.run_cycle()
        self.provider.read.assert_not_called()
        self.assertEqual(self.data.summary()["rows"], before)
        self.monitor.access = Access("online")
        self.provider.read = Mock(return_value=[OfferResult("Lamba", Decimal("810"), url=URL)])
        self.monitor._startup_pending.add(key(self.rule))
        self.monitor.run_cycle()
        self.assertEqual(self.job()[0], "ok")
        self.assertEqual(self.data.summary()["rows"][0]["price"], "810 TL")

    def test_mid_read_network_failure_is_recorded_separately(self):
        def read(_watch, _ctx, _outcome):
            self.monitor.access = Access("offline", reachable=False)
            raise requests.ConnectTimeout("connection timed out")
        self.provider.read = read
        self.monitor.run_cycle()
        outcome, evidence = self.job()
        self.assertEqual(outcome, "network_unavailable")
        self.assertEqual(evidence["failure"]["stage"], "tcp")
        self.assertEqual(evidence["failure"]["modem"]["state"], "offline")
        self.assertFalse(self.monitor.diagnostics.active())
        self.assertFalse(self.data.state().get(key(self.rule), {}).get("last_error"))

    def test_single_site_error_survives_and_contains_modem_evidence(self):
        self.monitor.access = Access("online", reachable=True)
        self.provider.read = Mock(side_effect=requests.ConnectTimeout("connection timed out"))
        self.monitor.run_cycle()
        error, = self.monitor.diagnostics.active()
        self.assertEqual(error["kind"], "read")
        self.assertEqual(error["context"]["read"]["stage"], "tcp")
        self.assertEqual(error["context"]["read"]["modem"]["state"], "online")

    def test_cooperative_pause_is_separate_from_a_site_failure(self):
        def read(_watch, ctx, _outcome):
            self.monitor.access = Access("planned", operation="reboot")
            ctx.check_access()
        self.provider.read = read
        self.monitor.run_cycle()
        self.assertEqual(self.job()[0], "network_unavailable")
        with self.assertRaises(NetworkPaused):
            self.monitor._check_access()

    def test_dashboard_shows_one_access_notice(self):
        self.monitor.diagnostics.incident("internet", "network", "Planlı modem yeniden başlatması")
        with patch.object(dashboard, "DATABASE_PATH", self.data.files.database), \
             patch.object(dashboard, "STATE_PATH", self.data.files.state), \
             patch.object(dashboard, "SUMMARY_PATH", self.data.files.summary):
            html = dashboard._build_dashboard_live_html(".")
        self.assertEqual(html.count("Planlı modem yeniden başlatması"), 1)
        self.assertIn("Son doğrulanmış fiyatlar", html)
