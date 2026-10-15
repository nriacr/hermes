import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from support import APP_PATH, TempData  # noqa: F401

from hermes.history import History
from hermes.monitor import daily, runner
from hermes.monitor import state as state_ops


class DailySummaryTests(unittest.TestCase):
    def setUp(self):
        self.data = TempData()

    def tearDown(self):
        self.data.cleanup()

    def test_message_counts_reads_hides_silent_blocks_and_lists_best_deals(self):
        history = History.at(self.data.files.database)
        for site, outcome in [("amazon", "ok")] * 6 + [("amazon", "timeout")] * 2 + [("togg", "unreadable"), ("togg", "ok"),
                                                                                    ("amazon", "captcha"), ("amazon", "http_503")]:
            history.record_read(site, outcome, 100)
        payload = {"rows": [
            {"seller": "Amazon", "product_title": "Kulaklık A", "product_url": "u1", "price": "900 TL", "target": "1.000 TL"},
            {"seller": "Amazon", "product_title": "Kulaklık B", "product_url": "u2", "price": "500 TL", "target": "1.000 TL"},
            {"seller": "Amazon", "product_title": "Pahalı", "product_url": "u3", "price": "2.000 TL", "target": "1.000 TL"},
        ]}
        text = daily.build_daily_summary(self.data.files.database, payload)
        self.assertIn("Okuma: 12", text)
        self.assertIn("hata: 3", text)  # the two timeouts and the unreadable one; captcha/503 stay silent
        self.assertNotIn("captcha", text.lower())
        self.assertIn("En çok hata: Amazon (2)", text)
        self.assertIn("Hedefte veya altında: 2 ürün", text)
        self.assertLess(text.index("Kulaklık B"), text.index("Kulaklık A"))
        self.assertNotIn("Pahalı", text)

    def test_due_once_a_day_between_nine_and_ten_pm(self):
        local = datetime(2026, 10, 15, 9, 30)
        self.assertTrue(daily.summary_due(local, "", True))
        self.assertFalse(daily.summary_due(local, "2026-10-15", True))
        self.assertFalse(daily.summary_due(local, "", False))
        self.assertFalse(daily.summary_due(local.replace(hour=8), "", True))
        self.assertFalse(daily.summary_due(local.replace(hour=22), "", True))

    def test_service_sends_once_and_remembers_the_day(self):
        service = object.__new__(runner.MonitorService)
        service.config = Mock(daily_summary_enabled=True)
        service.files = self.data.files
        service.monitor = Mock()
        service.monitor._state = {}
        with patch.object(runner, "local_now", return_value=datetime(2026, 10, 15, 10, 0)):
            service._maybe_send_daily_summary()
            service._maybe_send_daily_summary()
        service.monitor.delivery.send.assert_called_once()
        self.assertEqual(state_ops.meta(service.monitor._state)["last_daily_summary"], "2026-10-15")


if __name__ == "__main__":
    unittest.main()
