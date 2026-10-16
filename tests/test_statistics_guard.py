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


if __name__ == "__main__":
    unittest.main()
