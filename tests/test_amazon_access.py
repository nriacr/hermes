"""Amazon's request budget: rolling window, adaptive limit, slow start and the cookie jar."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

from support import LOG_LINES

from hermes.constants import (
    AMAZON_RECOVERY_SLOW_SECONDS,
    AMAZON_START_SLOW_SECONDS,
    AMAZON_WINDOW_MAX_LIMIT,
    AMAZON_WINDOW_MIN_LIMIT,
    AMAZON_WINDOW_SECONDS,
    AMAZON_WINDOW_START_LIMIT,
)
from hermes.providers.amazon import client as amazon_client
from hermes.providers.amazon.access import AmazonAccess
from hermes.providers.amazon.client import AmazonClient, load_cookies, save_cookies
from hermes.providers.base import RequestSpacing

HOUR = 60 * 60


class FakeTime:
    def __init__(self):
        self.now = 10_000.0

    def clock(self):
        return self.now

    def wall(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def advance(self, seconds):
        self.now += seconds


def access_with(fake, path=None):
    return AmazonAccess(path, clock=fake.clock, wall=fake.wall, sleep=fake.sleep)


def make_requests(access, fake, count, gap=1.0, blocked=False):
    for _ in range(count):
        access.wait_for_window()
        access.request_started()
        access.request_finished(blocked)
        fake.advance(gap)


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeTime()
        self.access = access_with(self.fake)

    def test_a_full_window_waits_until_the_oldest_request_leaves_it(self):
        self.access.limit = 3
        for _ in range(3):
            self.access.request_started()
            self.fake.advance(10)
        waited = self.access.wait_for_window()
        # The oldest request started 30 s ago and leaves the 35-minute window after the rest.
        self.assertAlmostEqual(waited, AMAZON_WINDOW_SECONDS - 30, delta=5)
        self.assertLess(self.access.window_count(), 3)

    def test_requests_older_than_the_window_do_not_count(self):
        make_requests(self.access, self.fake, 5, gap=1)
        self.assertEqual(self.access.window_count(), 5)
        self.fake.advance(AMAZON_WINDOW_SECONDS)
        self.assertEqual(self.access.window_count(), 0)

    def test_rate_is_the_last_five_minutes(self):
        make_requests(self.access, self.fake, 10, gap=1)
        self.assertEqual(self.access.rate_per_minute(), 2.0)
        self.fake.advance(400)
        self.assertEqual(self.access.rate_per_minute(), 0.0)

    def test_gap_doubles_for_the_first_minutes_after_a_start(self):
        self.assertEqual(self.access.gap_multiplier(), 2.0)
        self.fake.advance(AMAZON_START_SLOW_SECONDS + 1)
        self.assertEqual(self.access.gap_multiplier(), 1.0)


class AdaptiveLimitTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeTime()
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "amazon_access.json"
        self.access = access_with(self.fake, self.path)

    def tearDown(self):
        self.directory.cleanup()

    def use_window(self, count):
        self.access.starts.extend([self.fake.now] * count)
        self.access.peak_since_raise = count

    def test_limit_starts_at_300_and_rises_5_percent_after_a_clean_hour_of_real_use(self):
        self.assertEqual(self.access.limit, AMAZON_WINDOW_START_LIMIT)
        self.use_window(260)
        self.fake.advance(HOUR + 1)
        self.access.request_started()
        self.assertEqual(self.access.limit, 315)
        # Another clean hour needs its own use of the window.
        self.fake.advance(HOUR + 1)
        self.access.starts.clear()
        self.access.request_started()
        self.assertEqual(self.access.limit, 315)

    def test_limit_never_rises_above_the_hard_ceiling(self):
        self.access.limit = AMAZON_WINDOW_MAX_LIMIT - 1
        self.use_window(480)
        self.fake.advance(HOUR + 1)
        self.access.request_started()
        self.assertEqual(self.access.limit, AMAZON_WINDOW_MAX_LIMIT)
        self.assertLessEqual(self.access.limit, 500)

    def test_a_block_records_the_window_count_lowers_the_limit_to_85_percent_and_freezes_it(self):
        self.use_window(340)
        self.access.request_finished(True)
        self.assertEqual(self.access.threshold, 340)
        self.assertEqual(self.access.limit, 289)
        self.assertTrue(self.access.frozen)
        self.assertTrue(any("eşik=340" in line and "300 → 289" in line for line in LOG_LINES))
        # Frozen: a clean hour does not raise it again.
        self.use_window(280)
        self.fake.advance(HOUR + 1)
        self.access.request_started()
        self.assertEqual(self.access.limit, 289)

    def test_blocks_of_one_episode_do_not_lower_the_limit_again(self):
        self.use_window(200)
        self.access.request_finished(True)
        first = self.access.limit
        self.access.starts.clear()
        self.access.request_finished(True)  # a failed probe: same episode
        self.assertEqual(self.access.limit, first)
        self.assertEqual(self.access.threshold, 200)
        self.access.request_finished(False)
        self.use_window(100)
        self.access.request_finished(True)  # a new episode after a success
        self.assertEqual(self.access.threshold, 100)
        self.assertEqual(self.access.limit, 85)

    def test_limit_has_a_floor(self):
        self.use_window(10)
        self.access.request_finished(True)
        self.assertEqual(self.access.limit, AMAZON_WINDOW_MIN_LIMIT)

    def test_a_block_starts_an_hour_of_half_speed(self):
        self.fake.advance(AMAZON_START_SLOW_SECONDS + 1)
        self.assertEqual(self.access.gap_multiplier(), 1.0)
        self.access.request_finished(True)
        self.assertEqual(self.access.gap_multiplier(), 2.0)
        self.fake.advance(AMAZON_RECOVERY_SLOW_SECONDS - 1)
        self.assertEqual(self.access.gap_multiplier(), 2.0)
        self.fake.advance(2)
        self.assertEqual(self.access.gap_multiplier(), 1.0)

    def test_limit_threshold_and_slow_start_survive_a_restart(self):
        self.use_window(200)
        self.access.request_finished(True)
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual((stored["limit"], stored["threshold"], stored["frozen"]), (170, 200, True))
        self.fake.advance(120)
        restarted = access_with(self.fake, self.path)
        self.assertEqual((restarted.limit, restarted.threshold, restarted.frozen), (170, 200, True))
        self.assertEqual(restarted.gap_multiplier(), 2.0)

    def test_a_raise_is_saved_too(self):
        self.use_window(260)
        self.fake.advance(HOUR + 1)
        self.access.request_started()
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["limit"], 315)

    def test_a_broken_file_falls_back_to_the_defaults(self):
        self.path.write_text("{not json", encoding="utf-8")
        access = access_with(self.fake, self.path)
        self.assertEqual(access.limit, AMAZON_WINDOW_START_LIMIT)

    def test_measurement_line_has_window_threshold_and_rate(self):
        self.use_window(120)
        self.access.request_finished(True)
        line = self.access.stats_line()
        for expected in ("pencere=120/102", "eşik=120", "istek/dk", "sabit", "son 60 dk: istek=1, engel=1"):
            self.assertIn(expected, line)


class ClientBudgetTests(unittest.TestCase):
    def test_every_request_goes_through_the_window_and_the_slow_start_gap(self):
        fake = FakeTime()
        access = access_with(fake)
        spacing = RequestSpacing(5.0, sleep=fake.sleep, clock=fake.clock)
        pages = [requests.Response()]
        pages[0].status_code = 200
        pages[0]._content = b"<html>Amazon product</html>"
        pages[0].encoding = "utf-8"
        session = SimpleNamespace(cookies=requests.cookies.RequestsCookieJar(), get=lambda *a, **k: pages[0],
                                  close=lambda: None)
        with patch.object(amazon_client, "curl_requests", SimpleNamespace(Session=lambda: session)):
            with AmazonClient(spacing=spacing, access=access) as client:
                client.fetch("https://www.amazon.com.tr/dp/B000000001", 10)
                client.fetch("https://www.amazon.com.tr/dp/B000000002", 10)
                # Right after a start the gap is doubled: 10 seconds instead of 5.
                self.assertEqual(spacing.min_gap_seconds, 10.0)
                self.assertEqual(access.counters["istek"], 2)
                self.assertEqual(access.window_count(), 2)

    def test_a_challenge_page_counts_as_a_block(self):
        fake = FakeTime()
        access = access_with(fake)
        spacing = RequestSpacing(0, sleep=fake.sleep, clock=fake.clock)
        page = requests.Response()
        page.status_code = 200
        page._content = '<form action="/errors/validateCaptcha">Amazon</form>'.encode()
        page.encoding = "utf-8"
        session = SimpleNamespace(cookies=requests.cookies.RequestsCookieJar(), get=lambda *a, **k: page, close=lambda: None)
        with patch.object(amazon_client, "curl_requests", SimpleNamespace(Session=lambda: session)):
            with AmazonClient(spacing=spacing, access=access) as client:
                with self.assertRaises(Exception):
                    client.fetch("https://www.amazon.com.tr/dp/B000000001", 10)
        self.assertEqual(access.counters["engel"], 1)
        self.assertTrue(access.frozen)


class CookieJarTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "amazon_cookies.json"

    def tearDown(self):
        self.directory.cleanup()

    def test_cookies_survive_a_restart(self):
        first = requests.Session()
        first.cookies.set("session-id", "abc-123", domain=".amazon.com.tr", path="/")
        first.cookies.set("other", "x", domain=".example.com", path="/")
        save_cookies(first, self.path)
        self.assertEqual(oct(self.path.stat().st_mode & 0o777), "0o600")
        second = requests.Session()
        self.assertEqual(load_cookies(second, self.path), 1)
        self.assertEqual(second.cookies.get("session-id", domain=".amazon.com.tr"), "abc-123")
        self.assertIsNone(second.cookies.get("other", domain=".example.com"))

    def test_old_or_expired_cookies_are_not_restored(self):
        session = requests.Session()
        session.cookies.set("session-id", "abc", domain=".amazon.com.tr", path="/")
        save_cookies(session, self.path)
        with patch.object(amazon_client.time, "time", return_value=amazon_client.time.time() + 8 * 24 * 3600):
            self.assertEqual(load_cookies(requests.Session(), self.path), 0)
        self.path.write_text(json.dumps([{"name": "a", "value": "b", "domain": ".amazon.com.tr", "path": "/", "expires": 1}]),
                             encoding="utf-8")
        self.assertEqual(load_cookies(requests.Session(), self.path), 0)

    def test_missing_or_broken_file_restores_nothing(self):
        self.assertEqual(load_cookies(requests.Session(), self.path), 0)
        self.path.write_text("garbage", encoding="utf-8")
        self.assertEqual(load_cookies(requests.Session(), self.path), 0)
        self.assertEqual(load_cookies(requests.Session(), None), 0)


if __name__ == "__main__":
    unittest.main()
