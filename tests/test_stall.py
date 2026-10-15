import unittest
from unittest.mock import Mock, patch

from support import APP_PATH  # noqa: F401

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


if __name__ == "__main__":
    unittest.main()
