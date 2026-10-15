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
