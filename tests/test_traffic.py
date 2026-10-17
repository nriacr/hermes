"""Internet traffic counters: wire/decompressed/sent bytes, storage per day and the statistics section."""

import gzip
import tempfile
import threading
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import requests

import support  # noqa: F401 - puts the add-on code on the import path
from hermes import traffic
from hermes.history import History, read_traffic
from hermes.providers.http import MeasuredSession
from hermes.web.statistics import render_traffic

PAGE = ("<html>" + "fiyat 123 TL " * 4000 + "</html>").encode()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/page")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = gzip.compress(PAGE) if "gzip" in self.headers.get("Accept-Encoding", "") else PAGE
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        if body is not PAGE:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class CountingTests(unittest.TestCase):
    def setUp(self):
        traffic._pending.clear()
        traffic._window.clear()
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        traffic._pending.clear()
        traffic._window.clear()

    def test_compressed_page_counts_wire_and_decompressed_bytes(self):
        with traffic.bind("amazon"), MeasuredSession(lambda *_args: None) as session:
            response = session.get(f"{self.base}/page")
        count, wire, body, sent = traffic._pending["amazon"]
        self.assertEqual(count, 1)
        self.assertEqual(body, len(PAGE))
        self.assertEqual(len(response.content), len(PAGE))
        self.assertLess(wire, body // 5)  # gzip: far fewer bytes crossed the wire
        self.assertGreater(wire, 100)
        self.assertGreater(sent, 50)  # request line + headers

    def test_redirect_hops_are_counted(self):
        with traffic.bind("zara"), traffic.CountedSession() as session:
            session.get(f"{self.base}/redirect")
        self.assertEqual(traffic._pending["zara"][0], 2)

    def test_unbound_thread_goes_to_other(self):
        with traffic.CountedSession() as session:
            session.get(f"{self.base}/page")
        self.assertIn(traffic.UNBOUND_SITE, traffic._pending)

    def test_bind_is_per_thread(self):
        seen = {}
        with traffic.bind("hm"):
            thread = threading.Thread(target=lambda: seen.update(site=traffic.current_site()))
            thread.start()
            thread.join()
            self.assertEqual(traffic.current_site(), "hm")
        self.assertEqual(seen["site"], traffic.UNBOUND_SITE)
        self.assertEqual(traffic.current_site(), traffic.UNBOUND_SITE)

    def test_curl_response_uses_curl_sizes_and_other_responses_are_ignored(self):
        curl = SimpleNamespace(download_size=1000, header_size=300, request_size=400, upload_size=0, content=b"x" * 5000)
        self.assertIs(traffic.count_curl(curl, "amazon"), curl)
        self.assertEqual(traffic._pending["amazon"], [1, 1300, 5000, 400])
        traffic.count_curl(requests.Response(), "amazon")  # no curl attributes: not counted twice
        self.assertEqual(traffic._pending["amazon"][0], 1)

    def test_counting_never_raises(self):
        for broken in (None, object(), MagicMock(), SimpleNamespace(download_size="x", header_size=None)):
            traffic.count_requests(broken, "amazon")
            self.assertIs(traffic.count_curl(broken, "amazon"), broken)

    def test_a_failed_request_is_not_hidden_by_counting(self):
        with traffic.CountedSession() as session, self.assertRaises(requests.ConnectionError):
            session.get("http://127.0.0.1:9/")


class StorageTests(unittest.TestCase):
    def setUp(self):
        traffic._pending.clear()
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "hermes.db"
        self.history = History(self.path)

    def tearDown(self):
        self.history.close()
        self.folder.cleanup()
        traffic._pending.clear()

    def test_flush_adds_to_one_row_per_day_and_site(self):
        traffic.add("amazon", 1000, 9000, 500)
        traffic.flush(self.history)
        traffic.add("amazon", 2000, 8000, 700, 2)
        traffic.add("zara", 10, 20, 30)
        traffic.flush(self.history)
        rows = {site: (count, wire, body, sent) for _day, site, count, wire, body, sent in read_traffic(self.path, "2000-01-01")}
        self.assertEqual(rows, {"amazon": (3, 3000, 17000, 1200), "zara": (1, 10, 20, 30)})
        self.assertEqual(len({row[0] for row in read_traffic(self.path, "2000-01-01")}), 1)
        self.assertEqual(traffic._pending, {})

    def test_a_failed_write_keeps_the_numbers_for_the_next_flush(self):
        failing = MagicMock()
        failing.record_traffic.return_value = False
        traffic.add("amazon", 100, 200, 50)
        traffic.flush(failing)
        self.assertEqual(traffic._pending["amazon"], [1, 100, 200, 50])
        traffic.flush(self.history)
        self.assertEqual(read_traffic(self.path, "2000-01-01")[0][3], 100)

    def test_summary_line_appears_after_ten_minutes_only(self):
        traffic._window.clear()
        traffic.add("amazon", 2_000_000, 9_000_000, 10_000, 4)
        self.assertEqual(traffic._summary_line(), "")
        traffic._window_since -= traffic.LOG_EVERY_SECONDS + 1
        line = traffic._summary_line()
        self.assertIn("amazon 2.00 MB (4 istek)", line)
        self.assertEqual(traffic._window, {})


class PageTests(unittest.TestCase):
    NOW = datetime(2026, 10, 17, 15, 0).astimezone()

    def test_empty_database_explains_that_measuring_just_started(self):
        self.assertIn("Henüz ölçüm yok.", render_traffic([], self.NOW))

    def test_section_shows_tiles_sites_and_days(self):
        rows = [("2026-10-16", "amazon", 100, 30_000_000, 90_000_000, 1_000_000),
                ("2026-10-17", "amazon", 50, 15_000_000, 45_000_000, 500_000),
                ("2026-10-17", "hepsiburada", 20, 5_000_000, 12_000_000, 200_000)]
        html = render_traffic(rows, self.NOW)
        self.assertIn("20,7 MB", html)          # today: 15 + 0.5 + 5 + 0.2
        self.assertIn("51,7 MB", html)          # seven-day total
        self.assertIn("Amazon", html)
        self.assertIn("Günlük internet kullanımı", html)
        self.assertIn("17.10.2026", html)
        self.assertNotIn("<script", html)

    def test_site_names_are_escaped(self):
        html = render_traffic([("2026-10-17", "<b>x</b>", 1, 10, 10, 10)], self.NOW)
        self.assertNotIn("<b>x</b>", html)


if __name__ == "__main__":
    unittest.main()
