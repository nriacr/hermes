import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from support import watch

from hermes.monitor import runner
from hermes.monitor.stall import StallWatch, stall_threshold


class StallWatchTests(unittest.TestCase):
    def test_threshold_is_45_minutes_or_three_intervals(self):
        self.assertEqual(stall_threshold(60), 45 * 60)
        self.assertEqual(stall_threshold(6 * 3600), 18 * 3600)

    def test_site_stalls_once_and_recovers(self):
        watch = StallWatch(started_at=0)
        limits = {"amazon": 2700, "togg": 2700}
        self.assertEqual(watch.update(2000, {}, limits, []), ([], []))
        self.assertEqual(watch.update(2800, {"togg": 2700}, limits, []), (["amazon"], []))
        self.assertEqual(watch.update(3000, {"togg": 2900}, limits, []), ([], []))
        self.assertEqual(watch.update(3100, {"togg": 3000, "amazon": 3050}, limits, []), ([], ["amazon"]))
        self.assertEqual(watch.stalled, {})

    def test_captcha_hold_stays_silent(self):
        watch = StallWatch(started_at=0)
        self.assertEqual(watch.update(5000, {}, {"amazon": 2700}, ["amazon"]), ([], []))
        self.assertEqual(watch.stalled, {})


class ServiceStallTests(unittest.TestCase):
    def service(self, guards):
        service = object.__new__(runner.MonitorService)
        service.config = Mock(interval_seconds=60, watches=[Mock(site="amazon", active=True, priority="1m")])
        service.monitor = Mock()
        service.monitor._state = {"_meta": {"site_guards": guards}}
        service.monitor.diagnostics.last_success = {}
        service.monitor.home_assistant.enabled = True
        return service

    def test_stalled_site_notifies_once_outside_quiet_hours(self):
        service = self.service({})
        stall = StallWatch(started_at=0)
        with patch.object(runner, "quiet_hours", return_value=False), patch.object(runner.state_ops, "guard_store", return_value={}):
            service._check_stalls(stall, ["amazon"], 3000)
            service._check_stalls(stall, ["amazon"], 3100)
        service.monitor.delivery.send.assert_called_once()
        service.monitor.home_assistant.persistent_problem.assert_called_once()

    def test_quiet_hours_defer_the_push_but_not_the_ha_notice(self):
        service = self.service({})
        stall = StallWatch(started_at=0)
        with patch.object(runner, "quiet_hours", return_value=True), patch.object(runner.state_ops, "guard_store", return_value={}):
            service._check_stalls(stall, ["amazon"], 3000)
        service.monitor.delivery.send.assert_not_called()
        service.monitor.home_assistant.persistent_problem.assert_called_once()

    def test_silent_guard_blocks_the_alert(self):
        service = self.service({})
        stall = StallWatch(started_at=0)
        guards = {"site:amazon": {"kind": "captcha"}}
        with patch.object(runner, "quiet_hours", return_value=False), patch.object(runner.state_ops, "guard_store", return_value=guards):
            service._check_stalls(stall, ["amazon"], 3000)
        service.monitor.delivery.send.assert_not_called()

    def test_togg_night_mode_silences_stall_and_starts_fresh_at_eight(self):
        service = self.service({})
        service.config.watches = [watch("Togg", "https://configurator.togg.com.tr/", priority="5m",
                                        night_start="18:00", night_end="08:00"),
                                  watch(priority="1m")]
        stall = StallWatch(started_at=0)
        with patch.object(runner, "quiet_hours", return_value=False), \
             patch.object(runner, "local_now", return_value=datetime(2026, 11, 13, 18, 45)):
            service._check_stalls(stall, ["togg", "amazon"], 2800)
            service._check_stalls(stall, ["togg", "amazon"], 2900)
        service.monitor.home_assistant.clear_problem.assert_called_once_with("stall:togg")
        service.monitor.delivery.send.assert_called_once()
        self.assertEqual(set(stall.stalled), {"amazon"})
        with patch.object(runner, "quiet_hours", return_value=False), \
             patch.object(runner, "local_now", return_value=datetime(2026, 11, 14, 8)):
            service._check_stalls(stall, ["togg", "amazon"], 50000)
            service._check_stalls(stall, ["togg", "amazon"], 52699)
            self.assertNotIn("togg", stall.stalled)
            service._check_stalls(stall, ["togg", "amazon"], 52701)
        self.assertIn("togg", stall.stalled)
        self.assertEqual(service.monitor.delivery.send.call_count, 2)

    def test_a_second_awake_togg_card_still_reports_a_stall(self):
        service = self.service({})
        service.config.watches = [watch("Togg bekliyor", "https://configurator.togg.com.tr/",
                                        night_start="18:00", night_end="08:00"),
                                  watch("Togg uyanık", "https://configurator.togg.com.tr/")]
        with patch.object(runner, "quiet_hours", return_value=False), \
             patch.object(runner, "local_now", return_value=datetime(2026, 11, 13, 18, 45)):
            service._check_stalls(StallWatch(started_at=0), ["togg"], 2800)
        service.monitor.delivery.send.assert_called_once()


if __name__ == "__main__":
    unittest.main()
