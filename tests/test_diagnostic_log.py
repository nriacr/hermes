"""Rotating JSON-lines diagnostic log."""

import json
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401

from hermes import logging_utils


class DiagnosticLogTest(unittest.TestCase):
    def tearDown(self):
        logging_utils._file_sink = None

    def test_lines_are_redacted_json_and_rotate(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "hermes.log.jsonl"
            logging_utils.enable_file_log(path, max_bytes=300, backups=2)
            logging_utils.log("okuma token=ABCDEF123 tamam")
            for number in range(20):
                logging_utils.log(f"satır {number} " + "x" * 40)
            logging_utils._file_sink.close()
            files = sorted(Path(folder).glob("hermes.log.jsonl*"))
            self.assertLessEqual(len(files), 3)
            self.assertGreater(len(files), 1)
            text = "".join(item.read_text(encoding="utf-8") for item in files)
            self.assertNotIn("ABCDEF123", text)
            for line in text.splitlines():
                record = json.loads(line)
                self.assertEqual(set(record), {"ts", "msg"})

    def test_unwritable_path_is_ignored(self):
        logging_utils.enable_file_log("/nonexistent-folder/hermes.log.jsonl")
        self.assertIsNone(logging_utils._file_sink)
        logging_utils.log("devam")


class SqlBackedFilesTest(unittest.TestCase):
    def test_adopt_then_database_is_authoritative(self):
        from hermes import storage
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            (data / "amazon_cookies.json").write_text('[{"name": "a"}]', encoding="utf-8")
            (data / "status.json").write_text('{"ok": 1}', encoding="utf-8")
            (data / "options.json").write_text('{"x": 1}', encoding="utf-8")
            self.assertEqual(storage.adopt_json_files(data), ["amazon_cookies.json", "status.json"])
            self.assertEqual(storage.adopt_json_files(data), [])  # idempotent
            # a later save updates both; an edited file does not override the database
            storage.save_json(data / "status.json", {"ok": 2})
            (data / "status.json").write_text('{"ok": 99}', encoding="utf-8")
            self.assertEqual(storage.load_json(data / "status.json", {}), {"ok": 2})
            self.assertEqual(storage.load_json(data / "amazon_cookies.json", []), [{"name": "a"}])
            # options.json stays a plain file
            (data / "options.json").write_text('{"x": 2}', encoding="utf-8")
            self.assertEqual(storage.load_json(data / "options.json", {}), {"x": 2})
            storage.Database.at(data / "hermes.db").close()
