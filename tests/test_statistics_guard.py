import threading
import unittest
from unittest import mock

from hermes.web import statistics as page


class GuardedSectionTests(unittest.TestCase):
    def test_returns_value(self):
        self.assertEqual(page.guarded("a", lambda: 5, 0), 5)

    def test_error_falls_back(self):
        def boom():
            raise ValueError("x")
        with mock.patch.object(page, "log") as log:
            self.assertEqual(page.guarded("a", boom, "yedek"), "yedek")
        self.assertIn("ValueError", log.call_args[0][0])

    def test_stuck_section_does_not_block_page(self):
        release = threading.Event()
        try:
            with mock.patch.object(page, "SECTION_LIMIT_SECONDS", 0.2), mock.patch.object(page, "log") as log:
                self.assertEqual(page.guarded("takılan", release.wait, "yedek"), "yedek")
            self.assertIn("takılan", log.call_args[0][0])
        finally:
            release.set()


class CacheTests(unittest.TestCase):
    def setUp(self):
        page.clear_statistics_cache()
        self.addCleanup(page.clear_statistics_cache)

    def test_second_call_is_served_from_memory(self):
        with mock.patch.object(page, "_statistics_html", return_value="<p>sayfa</p>") as build:
            self.assertEqual(page.statistics_live_html("", {"p": ["24h"]}), "<p>sayfa</p>")
            self.assertEqual(page.statistics_live_html("", {"p": ["24h"]}), "<p>sayfa</p>")
        self.assertEqual(build.call_count, 1)

    def test_stale_page_is_shown_while_new_one_is_prepared(self):
        with mock.patch.object(page, "_statistics_html", side_effect=["eski", "yeni"]):
            self.assertEqual(page.statistics_live_html("", {"p": ["24h"]}), "eski")
            with mock.patch.object(page, "CACHE_FRESH_SECONDS", 0.0):
                page._cache["24h"] = (page._cache["24h"][0] - 5, 0.0, "eski")
                self.assertEqual(page.statistics_live_html("", {"p": ["24h"]}), "eski")
                for _ in range(50):
                    if page._cache["24h"][2] == "yeni":
                        break
                    threading.Event().wait(0.1)
            self.assertEqual(page._cache["24h"][2], "yeni")


if __name__ == "__main__":
    unittest.main()


class NetworkOutageTests(unittest.TestCase):
    def reads(self, rows):
        from datetime import datetime, timedelta
        from hermes.history import Read
        start = datetime(2026, 10, 28, 23, 23).astimezone()
        return [Read(start + timedelta(minutes=m), site, outcome, 0, "k", "p", detail) for m, site, outcome, detail in rows]

    def test_two_sites_failing_together_are_not_counted(self):
        rows = self.reads([(0, "togg", "connection", "SSLError"), (1, "amazon", "unreadable", "Operation timed out"),
                           (2, "amazon", "partial", ""), (3, "amazon", "ok", "")])
        kept, dropped = page.without_network_outages(rows)
        self.assertEqual((dropped, [item.outcome for item in kept]), (3, ["ok"]))

    def test_single_site_failure_stays(self):
        rows = self.reads([(0, "amazon", "timeout", ""), (90, "amazon", "unreadable", "Operation timed out")])
        self.assertEqual(page.without_network_outages(rows)[1], 0)

    def test_unreadable_without_network_words_stays(self):
        rows = self.reads([(0, "togg", "unreadable", "fiyat yok"), (1, "amazon", "unreadable", "fiyat yok")])
        self.assertEqual(page.without_network_outages(rows)[1], 0)
