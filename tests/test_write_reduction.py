import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hermes import storage, writestats
from hermes.database import Database
from hermes.monitor import results


class ExportDelayTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="hermes-export-"))
        self.path = self.dir / "state.json"
        patcher = mock.patch.object(storage, "EXPORT_EVERY_SECONDS", 60)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(storage._PENDING.clear)
        self.addCleanup(storage._LAST_EXPORT.clear)

    def test_second_export_within_a_minute_waits_and_flush_writes_newest(self):
        storage.export_json(self.path, {"a": 1})
        storage.export_json(self.path, {"a": 2})
        self.assertEqual(json.loads(self.path.read_text()), {"a": 1})
        storage.flush_exports()
        self.assertEqual(json.loads(self.path.read_text()), {"a": 2})

    def test_now_writes_immediately(self):
        storage.export_json(self.path, {"a": 1})
        storage.export_json(self.path, {"a": 2}, now=True)
        self.assertEqual(json.loads(self.path.read_text()), {"a": 2})

    def test_unlisted_files_are_never_delayed(self):
        other = self.dir / "options.json"
        storage.export_json(other, {"a": 1})
        storage.export_json(other, {"a": 2})
        self.assertEqual(json.loads(other.read_text()), {"a": 2})

    def test_later_state_changes_do_not_leak_into_waiting_copy(self):
        state = {"a": 1}
        storage.export_json(self.path, state)
        storage.export_json(self.path, state)
        state["a"] = 99
        storage.flush_exports()
        self.assertEqual(json.loads(self.path.read_text()), {"a": 1})


class WriteStatsTests(unittest.TestCase):
    def test_summary_is_logged_after_the_interval(self):
        with mock.patch.object(writestats, "LOG_EVERY_SECONDS", 0), mock.patch.object(writestats, "log") as log:
            writestats.record("deneme", 2_000_000)
        self.assertIn("deneme 2.0 MB", log.call_args[0][0])

    def test_transaction_counts_snapshot_bytes_and_remembers_state_write(self):
        database = Database.at(Path(tempfile.mkdtemp(prefix="hermes-db-")) / "hermes.db")
        with mock.patch.object(writestats, "record") as record:
            database.transaction([("INSERT OR REPLACE INTO snapshots VALUES (?,?)", ("state.json", "x" * 10))])
        record.assert_any_call("state.json SQL", 10)
        self.assertLess(database.state_snapshot_age(), 5)
        database.close()


class QuietReadTests(unittest.TestCase):
    def test_only_timestamps_count_as_quiet(self):
        base = {"last_price": "100", "last_price_checked_at": "a", "last_checked_at": "a"}
        self.assertEqual(results._without_times(base), results._without_times({**base, "last_checked_at": "b"}))
        self.assertNotEqual(results._without_times(base), results._without_times({**base, "last_price": "99"}))


if __name__ == "__main__":
    unittest.main()
