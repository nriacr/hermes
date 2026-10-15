import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from support import APP_PATH  # noqa: F401

from hermes import constants, sitepause
from hermes.telegram import commands


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        base = Path(self.dir.name)
        self.patches = [patch.object(constants, "SITE_PAUSE_PATH", base / "site_pauses.json"),
                        patch.object(commands, "DATABASE_PATH", base / "hermes.db"),
                        patch.object(commands, "SUMMARY_PATH", base / "summary.json")]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.dir.cleanup()

    def test_plain_text_is_not_a_command(self):
        self.assertIsNone(commands.reply_for("https://www.amazon.com.tr/dp/B000000001"))
        self.assertIsNone(commands.reply_for("40000"))

    def test_pause_and_resume_by_site_name_or_label(self):
        self.assertIn("Amazon okuması", commands.reply_for("/duraklat amazon 2"))
        self.assertTrue(sitepause.is_paused("amazon"))
        self.assertFalse(sitepause.is_paused("hepsiburada"))
        self.assertIn("Mola: Amazon", commands.reply_for("/durum"))
        self.assertIn("molası bitti", commands.reply_for("/devam Amazon"))
        self.assertFalse(sitepause.is_paused("amazon"))
        self.assertIn("zaten molada değildi", commands.reply_for("/devam amazon"))
        commands.reply_for("/duraklat Beymen Club".split()[0] + " beymenclub")
        self.assertTrue(sitepause.is_paused("beymenclub"))
        commands.reply_for("/duraklat h&m")
        self.assertTrue(sitepause.is_paused("hm"))

    def test_pause_is_bounded_and_expires_by_itself(self):
        until = sitepause.pause("amazon", 1000)
        self.assertLess(until - datetime.now(timezone.utc), timedelta(hours=72, minutes=1))
        with patch.object(sitepause, "local_now", return_value=datetime.now(timezone.utc) + timedelta(hours=73)):
            self.assertFalse(sitepause.is_paused("amazon"))

    def test_bad_arguments_get_a_helpful_answer(self):
        self.assertIn("Hangi site?", commands.reply_for("/duraklat"))
        self.assertIn("Hangi site?", commands.reply_for("/duraklat bilinmeyen"))
        self.assertIn("Saat bir sayı", commands.reply_for("/duraklat amazon çok"))
        self.assertIn("Bilinmeyen komut", commands.reply_for("/sil"))
        self.assertIn("/durum", commands.reply_for("/yardim"))


if __name__ == "__main__":
    unittest.main()
