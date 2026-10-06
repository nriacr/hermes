"""Amazon's request budget: rolling window, adaptive limit, slow start and the cookie jar."""

import json
from datetime import datetime, timezone
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

from support import LOG_LINES

from hermes.constants import (
    AMAZON_BLOCK_HOLD_SECONDS,
    AMAZON_SLOWDOWN_MAX,
    AMAZON_SLOWDOWN_RECOVER_SECONDS,
    AMAZON_START_SLOW_SECONDS,
    AMAZON_WINDOW_MIN_LIMIT,
    AMAZON_WINDOW_SECONDS,
    AMAZON_WINDOW_START_LIMIT,
)
from hermes.errors import BotProtectionHermesError
from hermes import constants as amazon_constants
from hermes.providers.amazon import access as amazon_access
from hermes.providers.amazon import client as amazon_client
from hermes.providers.amazon.access import MAIN_LANE, AmazonAccess
from hermes.providers.amazon.client import AmazonClient, load_cookies, save_cookies

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
        waited = self.access.wait_for_window(MAIN_LANE)
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


def site_block(access):
    """Since 3.6.0 every block is site-wide; a second page failing in the same wave changes nothing more."""
    access.request_finished(True, "B000000001")
    access.request_finished(True, "B000000002")


class AdaptiveLimitTests(unittest.TestCase):
    def setUp(self):
        # These tests describe the limit's mechanics with round numbers (start 300, ceiling 500);
        # the shipped values (3.7.0: 400 and 600) are pinned in test_shipped_limits.
        for name, value in (("AMAZON_WINDOW_START_LIMIT", 300), ("AMAZON_WINDOW_MAX_LIMIT", 500)):
            patcher = patch.object(amazon_access, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
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
        self.assertEqual(self.access.limit, 300)
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
        self.access.limit = 499
        self.use_window(480)
        self.fake.advance(HOUR + 1)
        self.access.request_started()
        self.assertEqual(self.access.limit, 500)
        self.assertLessEqual(self.access.limit, 500)

    def test_the_first_block_records_the_threshold_and_lowers_the_limit_once(self):
        self.use_window(280)
        site_block(self.access)
        self.assertEqual(self.access.threshold, 280)
        self.assertEqual(self.access.limit, 238)
        self.assertTrue(self.access.lowered)
        self.assertTrue(any("eşik=280" in line and "300 → 238" in line for line in LOG_LINES))

    def test_a_later_block_records_its_threshold_but_never_lowers_the_limit_again(self):
        self.use_window(280)
        site_block(self.access)
        self.access.request_finished(False, "B000000003")
        self.access.starts.clear()
        self.use_window(100)
        site_block(self.access)
        self.assertEqual(self.access.threshold, 100)
        self.assertEqual(self.access.limit, 238)

    def test_blocks_of_one_episode_do_not_touch_the_threshold_again(self):
        self.use_window(250)
        site_block(self.access)
        self.access.starts.clear()
        self.access.request_finished(True, "B000000003")  # a failed probe: same episode
        self.access.request_finished(True, "B000000004")
        self.assertEqual(self.access.threshold, 250)

    def test_the_limit_has_a_floor_of_200(self):
        self.use_window(10)
        site_block(self.access)
        self.assertEqual(self.access.limit, 200)
        self.assertEqual(AMAZON_WINDOW_MIN_LIMIT, 200)

    def test_the_limit_rises_again_after_the_block(self):
        self.use_window(260)
        site_block(self.access)
        self.assertEqual(self.access.limit, 221)
        self.access.request_finished(False, "B000000003")
        self.use_window(200)
        self.fake.advance(HOUR + 1)
        self.access.request_started()
        self.assertEqual(self.access.limit, 233)

    def test_a_single_failing_page_is_already_a_site_block(self):
        # 2026-10-04: 43 of 60 requests right after a block were blocked too; the block marks the visitor.
        self.use_window(280)
        self.access.request_finished(True, "B000000001")
        self.assertEqual((self.access.limit, self.access.threshold, self.access.lowered), (238, 280, True))
        self.assertEqual(self.access.gap_multiplier(), 2.0)
        self.assertEqual(self.access.last_block_at, self.fake.now)

    def test_after_a_block_requests_are_held_back_for_the_first_pause_step(self):
        self.assertEqual(self.access.hold_remaining(), 0)
        self.access.request_finished(True, "B000000001")
        self.assertEqual(self.access.hold_remaining(), AMAZON_BLOCK_HOLD_SECONDS)
        self.fake.advance(AMAZON_BLOCK_HOLD_SECONDS)
        self.assertEqual(self.access.hold_remaining(), 0)

    def test_each_wave_is_logged_once_with_its_cause_and_counted_for_the_day(self):
        make_requests(self.access, self.fake, 5)
        LOG_LINES.clear()
        self.access.request_finished(True, "B000000001", "captcha_formu/http_200")
        self.access.request_finished(True, "B000000002", "captcha_formu/http_200")  # the same wave
        waves = [line for line in LOG_LINES if "engel dalgası" in line]
        self.assertEqual(len(waves), 1)
        self.assertIn("bugün #1 | sebep=captcha_formu/http_200 | sayfa=B000000001 | son 60 dk istek=5", waves[0])
        self.access.request_finished(False, "B000000003")
        self.fake.advance(90 * 60)
        self.access.request_finished(True, "B000000004", "http_503")
        self.assertEqual(self.access.waves_today(), 2)
        last_wave = [line for line in LOG_LINES if "engel dalgası" in line][-1]
        self.assertIn("bugün #2 | sebep=http_503", last_wave)
        self.assertIn("önceki engelden beri=90 dk", last_wave)
        self.assertIn("bugünkü engel dalgası=2", self.access.stats_line())

    def test_a_block_wave_slows_the_categories_and_the_speed_returns_by_itself(self):
        self.fake.advance(AMAZON_START_SLOW_SECONDS + 1)
        self.assertEqual(self.access.speed_factor(), 1.0)
        site_block(self.access)
        self.assertEqual(self.access.speed_factor(), 2.0)
        self.fake.advance(AMAZON_SLOWDOWN_RECOVER_SECONDS - 1)
        self.assertEqual(self.access.speed_factor(), 2.0)
        self.fake.advance(2)
        self.assertEqual(self.access.speed_factor(), 1.0)
        self.assertTrue(any("normal hıza döndü" in line for line in LOG_LINES))

    def test_a_second_wave_doubles_again_but_never_beyond_the_maximum(self):
        site_block(self.access)
        self.access.request_finished(False, "B000000009")  # a success ends the wave
        self.fake.advance(60)
        site_block(self.access)
        self.assertEqual(self.access.speed_factor(), 4.0)
        self.access.request_finished(False, "B000000009")
        site_block(self.access)
        self.assertEqual(self.access.speed_factor(), AMAZON_SLOWDOWN_MAX)

    def test_the_speed_comes_back_one_step_per_clean_stretch_and_a_new_block_restarts_the_clock(self):
        site_block(self.access)
        self.access.request_finished(False, "B000000009")
        self.fake.advance(60)
        site_block(self.access)  # x4
        self.fake.advance(AMAZON_SLOWDOWN_RECOVER_SECONDS - 1)
        self.assertEqual(self.access.speed_factor(), 4.0)
        self.fake.advance(2)
        self.assertEqual(self.access.speed_factor(), 2.0)
        self.fake.advance(AMAZON_SLOWDOWN_RECOVER_SECONDS)
        self.assertEqual(self.access.speed_factor(), 1.0)

    def test_the_page_processing_between_two_requests_counts_towards_the_random_wait(self):
        clock = [100.0]
        sleeps = []
        page = requests.Response()
        page.status_code = 200
        page._content = b"<html>Amazon product</html>"
        page.encoding = "utf-8"
        session = SimpleNamespace(cookies=requests.cookies.RequestsCookieJar(), get=lambda *a, **k: page, close=lambda: None)
        access = access_with(FakeTime())
        access.slow_until = 0  # no start slow-down
        with patch.object(amazon_client, "curl_requests", SimpleNamespace(Session=lambda: session)), \
                patch.object(amazon_client.random, "uniform", return_value=3.0):
            with AmazonClient(access=access, delay_range=(1, 4), sleep=sleeps.append, clock=lambda: clock[0]) as client:
                client.fetch("https://www.amazon.com.tr/dp/B000000001", 10)
                self.assertEqual(sleeps, [3.0])  # the first request waits the whole draw
                clock[0] += 3.5  # processing the first page took longer than the draw: no extra wait
                client.fetch("https://www.amazon.com.tr/dp/B000000002", 10)
                self.assertEqual(sleeps, [3.0])
                clock[0] += 1.0  # only 1 s of processing: the remaining 2 s of the draw are waited
                client.fetch("https://www.amazon.com.tr/dp/B000000003", 10)
                self.assertEqual(sleeps, [3.0, 2.0])

    def test_the_governor_stretches_the_request_gap_too(self):
        # A red watch has no timer, so the request gap is how a block slows it down.
        self.fake.advance(AMAZON_START_SLOW_SECONDS + 1)
        self.assertEqual(self.access.gap_multiplier(), 1.0)
        site_block(self.access)
        self.assertEqual(self.access.gap_multiplier(), 2.0)
        self.fake.advance(AMAZON_SLOWDOWN_RECOVER_SECONDS)
        self.assertEqual(self.access.gap_multiplier(), 1.0)

    def test_the_slowdown_survives_a_restart(self):
        path = Path(tempfile.mkdtemp()) / "amazon_access.json"
        access = access_with(self.fake, path)
        site_block(access)
        self.fake.advance(120)
        restarted = access_with(self.fake, path)
        self.assertEqual(restarted.speed_factor(), 2.0)
        self.fake.advance(AMAZON_SLOWDOWN_RECOVER_SECONDS)
        self.assertEqual(restarted.speed_factor(), 1.0)

    def test_the_sweep_leaves_the_depo_lane_only_the_floor_it_has_not_used_yet(self):
        self.access.limit = 100
        self.assertEqual(self.access.limit_for(MAIN_LANE), 100)
        # An idle Depo lane keeps only the floor (12 requests) back, not its whole 28 % share.
        self.assertEqual(self.access.depo_reserve(), 12)
        self.assertEqual(self.access.limit_for(""), 88)
        self.use_window(88)
        self.assertEqual(self.access.wait_for_window(MAIN_LANE), 0.0)  # the Depo lane still has room
        self.assertGreater(self.access.wait_for_window(""), 0)  # the sweep lane waits for the window to move

    def test_the_depo_reserve_follows_its_real_use_up_to_the_share(self):
        self.access.limit = 100
        for used, reserve in ((5, 12), (12, 12), (20, 20), (28, 28), (60, 28)):
            fresh = access_with(FakeTime())
            fresh.limit = 100
            for _ in range(used):
                fresh.request_started(MAIN_LANE)
            self.assertEqual(fresh.depo_reserve(), reserve, used)
            # What the sweep may still use: the window minus the part of the reserve not used yet.
            self.assertEqual(fresh.limit_for(""), 100 - max(0, reserve - used), used)

    def test_a_depo_lane_that_used_more_than_the_floor_leaves_the_sweep_the_rest(self):
        self.access.limit = 100
        for _ in range(20):
            self.access.request_started(MAIN_LANE)
        self.assertEqual(self.access.limit_for(""), 100)
        self.use_window(50)
        self.assertEqual(self.access.wait_for_window(""), 0.0)  # 70 of 100 started

    def test_the_measurement_line_names_the_sweep_limit_and_the_depo_use(self):
        self.access.limit = 210
        for _ in range(5):
            self.access.request_started(MAIN_LANE)
        line = self.access.stats_line()
        self.assertIn("tarama şeridi sınırı=203", line)
        self.assertIn("depo şeridi son 35 dk=5", line)

    def test_the_sweep_steps_aside_while_the_depo_lane_waits_for_a_slot(self):
        waits = []
        self.access.limit_for = lambda lane="": 100
        self.access._main_waiting = 1

        def sleep(seconds):
            waits.append(seconds)
            self.access._main_waiting = 0  # the Depo lane got its slot

        self.access.sleep = sleep
        self.assertGreater(self.access.wait_for_window(""), 0)
        self.assertEqual(len(waits), 1)

    def test_a_full_window_makes_the_depo_lane_wait_too(self):
        self.access.limit = 100
        self.use_window(100)
        self.assertGreater(self.access.wait_for_window(MAIN_LANE), 0)

    def test_limit_threshold_and_slow_start_survive_a_restart(self):
        self.use_window(300)
        site_block(self.access)
        stored = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual((stored["schema"], stored["limit"], stored["threshold"], stored["lowered"]), (3, 255, 300, True))
        self.fake.advance(120)
        restarted = access_with(self.fake, self.path)
        self.assertEqual((restarted.limit, restarted.threshold, restarted.lowered), (255, 300, True))
        self.assertEqual(restarted.gap_multiplier(), 2.0)

    def test_shipped_limits(self):
        self.assertEqual((amazon_constants.AMAZON_WINDOW_START_LIMIT, amazon_constants.AMAZON_WINDOW_MAX_LIMIT), (500, 700))

    def test_the_lowered_limit_of_3_3_0_is_reset_to_300(self):
        self.path.write_text(json.dumps({"limit": 119, "threshold": 141, "frozen": True, "last_raise_at": 1.0,
                                         "last_block_at": 2.0, "slow_until": 3.0}), encoding="utf-8")
        access = access_with(self.fake, self.path)
        self.assertEqual((access.limit, access.threshold, access.lowered), (300, None, False))
        self.assertTrue(any("eski sürümden" in line for line in LOG_LINES))

    def test_a_raise_is_saved_too(self):
        self.use_window(260)
        self.fake.advance(HOUR + 1)
        self.access.request_started()
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["limit"], 315)

    def test_a_broken_file_falls_back_to_the_defaults(self):
        self.path.write_text("{not json", encoding="utf-8")
        access = access_with(self.fake, self.path)
        self.assertEqual(access.limit, 300)

    def test_measurement_line_has_window_threshold_and_rate(self):
        self.use_window(240)
        site_block(self.access)
        line = self.access.stats_line()
        for expected in ("pencere=240/204", "eşik=240", "istek/dk", "bir kez düşürüldü", "son 60 dk: istek=2, engel=2"):
            self.assertIn(expected, line)


class ClientBudgetTests(unittest.TestCase):
    def test_every_request_goes_through_the_window_and_waits_before_it_stretched_right_after_a_start(self):
        fake = FakeTime()
        access = access_with(fake)
        sleeps = []
        pages = [requests.Response()]
        pages[0].status_code = 200
        pages[0]._content = b"<html>Amazon product</html>"
        pages[0].encoding = "utf-8"
        session = SimpleNamespace(cookies=requests.cookies.RequestsCookieJar(), get=lambda *a, **k: pages[0],
                                  close=lambda: None)
        with patch.object(amazon_client, "curl_requests", SimpleNamespace(Session=lambda: session)):
            with AmazonClient(access=access, delay_range=(1, 4), sleep=sleeps.append) as client:
                client.fetch("https://www.amazon.com.tr/dp/B000000001", 10)
                client.fetch("https://www.amazon.com.tr/dp/B000000002", 10)
                # Right after a start every wait is doubled: 2 to 8 seconds instead of 1 to 4.
                self.assertEqual(len(sleeps), 2)
                self.assertTrue(all(2 <= seconds <= 8 for seconds in sleeps))
                self.assertEqual(access.counters["istek"], 2)
                self.assertEqual(access.window_count(), 2)

    def test_a_challenge_page_counts_as_a_block(self):
        fake = FakeTime()
        access = access_with(fake)
        page = requests.Response()
        page.status_code = 200
        page._content = '<form action="/errors/validateCaptcha">Amazon</form>'.encode()
        page.encoding = "utf-8"
        session = SimpleNamespace(cookies=requests.cookies.RequestsCookieJar(), get=lambda *a, **k: page, close=lambda: None)
        with patch.object(amazon_client, "curl_requests", SimpleNamespace(Session=lambda: session)):
            with AmazonClient(access=access, sleep=fake.sleep) as client:
                with self.assertRaises(Exception):
                    client.fetch("https://www.amazon.com.tr/dp/B000000001", 10)
        # Every block is site-wide: the limit is lowered once and the wave is counted.
        self.assertEqual((access.counters["engel"], access.waves_today()), (1, 1))
        self.assertTrue(access.lowered)

    def test_after_a_block_nothing_is_sent_until_the_hold_ends_then_a_new_visitor_starts(self):
        fake = FakeTime()
        access = access_with(fake)
        challenge = requests.Response()
        challenge.status_code = 200
        challenge._content = '<form action="/errors/validateCaptcha">Amazon</form>'.encode()
        challenge.encoding = "utf-8"
        product = requests.Response()
        product.status_code = 200
        product._content = b"<html>Amazon product</html>"
        product.encoding = "utf-8"
        answers = [challenge, product]
        sessions = []

        def new_session():
            session = SimpleNamespace(cookies=requests.cookies.RequestsCookieJar(), sent=[], closed=False)
            session.get = lambda url, **kwargs: (session.sent.append(url), answers.pop(0))[1]
            session.close = lambda: setattr(session, "closed", True)
            sessions.append(session)
            return session

        with patch.object(amazon_client, "curl_requests", SimpleNamespace(Session=new_session)):
            with AmazonClient(access=access, sleep=fake.sleep) as client:
                with self.assertRaises(BotProtectionHermesError):
                    client.fetch("https://www.amazon.com.tr/dp/B000000001", 10)
                fake.advance(60)
                with self.assertRaises(BotProtectionHermesError) as held:
                    client.fetch("https://www.amazon.com.tr/dp/B000000002", 10)
                self.assertEqual(held.exception.challenge_reason, "mola")
                self.assertEqual((len(sessions), len(sessions[0].sent), access.counters["istek"]), (1, 1, 1))
                fake.advance(AMAZON_BLOCK_HOLD_SECONDS)
                client.fetch("https://www.amazon.com.tr/dp/B000000002", 10)
        self.assertEqual(len(sessions), 2)
        self.assertTrue(sessions[0].closed)
        self.assertEqual(sessions[1].sent, ["https://www.amazon.com.tr/dp/B000000002"])

    def test_headers_name_one_consistent_chrome_and_never_force_a_reload(self):
        sent = {}

        def get(url, **kwargs):
            sent.update(kwargs)
            page = requests.Response()
            page.status_code = 200
            page._content = b"<html>Amazon product</html>"
            page.encoding = "utf-8"
            return page

        session = SimpleNamespace(cookies=requests.cookies.RequestsCookieJar(), get=get, close=lambda: None)
        with patch.object(amazon_client, "curl_requests", SimpleNamespace(Session=lambda: session)):
            with AmazonClient(access=access_with(FakeTime())) as client:
                client.fetch("https://www.amazon.com.tr/dp/B000000001", 10)
        headers = sent["headers"]
        self.assertEqual(sent["impersonate"], f"chrome{amazon_client.CHROME_MAJOR}")
        self.assertIn(f"Chrome/{amazon_client.CHROME_MAJOR}.0.0.0", headers["User-Agent"])
        self.assertIn(f'v="{amazon_client.CHROME_MAJOR}"', headers["sec-ch-ua"])
        self.assertIn("Linux", headers["User-Agent"])
        self.assertEqual(headers["sec-ch-ua-platform"], '"Linux"')
        for forbidden in ("Referer", "Cache-Control", "Pragma", "Accept-Encoding", "Sec-Fetch-Site", "Connection"):
            self.assertNotIn(forbidden, headers)


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

    def test_cookies_saved_before_the_last_block_are_left_behind(self):
        session = requests.Session()
        session.cookies.set("session-id", "marked", domain=".amazon.com.tr", path="/")
        save_cookies(session, self.path)
        saved_at = self.path.stat().st_mtime
        self.assertEqual(load_cookies(requests.Session(), self.path, blocked_at=saved_at + 1), 0)
        self.assertEqual(load_cookies(requests.Session(), self.path, blocked_at=saved_at - 60), 1)

    def test_missing_or_broken_file_restores_nothing(self):
        self.assertEqual(load_cookies(requests.Session(), self.path), 0)
        self.path.write_text("garbage", encoding="utf-8")
        self.assertEqual(load_cookies(requests.Session(), self.path), 0)
        self.assertEqual(load_cookies(requests.Session(), None), 0)


if __name__ == "__main__":
    unittest.main()


class LaneClientTests(unittest.TestCase):
    def make_client(self):
        fake = FakeTime()
        return AmazonClient(access=access_with(fake), sleep=fake.sleep)

    def test_the_lane_is_per_thread(self):
        import threading

        client = self.make_client()
        seen = {}

        def other():
            seen["start"] = client.lane
            client.lane = MAIN_LANE
            seen["other"] = client.lane

        client.lane = ""
        worker = threading.Thread(target=other)
        worker.start()
        worker.join()
        self.assertEqual((seen["start"], seen["other"], client.lane), ("", MAIN_LANE, ""))

    def test_requests_of_two_lanes_never_overlap_and_are_counted_per_lane(self):
        import threading
        import time as real_time

        client = self.make_client()
        running, overlaps = [0], [0]
        lock = threading.Lock()

        def slow_read():
            with lock:
                running[0] += 1
                overlaps[0] = max(overlaps[0], running[0])
            real_time.sleep(0.02)
            with lock:
                running[0] -= 1
            return "ok"

        def lane_worker(lane):
            client.lane = lane
            for _ in range(5):
                client._timed("curl", "https://www.amazon.com.tr/dp/B000000001", False, slow_read)

        workers = [threading.Thread(target=lane_worker, args=(lane,)) for lane in (MAIN_LANE, "")]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(overlaps[0], 1)
        self.assertEqual((client.access.counters["istek_depo"], client.access.counters["istek_tarama"]), (5, 5))


    def test_two_busy_lanes_take_turns_so_the_sweep_is_never_starved(self):
        import threading
        import time as real_time

        client = self.make_client()
        order, lock = [], threading.Lock()
        start = threading.Barrier(2)

        def read_in(lane):
            def read():
                with lock:
                    order.append(lane)
                real_time.sleep(0.005)
                return "ok"
            return read

        def lane_worker(lane):
            client.lane = lane
            start.wait()
            for _ in range(20):
                client._timed("curl", "https://www.amazon.com.tr/dp/B000000001", False, read_in(lane))

        workers = [threading.Thread(target=lane_worker, args=(lane,)) for lane in (MAIN_LANE, "")]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        # While both lanes had requests waiting, neither went twice in a row (the Depo lane no longer goes first).
        both_busy = order[:30]
        self.assertFalse(any(a == b for a, b in zip(both_busy, both_busy[1:])), both_busy)
        self.assertEqual((order.count(MAIN_LANE), order.count("")), (20, 20))


class RestoreTests(unittest.TestCase):
    """After a restart the window and the last hour come back from the database."""

    def setUp(self):
        self.fake = FakeTime()
        self.access = access_with(self.fake)
        LOG_LINES.clear()

    def finished(self, seconds_ago, outcome="ok", ms=2000):
        return (datetime.fromtimestamp(self.fake.wall() - seconds_ago, timezone.utc), ms, outcome)

    def test_window_and_last_hour_are_rebuilt(self):
        rows = [self.finished(30 * 60 + seconds) for seconds in range(0, 240)]  # 30–34 min ago: in the window
        rows += [self.finished(40 * 60), self.finished(50 * 60, "bot_korumasi"), self.finished(55 * 60, "http_503")]
        rows += [self.finished(2 * HOUR)]  # too old for both
        rows += [self.finished(60, "ReadTimeout")]  # a failure that is not a block
        self.access.restore(rows)
        self.assertEqual(self.access.window_count(), 241)
        line = self.access.stats_line()
        self.assertIn("son 60 dk: istek=244, engel=2", line)
        self.assertIn("pencere=241/", line)
        self.assertTrue(any("geri yüklendi: son 35 dk=241 istek | son 60 dk=244 istek, 2 engel" in item for item in LOG_LINES))
        # Restored starts leave the window when their 35 minutes are over.
        self.fake.advance(5 * 60 + 1)
        self.assertEqual(self.access.window_count(), 1)

    def test_first_block_after_a_restart_uses_the_real_window(self):
        self.access.restore([self.finished(seconds * 12) for seconds in range(150)])  # 150 requests, 30 minutes
        make_requests(self.access, self.fake, 1, blocked=True)
        self.access.request_finished(True, "B000000002")
        self.assertEqual(self.access.threshold, 151)  # not 1: the requests before the restart count

    def test_a_restored_full_window_makes_the_sweep_wait_as_without_a_restart(self):
        self.access.restore([self.finished(seconds * 4) for seconds in range(AMAZON_WINDOW_START_LIMIT)])
        before = self.fake.now
        self.access.wait_for_window()
        self.assertGreater(self.fake.now, before)
        self.assertLess(self.access.window_count(), self.access.limit_for(""))
        self.assertEqual(self.access.limit_for(""), self.access.limit - self.access.depo_reserve())

    def test_nothing_to_restore_changes_nothing(self):
        self.access.restore([])
        self.assertEqual(self.access.window_count(), 0)
        self.assertEqual(self.access.limit, AMAZON_WINDOW_START_LIMIT)

    def test_only_one_thread_writes_the_measurement_line(self):
        self.fake.advance(10 * 60)
        self.assertEqual([self.access.stats_due() for _ in range(3)], [True, False, False])

