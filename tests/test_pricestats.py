import unittest
from datetime import datetime, timedelta, timezone

from support import APP_PATH  # noqa: F401

from hermes.pricestats import is_new_low, window_stats

NOW = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)


def at(days_ago):
    return NOW - timedelta(days=days_ago)


class WindowStatsTests(unittest.TestCase):
    def test_no_history(self):
        self.assertIsNone(window_stats([], NOW))

    def test_time_weighted_average_and_low(self):
        points = [(at(30), 100.0), (at(20), 200.0), (at(10), 50.0)]
        stats = window_stats(points, NOW)
        self.assertEqual(stats.low, 50.0)
        self.assertAlmostEqual(stats.days, 30)
        self.assertAlmostEqual(stats.average, (100 * 10 + 200 * 10 + 50 * 10) / 30)

    def test_old_points_are_clipped_to_the_window(self):
        points = [(at(200), 10.0), (at(95), 500.0), (at(40), 300.0)]
        stats = window_stats(points, NOW)
        self.assertEqual(stats.low, 300.0)
        self.assertAlmostEqual(stats.days, 90)
        self.assertAlmostEqual(stats.average, (500 * 50 + 300 * 40) / 90)

    def test_new_low_needs_history_and_a_strictly_lower_price(self):
        history = [(at(30), 100.0), (at(10), 80.0)]
        self.assertTrue(is_new_low(history, 79.0, NOW))
        self.assertFalse(is_new_low(history, 80.0, NOW))
        self.assertFalse(is_new_low([(at(3), 100.0)], 50.0, NOW))
        self.assertFalse(is_new_low([], 50.0, NOW))


class CardIntegrationTests(unittest.TestCase):
    def test_option_is_read_from_the_card(self):
        from hermes.config import prepare_watches

        base = {"name": "x", "target_price": 100, "url_1": "https://www.amazon.com.tr/dp/B000000001"}
        self.assertFalse(prepare_watches([base])[0].notify_on_90_day_low)
        self.assertTrue(prepare_watches([{**base, "notify_90_day_low": True}])[0].notify_on_90_day_low)

    def test_average_chip_shows_only_with_a_week_of_history(self):
        from decimal import Decimal

        from hermes.web.dashboard import Offer, average_chip

        def offer(points, price):
            checked = NOW.strftime("%Y-%m-%d %H:%M:%S")
            return Offer(row={}, seller="Amazon", title="x", stock="", site="amazon", price=Decimal(price), target=Decimal(500),
                         low=Decimal(price), high=Decimal(price), hit=False, warehouse=False, checked_at=checked, points=points)

        self.assertEqual(average_chip(offer([(at(2), 100.0)], 100)), "")
        chip = average_chip(offer([(at(30), 200.0), (at(15), 100.0)], 100))
        self.assertIn("altında", chip)
        self.assertIn("dip", chip)


if __name__ == "__main__":
    unittest.main()
