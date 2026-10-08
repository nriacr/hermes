"""Home Assistant sensors and events published through the Supervisor proxy."""

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import requests

from support import LOG_LINES, watch

from hermes import homeassistant
from hermes.homeassistant import HomeAssistantBridge
from hermes.models import OfferResult, PriceSummaryRow


def row(title, price, target, **fields):
    return PriceSummaryRow("Amazon", title, f"https://www.amazon.com.tr/dp/{title}", Decimal(price), Decimal(target),
                           Decimal(price), Decimal(price), **fields)


class BridgeTests(unittest.TestCase):
    def test_disabled_without_a_supervisor_token(self):
        with patch.dict("os.environ", {}, clear=True), patch.object(homeassistant.requests, "post") as post:
            bridge = HomeAssistantBridge()
            self.assertFalse(bridge.enabled)
            self.assertFalse(bridge.set_state("sensor.x", 1, {}))
        post.assert_not_called()

    def test_states_and_events_go_through_the_supervisor_proxy(self):
        bridge = HomeAssistantBridge(token="secret")
        with patch.object(homeassistant.requests, "post") as post:
            self.assertTrue(bridge.set_state("sensor.hermes_test", 3, {"a": 1}))
            self.assertTrue(bridge.fire_event("hermes_firsat", {"b": 2}))
        urls = [call.args[0] for call in post.call_args_list]
        self.assertEqual(urls, ["http://supervisor/core/api/states/sensor.hermes_test", "http://supervisor/core/api/events/hermes_firsat"])
        self.assertEqual(post.call_args_list[0].kwargs["json"], {"state": 3, "attributes": {"a": 1}})
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer secret")

    def test_failures_are_logged_once_until_recovery(self):
        bridge = HomeAssistantBridge(token="secret")
        LOG_LINES.clear()
        with patch.object(homeassistant.requests, "post", side_effect=requests.ConnectionError("down")):
            self.assertFalse(bridge.set_state("sensor.a", 1, {}))
            self.assertFalse(bridge.set_state("sensor.b", 1, {}))
        self.assertEqual(sum("gönderilemedi" in line for line in LOG_LINES), 1)
        with patch.object(homeassistant.requests, "post"):
            self.assertTrue(bridge.set_state("sensor.a", 1, {}))
        self.assertTrue(any("yeniden çalışıyor" in line for line in LOG_LINES))

    def test_cycle_sensors_list_opportunities_timing_and_errors(self):
        bridge = HomeAssistantBridge(token="secret")
        now = datetime(2026, 10, 3, 18, 0, tzinfo=timezone.utc)
        rows = [row("A", "900", "1000"), row("B", "1200", "1000"), row("C", "700", "1000", is_warehouse=True)]
        state = {
            "watch_a": {"site": "amazon", "watch_name": "Kulaklık", "last_error": "Amazon captcha", "last_checked_at": now.isoformat()},
            "watch_old": {"site": "zara", "last_error": "Eski", "last_checked_at": (now - timedelta(days=2)).isoformat()},
            # Failed before the restart and not read again yet: history, not a current error.
            "watch_before_start": {"site": "amazon", "last_error": "Zaman aşımı",
                                   "last_checked_at": (now - timedelta(hours=2)).isoformat()},
            "watch_ok": {"site": "hm", "last_error": None, "last_checked_at": now.isoformat()},
            "_meta": {},
        }
        with patch.object(bridge, "set_state") as set_state, \
                patch.object(homeassistant, "PROCESS_STARTED_AT", now - timedelta(hours=1)):
            bridge.publish_cycle(rows, 2, state, 125.4, 65.2, finished_at=now)
        published = {call.args[0]: call.args[1:] for call in set_state.call_args_list}
        count, attributes = published["sensor.hermes_firsat_sayisi"]
        self.assertEqual(count, 2)
        self.assertEqual([item["urun"] for item in attributes["firsatlar"]], ["C", "A"])  # biggest saving first
        self.assertTrue(attributes["firsatlar"][0]["depo"])
        cycle_time, cycle = published["sensor.hermes_son_tur"]
        self.assertEqual(cycle_time, "2026-10-03T18:00:00+00:00")
        self.assertEqual((cycle["sure_saniye"], cycle["urun_sayisi"], cycle["stokta_olmayan"]), (125, 3, 2))
        error_count, errors = published["sensor.hermes_hata_sayisi"]
        self.assertEqual(error_count, 1)
        self.assertEqual(errors["hatalar"][0], {"site": "Amazon", "takip": "Kulaklık", "hata": "Amazon captcha"})

    def test_opportunity_event_carries_everything_an_automation_needs(self):
        bridge = HomeAssistantBridge(token="secret")
        rule = watch("Edifier M60", "https://www.amazon.com.tr/dp/B0D95QG8W4", target="9000")
        offer = OfferResult("Edifier M60 Siyah", Decimal("8787.77"), "Amazon Depo", rule.url, True)
        with patch.object(bridge, "fire_event") as fire:
            bridge.publish_opportunity(rule, offer, "Edifier M60 Siyah", rule.url)
        event, data = fire.call_args.args
        self.assertEqual(event, "hermes_firsat")
        self.assertEqual(data, {"site": "Amazon", "takip": "Edifier M60", "urun": "Edifier M60 Siyah", "fiyat": 8787.77,
                                "fiyat_metni": "8.787 TL", "hedef": 9000.0, "fark": -212.23, "depo": True,
                                "satici": "Amazon Depo", "url": rule.url})

    def test_idle_cycles_do_not_flood_home_assistant(self):
        bridge = HomeAssistantBridge(token="secret")
        now = datetime(2026, 10, 3, 18, 0, tzinfo=timezone.utc)
        rows = [row("A", "900", "1000")]
        with patch.object(bridge, "set_state", return_value=True) as set_state:
            for seconds in (0, 3, 6, 59):
                bridge.publish_cycle(rows, 0, {}, 3, 1, finished_at=now + timedelta(seconds=seconds))
            self.assertEqual(set_state.call_count, 3)  # each sensor once
            bridge.publish_cycle(rows, 0, {}, 3, 1, finished_at=now + timedelta(seconds=61))
            self.assertEqual([call.args[0] for call in set_state.call_args_list[3:]], ["sensor.hermes_son_tur"])
            bridge.publish_cycle([row("A", "800", "1000")], 0, {}, 3, 1, finished_at=now + timedelta(seconds=62))
            self.assertEqual(set_state.call_args_list[-1].args[0], "sensor.hermes_firsat_sayisi")

    def test_a_failed_sensor_update_is_sent_again(self):
        bridge = HomeAssistantBridge(token="secret")
        rows = [row("A", "900", "1000")]
        with patch.object(bridge, "set_state", side_effect=[False, False, False, True, True, True]) as set_state:
            bridge.publish_cycle(rows, 0, {}, 3, 1)
            bridge.publish_cycle(rows, 0, {}, 3, 1)
        self.assertEqual(set_state.call_count, 6)

    def test_long_lists_are_capped(self):
        bridge = HomeAssistantBridge(token="secret")
        rows = [row(f"P{i}", "1", "10") for i in range(40)]
        with patch.object(bridge, "set_state") as set_state:
            bridge.publish_cycle(rows, 0, {}, 1, 1)
        self.assertEqual(set_state.call_args_list[0].args[1], 40)
        self.assertEqual(len(set_state.call_args_list[0].args[2]["firsatlar"]), homeassistant.MAX_LISTED_ITEMS)


class ModuleImportTests(unittest.TestCase):
    def test_every_module_imports(self):
        """Catches leftovers of removed modules and broken imports in any file."""
        import importlib
        import pkgutil

        import hermes

        names = [info.name for info in pkgutil.walk_packages(hermes.__path__, "hermes.") if not info.name.endswith("__main__")]
        self.assertGreater(len(names), 30)
        for name in names:
            with self.subTest(module=name):
                importlib.import_module(name)


if __name__ == "__main__":
    unittest.main()
