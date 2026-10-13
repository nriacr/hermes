"""Eight explicit intervals, migration safety, and shared panel selections."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from support import key, watch
from hermes.config import prepare_watches
from hermes.constants import DEFAULT_PRIORITY, PRIORITIES, normalize_priority
from hermes.monitor import scheduling
from hermes.web import settings
from hermes.web.statistics import check_gaps


STEPS = (("1m", 60, "1 dk"), ("5m", 300, "5 dk"), ("15m", 900, "15 dk"),
         ("30m", 1800, "30 dk"), ("60m", 3600, "60 dk"), ("3h", 10800, "3 saat"),
         ("6h", 21600, "6 saat"), ("12h", 43200, "12 saat"))


class FrequencyTests(unittest.TestCase):
    def test_only_the_eight_requested_intervals_are_selectable(self):
        self.assertEqual(PRIORITIES, tuple(step[0] for step in STEPS))
        self.assertEqual(DEFAULT_PRIORITY, "1m")
        html = settings.watch_form({}, 0, is_new=True)
        self.assertNotIn("Her çevrim", html)
        for value, _seconds, label in STEPS:
            self.assertIn(f"value='{value}'", html)
            self.assertIn(f">{label}</option>", html)

    def test_boundary_for_each_interval_uses_minutes_even_with_five_second_rounds(self):
        now = datetime.now(timezone.utc)
        checked = {"last_checked_at": now.isoformat()}
        for value, seconds, _label in STEPS:
            rule = watch(priority=value)
            with self.subTest(priority=value):
                with patch.object(scheduling, "local_now", return_value=now + timedelta(seconds=seconds - 1)):
                    self.assertFalse(scheduling.watch_check_due(rule, checked, 5))
                with patch.object(scheduling, "local_now", return_value=now + timedelta(seconds=seconds)):
                    self.assertTrue(scheduling.watch_check_due(rule, checked, 5))

    def test_legacy_cycle_maps_to_one_minute_without_changing_watch_identity(self):
        original = watch(priority="cycle")
        migrated = watch(priority="1m")
        self.assertEqual(normalize_priority("cycle"), "1m")
        self.assertEqual(key(original), key(migrated))
        config = [{"name": "Korunan kart", "url_1": original.url, "target_price": 1000, "priority": "cycle"}]
        self.assertEqual(prepare_watches(config)[0].priority, "1m")
        self.assertEqual(config[0]["priority"], "cycle")  # no destructive rewrite of stored options

    def test_existing_long_intervals_and_pre_312_fallback_remain_unchanged(self):
        for value in ("30m", "60m", "3h", "6h"):
            self.assertEqual(normalize_priority(value), value)
        for value in (None, "high", "medium", "low", "unknown"):
            self.assertEqual(normalize_priority(value), "6h")

    def test_every_new_interval_survives_panel_save_and_configuration_load(self):
        for value, _seconds, label in STEPS:
            with self.subTest(priority=value):
                saved = settings.build_watches({"watches_count": ["1"], "watches_0_name": ["Kart"],
                    "watches_0_target_price": ["1000"], "watches_0_url_1": [watch().url],
                    "watches_0_active": ["on"],
                    "watches_0_priority": [value]})
                self.assertEqual(prepare_watches(saved)[0].priority, value)
                self.assertIn(f"selected>{label}</option>", settings.watch_form(saved[0], 0))

    def test_pre_update_form_saves_the_removed_choice_as_one_minute(self):
        saved = settings.build_watches({"watches_count": ["1"], "watches_0_name": ["Kart"],
            "watches_0_target_price": ["1000"], "watches_0_url_1": [watch().url],
            "watches_0_active": ["on"], "watches_0_priority": ["cycle"]})
        self.assertEqual(saved[0]["priority"], "1m")

    def test_statistics_keep_fastest_card_history_across_the_migration(self):
        now = datetime.now(timezone.utc)
        rows = [SimpleNamespace(watch_key="same-card", priority=priority, at=now + timedelta(minutes=index))
                for index, priority in enumerate(("high", "cycle", "1m"))]
        self.assertEqual([gap for _read, gap in check_gaps(rows, now)], [60, 60])


if __name__ == "__main__":
    unittest.main()
