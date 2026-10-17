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


if __name__ == "__main__":
    unittest.main()
