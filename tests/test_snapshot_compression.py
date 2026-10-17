import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hermes import storage
from hermes.database import COMPRESSED_MARKER, Database, decode_payload, encode_payload, read_snapshot


def _state(price="100"):
    return {f"offer-{n}": {"last_price": price, "title": "Ürün " * 40, "url": f"https://example.test/{n}"} for n in range(80)}


class SnapshotCompressionTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp(prefix="hermes-db-")) / "hermes.db"
        self.database = Database.at(self.path)
        self.addCleanup(self.database.close)

    def stored(self, name="state.json"):
        db = sqlite3.connect(self.path)
        try:
            return db.execute("SELECT payload FROM snapshots WHERE name=?", (name,)).fetchone()[0]
        finally:
            db.close()

    def test_large_snapshot_is_stored_smaller_and_read_back_identically(self):
        state = _state()
        self.database.snapshot("state.json", state)
        stored = self.stored()
        self.assertIsInstance(stored, bytes)
        self.assertTrue(stored.startswith(COMPRESSED_MARKER))
        self.assertLess(len(stored), len(json.dumps(state, ensure_ascii=False)) / 4)
        self.assertEqual(read_snapshot(self.path, "state.json"), state)

    def test_small_and_legacy_plain_snapshots_stay_readable(self):
        self.database.snapshot("small.json", {"a": 1})
        self.assertEqual(self.stored("small.json"), '{"a": 1}')
        legacy = json.dumps(_state(), ensure_ascii=False)
        self.database.connect().execute("INSERT OR REPLACE INTO snapshots VALUES (?,?)", ("state.json", legacy))
        self.assertEqual(read_snapshot(self.path, "state.json"), _state())
        self.assertEqual(decode_payload(legacy), legacy)

    def test_unchanged_snapshot_is_not_written_again_but_a_change_is(self):
        self.database.snapshot("state.json", _state())
        before = self.database.connect().execute("PRAGMA data_version").fetchone()[0]
        with mock.patch.object(self.database, "connect", wraps=self.database.connect) as connect:
            self.database.snapshot("state.json", _state())
            connect.assert_not_called()
        self.database.snapshot("state.json", _state("90"))
        self.assertEqual(read_snapshot(self.path, "state.json"), _state("90"))
        self.assertIsNotNone(before)

    def test_failed_transaction_is_not_remembered_as_written(self):
        with self.assertRaises(sqlite3.Error):
            self.database.transaction([("INSERT OR REPLACE INTO snapshots VALUES (?,?)", ("state.json", json.dumps(_state()))),
                                       ("INSERT INTO missing_table VALUES (1)", ())])
        self.assertIsNone(read_snapshot(self.path, "state.json"))
        self.database.snapshot("state.json", _state())
        self.assertEqual(read_snapshot(self.path, "state.json"), _state())

    def test_incompressible_or_broken_compression_falls_back_to_plain_text(self):
        text = json.dumps(_state())
        with mock.patch("hermes.database.zlib.decompress", return_value=b"different"):
            self.assertEqual(encode_payload(text), text)


class JsonExportSkipTests(unittest.TestCase):
    def test_identical_export_does_not_rewrite_the_file(self):
        path = Path(tempfile.mkdtemp(prefix="hermes-json-")) / "state.json"
        storage._LAST_TEXT.pop(path, None)
        storage.export_json(path, {"a": 1}, now=True)
        first = path.stat().st_mtime_ns
        with mock.patch.object(storage.tempfile, "NamedTemporaryFile") as writer:
            storage.export_json(path, {"a": 1}, now=True)
            writer.assert_not_called()
        self.assertEqual(path.stat().st_mtime_ns, first)
        storage.export_json(path, {"a": 2}, now=True)
        self.assertEqual(json.loads(path.read_text()), {"a": 2})


if __name__ == "__main__":
    unittest.main()
