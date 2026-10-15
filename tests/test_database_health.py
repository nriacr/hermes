"""Database startup check and periodic WAL cleanup."""

import unittest
from pathlib import Path

from support import LOG_LINES, TempData

from hermes.database import Database


class DatabaseHealthTests(unittest.TestCase):
    def setUp(self):
        self.data = TempData()
        self.addCleanup(self.data.cleanup)
        self.path = self.data.root / "health.db"
        self.addCleanup(lambda: Database.at(self.path).close())
        LOG_LINES.clear()

    def test_a_healthy_file_opens_silently(self):
        database = Database.at(self.path)
        database.connect()
        self.assertEqual(database.integrity_error, "")
        self.assertFalse(any("SAĞLIKSIZ" in line for line in LOG_LINES))

    def test_a_damaged_file_is_reported_and_left_in_place(self):
        database = Database.at(self.path)
        for index in range(200):
            database.snapshot(f"kayit{index}", {"deger": "x" * 200})
        database.checkpoint(force=True)
        database.close()
        raw = bytearray(self.path.read_bytes())
        for offset in range(4096 * 2, len(raw) - 4096, 97):  # damage table pages, keep the header readable
            raw[offset] ^= 0xFF
        self.path.write_bytes(bytes(raw))
        damaged = self.path.read_bytes()
        reopened = Database.at(self.path)
        reopened.connect()
        self.assertTrue(reopened.integrity_error)
        self.assertTrue(any("VERİTABANI SAĞLIKSIZ" in line for line in LOG_LINES))
        self.assertEqual(self.path.read_bytes(), damaged)  # nothing was deleted, repaired or rewritten

    def test_checkpoint_truncates_the_wal(self):
        database = Database.at(self.path)
        for index in range(50):
            database.snapshot(f"kayit{index}", {"deger": "y" * 500})
        wal = Path(f"{self.path}-wal")
        self.assertGreater(wal.stat().st_size, 0)
        database.checkpoint(force=True)
        self.assertEqual(wal.stat().st_size, 0)

    def test_checkpoint_is_skipped_until_the_interval_has_passed(self):
        database = Database.at(self.path)
        database.snapshot("kayit", {"deger": 1})
        wal = Path(f"{self.path}-wal")
        size = wal.stat().st_size
        database.checkpoint()
        self.assertEqual(wal.stat().st_size, size)


if __name__ == "__main__":
    unittest.main()
