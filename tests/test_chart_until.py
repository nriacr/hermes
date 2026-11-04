import unittest
from datetime import datetime, timedelta

from hermes.web.pricechart import detail_chart


class ChartUntilTests(unittest.TestCase):
    def test_line_reaches_last_check(self):
        start = datetime(2026, 11, 1, 12, 0)
        points = [(start, 1000.0), (start + timedelta(hours=5), 900.0)]
        now = start + timedelta(days=3)
        chart = detail_chart(points, 800.0, until=now)
        self.assertIn("4 Kas", chart)

    def test_without_until_ends_at_last_point(self):
        start = datetime(2026, 11, 1, 12, 0)
        points = [(start, 1000.0), (start + timedelta(days=2), 900.0)]
        self.assertIn("3 Kas", detail_chart(points, 800.0))

    def test_single_point_draws_flat_line_to_last_check(self):
        start = datetime(2026, 11, 4, 12, 0)
        chart = detail_chart([(start, 1000.0)], 900.0, until=start + timedelta(hours=6))
        self.assertIn("ov-draw", chart)
        self.assertIn(" L", chart)


if __name__ == "__main__":
    unittest.main()
