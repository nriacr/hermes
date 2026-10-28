"""Hepsiburada's home-page warm-up runs once per session, and again after a failed read."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import support  # noqa: F401 - puts the add-on code on the import path
from hermes.providers import hepsiburada
from hermes.providers.hepsiburada import HOME_URL, fetch_hepsiburada_page

PRODUCT = "https://www.hepsiburada.com/urun-p-HBC000001"
GOOD = "<html><body>Sepete ekle 1.000,00 TL " + "x" * 400 + "</body></html>"


def reply(status=200, text=GOOD):
    response = SimpleNamespace(status_code=status, headers={"content-type": "text/html; charset=utf-8"}, text=text,
                               url=PRODUCT, encoding="utf-8", content=text.encode(), history=[])

    def raise_for_status():
        if status >= 400:
            raise hepsiburada.HttpStatusHermesError(status, PRODUCT)
    response.raise_for_status = raise_for_status
    return response


class FakeSession:
    def __init__(self, page_status=200, home_status=200):
        self.calls = []
        self.page_status = page_status
        self.home_status = home_status

    def get(self, url, **_kwargs):
        self.calls.append(url)
        return reply(self.home_status if url == HOME_URL else self.page_status)


class WarmUpTests(unittest.TestCase):
    def test_home_page_is_requested_once_for_many_reads(self):
        session = FakeSession()
        for _ in range(4):
            fetch_hepsiburada_page(session, PRODUCT, 5)
        self.assertEqual(session.calls.count(HOME_URL), 1)
        self.assertEqual(session.calls[0], HOME_URL)
        self.assertEqual(len([call for call in session.calls if call != HOME_URL]), 4)

    def test_each_new_session_warms_up_first(self):
        for _ in range(2):
            session = FakeSession()
            fetch_hepsiburada_page(session, PRODUCT, 5)
            self.assertEqual(session.calls[0], HOME_URL)

    def test_a_failed_warm_up_is_tried_again_on_the_next_read(self):
        session = FakeSession(home_status=503)
        fetch_hepsiburada_page(session, PRODUCT, 5)
        fetch_hepsiburada_page(session, PRODUCT, 5)
        self.assertEqual(session.calls.count(HOME_URL), 2)

    def test_a_failed_read_warms_up_again_next_time(self):
        session = FakeSession(page_status=403)
        with patch.object(hepsiburada, "curl_requests", None), patch.object(hepsiburada, "CountedSession", lambda: FakeSession(page_status=403)), self.assertRaises(Exception):
            fetch_hepsiburada_page(session, PRODUCT, 5)
        session.page_status = 200
        fetch_hepsiburada_page(session, PRODUCT, 5)
        self.assertEqual(session.calls.count(HOME_URL), 2)
        fetch_hepsiburada_page(session, PRODUCT, 5)
        self.assertEqual(session.calls.count(HOME_URL), 2)  # healthy again: no more warm-ups


class AttemptTallyTests(unittest.TestCase):
    def setUp(self):
        hepsiburada._attempts.clear()
        hepsiburada._attempts_since = __import__("time").monotonic()

    def test_attempts_are_tallied_by_method_and_result(self):
        session = FakeSession()
        fetch_hepsiburada_page(session, PRODUCT, 5)
        self.assertEqual(hepsiburada._attempts[("home", "200")], 1)
        self.assertEqual(hepsiburada._attempts[("requests", "ok")], 1)

    def test_failures_are_tallied_with_their_status_and_the_summary_line_is_logged_after_ten_minutes(self):
        session = FakeSession(page_status=403)
        with patch.object(hepsiburada, "curl_requests", None), patch.object(hepsiburada, "CountedSession", lambda: FakeSession(page_status=403)), \
                self.assertRaises(Exception):
            fetch_hepsiburada_page(session, PRODUCT, 5)
        self.assertGreaterEqual(hepsiburada._attempts[("requests", "403")], 1)
        self.assertGreaterEqual(hepsiburada._attempts[("requests_fresh", "403")], 1)
        hepsiburada._attempts_since -= hepsiburada.ATTEMPT_LOG_SECONDS + 1
        with patch.object(hepsiburada, "log") as log:
            hepsiburada.note_attempt("curl", "ok")
        self.assertIn("Hepsiburada deneme özeti", log.call_args.args[0])
        self.assertIn("requests: 403=", log.call_args.args[0])
        self.assertEqual(len(hepsiburada._attempts), 0)


class CurlFirstTests(unittest.TestCase):
    """After curl_cffi read a url, later reads go there first; every failure falls back to the full original order."""

    def setUp(self):
        hepsiburada._curl_winner.clear()
        self.curl_calls = []
        self.curl_status = 200
        outer = self

        class FakeCurlSession:
            def get(self, url, **_kwargs):
                outer.curl_calls.append(url)
                return reply(outer.curl_status)

        self.fake_curl = SimpleNamespace(Session=FakeCurlSession)

    def blocked_requests_world(self):
        return (patch.object(hepsiburada, "curl_requests", self.fake_curl),
                patch.object(hepsiburada, "CountedSession", lambda: FakeSession(page_status=403)))

    def test_first_read_goes_to_curl_without_blocked_requests_and_remembers_it(self):
        session = FakeSession(page_status=403, home_status=403)
        first, second = self.blocked_requests_world()
        with first, second:
            fetch_hepsiburada_page(session, PRODUCT, 5)
        self.assertEqual(session.calls, [])                   # no warm-up or python-requests read that 403s
        self.assertEqual(len(self.curl_calls), 1)
        self.assertIn(PRODUCT, hepsiburada._curl_winner)

    def test_remembered_url_goes_straight_to_curl_with_no_other_request(self):
        hepsiburada._curl_winner[PRODUCT] = PRODUCT
        session = FakeSession(page_status=403, home_status=403)
        first, second = self.blocked_requests_world()
        with first, second:
            page = fetch_hepsiburada_page(session, PRODUCT, 5)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(session.calls, [])
        self.assertEqual(self.curl_calls, [PRODUCT])

    def test_a_failed_shortcut_runs_the_whole_original_order_and_forgets_the_address(self):
        hepsiburada._curl_winner[PRODUCT] = PRODUCT
        session = FakeSession()                                # python requests works now
        self.curl_status = 403                                 # but curl is refused
        with patch.object(hepsiburada, "curl_requests", self.fake_curl):
            page = fetch_hepsiburada_page(session, PRODUCT, 5)
        self.assertEqual(page.status_code, 200)               # the read is not lost
        self.assertEqual(session.calls[0], HOME_URL)          # after curl, the requests path (warm-up first) ran
        self.assertNotIn(PRODUCT, hepsiburada._curl_winner)

    def test_without_curl_nothing_is_remembered(self):
        with patch.object(hepsiburada, "curl_requests", None):
            fetch_hepsiburada_page(FakeSession(), PRODUCT, 5)
        self.assertEqual(hepsiburada._curl_winner, {})


if __name__ == "__main__":
    unittest.main()
