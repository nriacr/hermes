"""SQLite history: JSON migration, price points, cycles, measurements and panel reads."""

import hashlib
import json
import sqlite3
import threading
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import requests

from support import LOG_LINES, TempData, config, key, monitor, notifier, watch

from hermes import history as history_module
from hermes.errors import BotProtectionHermesError, HermesError, HttpStatusHermesError, OutOfStockHermesError, PriceUnavailableHermesError
from hermes.history import History, read_cycles, read_prices, read_site_reads, read_site_requests
from hermes.models import OfferResult
from hermes.monitor import cycle as cycle_module, runner, scheduling, state as state_ops, summary
from hermes.utils import SystemLoad

AMAZON = "https://www.amazon.com.tr/dp/B000000001"
HEPSIBURADA = "https://www.hepsiburada.com/urun-p-HBC000001"


def realistic_state():
    """A trimmed copy of a live 3.0.1 `state.json`: watch, offers, guard and metadata."""
    return {
        "watch_amazon_iphone_17_pro_https_www_amazon_com_tr_dp_b0fqftyptq": {
            "site": "amazon", "watch_name": "iPhone 17 Pro", "tracking_id": "trk-1",
            "configured_url": "https://www.amazon.com.tr/dp/B0FQFTYPTQ", "size": "", "check_now_token": "",
            "last_checked_at": "2026-10-03T20:12:00+00:00", "include_variations": True,
            "offer_keys": ["watch_offer_amazon_trk_1_b0fqftyptq_normal", "watch_offer_amazon_trk_1_b0fqftyptq_warehouse"],
            "last_error": None, "last_error_status": None, "amazon_partial_result": False,
            "amazon_no_offer_retry_after": None, "unavailable_variants": [],
        },
        "watch_offer_amazon_trk_1_b0fqftyptq_normal": {
            "title": "Apple iPhone 17 Pro 256 GB", "url": "https://www.amazon.com.tr/dp/B0FQFTYPTQ", "site": "amazon",
            "last_price": "84999.00", "min_price": "79999.00", "max_price": "89999.00",
            "min_price_at": "2026-09-28T08:00:00+00:00", "max_price_at": "2026-09-20T10:00:00+00:00",
            "last_checked_at": "2026-10-03T20:12:00+00:00", "last_price_checked_at": "2026-10-03T20:11:58.101+00:00",
            "was_below_target": False, "last_alerted_price": "79999.00", "last_alerted_at": "2026-09-28T08:00:05+00:00",
            "is_warehouse": False, "priority": "high",
        },
        "watch_offer_amazon_trk_1_b0fqftyptq_warehouse": {
            "title": "Apple iPhone 17 Pro 256 GB (Depo)", "site": "amazon", "is_warehouse": True,
            # Never moved: min, max and last are the same price and time.
            "last_price": "70000", "min_price": "70000", "max_price": "70000",
            "min_price_at": "2026-10-01T09:00:00+03:00", "max_price_at": "2026-10-01T09:00:00+03:00",
            "last_checked_at": "2026-10-01T06:00:00+00:00",
        },
        "watch_offer_hepsiburada_zeytinyagi_normal": {
            "watch_name": "Palamidas", "site": "hepsiburada", "last_price": "1988", "min_price": "1850",
            "max_price": "abc", "min_price_at": "2026-09-30T12:00:00+00:00", "max_price_at": "",
            "last_checked_at": "2026-10-03T20:20:29+00:00",
        },
        # Entries without prices and broken values are ignored.
        "watch_offer_broken": {"last_price": "-5", "last_checked_at": "2026-10-03T20:00:00+00:00"},
        "watch_offer_list": ["not", "a", "dict"],
        "_meta": {
            "amazon_protection": {"watch_amazon_x": {"blocked_at": "2026-10-03T20:00:00+00:00", "kind": "captcha"}},
            "price_history_reset_at": "2026-09-01T00:00:00+00:00",
            "summary_drop": {"last_count": 4},
        },
    }


def realistic_cycles(now):
    return [
        {"checked_at": (now - timedelta(days=2)).isoformat(), "duration_seconds": 270.5},
        {"checked_at": (now - timedelta(hours=3)).astimezone(timezone(timedelta(hours=3))).isoformat(), "duration_seconds": 1.2},
        {"checked_at": "not a time", "duration_seconds": 3},
        {"checked_at": now.isoformat(), "duration_seconds": "x"},
        {"checked_at": now.isoformat(), "duration_seconds": float("nan")},
        "garbage",
    ]


class HistoryCase(unittest.TestCase):
    def setUp(self):
        self.data = TempData()
        self.history = History.at(self.data.files.database)
        LOG_LINES.clear()

    def tearDown(self):
        self.data.cleanup()


class MigrationTests(HistoryCase):
    def write_json(self, now):
        self.data.write_state(realistic_state())
        self.data.files.cycle_history.write_text(json.dumps(realistic_cycles(now)), encoding="utf-8")

    def digests(self):
        return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (self.data.files.state, self.data.files.cycle_history)}

    def test_first_start_copies_cycles_and_prices_and_leaves_json_untouched(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self.write_json(now)
        before = self.digests()

        self.history.migrate_json(self.data.files.state, self.data.files.cycle_history)

        self.assertEqual(self.digests(), before)
        cycles = read_cycles(self.data.files.database, now - timedelta(days=7), now + timedelta(minutes=1))
        self.assertEqual([duration for _, duration in cycles], [270.5, 1.2])
        # The +03:00 time is stored as the same instant.
        self.assertEqual(cycles[1][0], now - timedelta(hours=3))

        normal = read_prices(self.data.files.database, "watch_offer_amazon_trk_1_b0fqftyptq_normal")
        self.assertEqual([(at.astimezone(timezone.utc).isoformat(), price) for at, price in normal], [
            ("2026-09-20T10:00:00+00:00", Decimal("89999.00")),
            ("2026-09-28T08:00:00+00:00", Decimal("79999.00")),
            ("2026-10-03T20:11:58+00:00", Decimal("84999.00")),
        ])
        # Same price at the same instant (written in two time zones) is one point.
        warehouse = read_prices(self.data.files.database, "watch_offer_amazon_trk_1_b0fqftyptq_warehouse")
        self.assertEqual([price for _, price in warehouse], [Decimal("70000")])
        # A broken max price is skipped; the last price falls back to last_checked_at.
        olive = read_prices(self.data.files.database, "watch_offer_hepsiburada_zeytinyagi_normal")
        self.assertEqual([(at.astimezone(timezone.utc).isoformat(), price) for at, price in olive], [
            ("2026-09-30T12:00:00+00:00", Decimal("1850")),
            ("2026-10-03T20:20:29+00:00", Decimal("1988")),
        ])
        self.assertEqual(read_prices(self.data.files.database, "watch_offer_broken"), [])
        with sqlite3.connect(self.data.files.database) as db:
            titles = dict(db.execute("SELECT offer_key, title FROM prices GROUP BY offer_key"))
        self.assertEqual(titles["watch_offer_hepsiburada_zeytinyagi_normal"], "Palamidas")
        self.assertTrue(any("çevrim=2 | fiyat noktası=6" in line for line in LOG_LINES))

    def test_migration_runs_once(self):
        now = datetime.now(timezone.utc)
        self.write_json(now)
        self.history.migrate_json(self.data.files.state, self.data.files.cycle_history)
        self.history.migrate_json(self.data.files.state, self.data.files.cycle_history)
        History.at(self.data.files.database).close()
        History.at(self.data.files.database).migrate_json(self.data.files.state, self.data.files.cycle_history)
        self.assertEqual(len(read_cycles(self.data.files.database, now - timedelta(days=7), now + timedelta(minutes=1))), 2)

    def test_fresh_install_without_json_starts_empty(self):
        self.history.migrate_json(self.data.files.state, self.data.files.cycle_history)
        now = datetime.now(timezone.utc)
        self.assertEqual(read_cycles(self.data.files.database, now - timedelta(days=7)), [])
        self.assertFalse(self.data.files.state.exists())

    def test_database_uses_wal_and_panel_reads_while_writing(self):
        self.history.record_cycle(10)
        with sqlite3.connect(self.data.files.database) as db:
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.history._lock.acquire()  # a writer in the middle of a write
        try:
            reads = []
            reader = threading.Thread(target=lambda: reads.append(read_cycles(
                self.data.files.database, datetime.now(timezone.utc) - timedelta(days=1))))
            reader.start()
            reader.join(5)
            self.assertEqual(len(reads[0]), 1)
        finally:
            self.history._lock.release()


class SchemaUpgradeTests(HistoryCase):
    def test_a_3_2_database_gains_watch_and_priority_columns(self):
        with sqlite3.connect(self.data.files.database) as db:
            db.execute("CREATE TABLE reads (at TEXT NOT NULL, site TEXT NOT NULL, outcome TEXT NOT NULL, duration_ms INTEGER NOT NULL)")
            db.execute("INSERT INTO reads VALUES ('2026-10-03T20:00:00+00:00', 'amazon', 'ok', 1000)")
        self.history.record_read("amazon", "timeout", 2000, "watch_x", "high")
        rows = history_module.read_reads(self.data.files.database, datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual([(row.outcome, row.watch_key, row.priority) for row in rows],
                         [("ok", "", ""), ("timeout", "watch_x", "high")])
        with sqlite3.connect(self.data.files.database) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], history_module.SCHEMA_VERSION)


    def test_a_3_4_database_gains_detail_and_load_columns(self):
        with sqlite3.connect(self.data.files.database) as db:
            db.execute("CREATE TABLE reads (at TEXT NOT NULL, site TEXT NOT NULL, outcome TEXT NOT NULL, duration_ms INTEGER NOT NULL, "
                       "watch_key TEXT NOT NULL DEFAULT '', priority TEXT NOT NULL DEFAULT '')")
            db.execute("INSERT INTO reads VALUES ('2026-10-03T20:00:00+00:00', 'amazon', 'error', 1000, 'watch_x', 'high')")
        self.history.record_read("amazon", "timeout", 2000, "watch_x", "high", "Zaman aşımı", SystemLoad(150, 300))
        rows = history_module.read_reads(self.data.files.database, datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual([(row.outcome, row.detail, row.load) for row in rows],
                         [("error", "", SystemLoad()), ("timeout", "Zaman aşımı", SystemLoad(150, 300))])


class StatisticsResetTests(HistoryCase):
    def test_reset_clears_every_outcome_measurement_and_resolved_problem(self):
        from hermes.diagnostics import Diagnostics, runtime_metrics
        for outcome in ("ok", "empty", "stock", "captcha", "timeout", "unreadable", "http_503", "partial", "interrupted"):
            self.history.record_read("amazon", outcome, 100)
        self.history.record_request("amazon", "curl", "ürün", "bot_korumasi", 300)
        self.history.record_cycle(20)
        diagnostics = Diagnostics(self.data.files.database)
        completed = diagnostics.start("amazon", "watch")
        diagnostics.finish(completed, "amazon", "ok", 100)
        running = diagnostics.start("togg", "watch")
        diagnostics.incident("amazon", "read", "Kapandı")
        diagnostics.recover("amazon", "Düzeldi")
        diagnostics.incident("togg", "read", "Açık")
        self.assertEqual(runner.reset_statistics(self.data.files), 9)
        db = self.history._db()
        for table in ("reads", "requests", "cycles"):
            self.assertEqual(db.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0)
        self.assertEqual(db.execute("SELECT id FROM jobs").fetchall(), [(running,)])
        self.assertEqual(len(diagnostics.active()), 1)
        self.assertEqual(diagnostics.recent(), [])
        receipt = runtime_metrics(self.data.files.database)["statistics_reset"]
        self.assertEqual(receipt["cleared"], {"reads": 9, "requests": 1, "cycles": 1, "jobs": 1, "incidents": 1})
        self.assertTrue(all(value == 0 for value in receipt["remaining"].values()))
        self.assertTrue(any("İstatistik geçmişi sıfırlandı: okuma=9" in line for line in LOG_LINES))
        diagnostics.incident("togg", "read", "Yeni olay")
        self.assertEqual(len(diagnostics.recent()), 1)

    def test_reset_preserves_prices_snapshots_notification_and_operational_state_exactly(self):
        from hermes.diagnostics import Diagnostics
        state = realistic_state()
        self.data.write_state(state)
        self.data.write_summary({"rows": [{"price": "100", "min_price": "80"}], "cycle_duration_seconds": 30})
        self.history.migrate_json(self.data.files.state, self.data.files.cycle_history)
        self.history.database.snapshot("state.json", state)
        self.history.database.snapshot("summary.json", self.data.summary())
        db = self.history._db()
        db.execute("INSERT INTO outbox(id,payload,due,created) VALUES ('pending','{}',1,1)")
        db.execute("INSERT INTO successful_reads VALUES ('watch',1)")
        Diagnostics(self.data.files.database).incident("delivery", "send", "Bekliyor", "Müdahale gerekli")
        protected = ("prices", "snapshots", "outbox", "successful_reads", "incidents", "request_window")
        before = {table: db.execute(f"SELECT * FROM {table}").fetchall() for table in protected}
        files = {p: p.read_bytes() for p in (self.data.files.state, self.data.files.summary)}
        self.history.clear_statistics()
        self.assertEqual(before, {table: db.execute(f"SELECT * FROM {table}").fetchall() for table in protected})
        self.assertEqual(files, {p: p.read_bytes() for p in files})

    def test_new_statistics_accumulate_and_reset_stays_empty_after_restart(self):
        self.data.files.cycle_history.write_text(json.dumps(realistic_cycles(datetime.now(timezone.utc))))
        self.history.migrate_json(self.data.files.state, self.data.files.cycle_history)
        self.history.record_read("amazon", "ok", 10)
        self.history.clear_statistics()
        self.history.close()
        self.history = History.at(self.data.files.database)
        self.history.migrate_json(self.data.files.state, self.data.files.cycle_history)
        self.assertEqual(read_cycles(self.data.files.database, datetime(2020,1,1,tzinfo=timezone.utc)), [])
        self.history.record_read("amazon", "timeout", 10)
        self.history.record_read("amazon", "ok", 20)
        self.assertEqual(len(history_module.read_reads(self.data.files.database, datetime(2020,1,1,tzinfo=timezone.utc))), 2)
        self.history.clear_statistics()
        self.assertEqual(self.history._db().execute("SELECT count(*) FROM reads").fetchone()[0], 0)

    def test_reset_is_atomic_if_any_delete_fails(self):
        self.history.record_read("amazon", "ok", 10)
        self.history.record_request("amazon", "curl", "ürün", "ok", 10)
        db = self.history._db()
        db.execute("CREATE TRIGGER reject_reset BEFORE DELETE ON requests BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.history.clear_statistics()
        self.assertEqual(db.execute("SELECT count(*) FROM reads").fetchone()[0], 1)
        self.assertEqual(db.execute("SELECT count(*) FROM requests").fetchone()[0], 1)
        self.assertIsNone(db.execute("SELECT value FROM meta WHERE key='statistics_reset'").fetchone())

    def test_operational_window_survives_reset_restart_and_is_bounded(self):
        from hermes.providers.amazon.access import AmazonAccess
        now = datetime.now(timezone.utc)
        with patch.object(history_module, "_at", return_value=(now-timedelta(minutes=10)).isoformat()):
            self.history.record_request("amazon", "curl", "ürün", "http_503", 100)
        self.history.clear_statistics()
        self.history.close()
        self.history = History.at(self.data.files.database)
        self.history._db()
        rows = history_module.read_requests(self.data.files.database, "amazon", now-timedelta(hours=1))
        access = AmazonAccess()
        access.restore(rows)
        self.assertEqual(access.window_count(), 1)
        self.assertEqual(len(access.events), 1)
        self.assertTrue(access.events[0][1])
        # An older operational sample is pruned, independently of the statistical log.
        self.history._db().execute("INSERT INTO request_window VALUES (?,?,?,?)", ((now-timedelta(hours=2)).isoformat(),"amazon",1,"ok"))
        self.history.record_request("amazon", "curl", "ürün", "ok", 200)
        self.assertEqual(self.history._db().execute("SELECT count(*) FROM request_window").fetchone()[0], 2)
        self.assertEqual(self.history._db().execute("SELECT count(*) FROM requests").fetchone()[0], 1)

    def test_older_request_log_seeds_operational_window_once(self):
        self.history.close()
        now = datetime.now(timezone.utc)
        with sqlite3.connect(self.data.files.database) as db:
            db.execute("CREATE TABLE requests(at TEXT, site TEXT, method TEXT, kind TEXT, outcome TEXT,duration_ms INTEGER)")
            for stamp in (now-timedelta(minutes=5), now-timedelta(hours=2)):
                db.execute("INSERT INTO requests VALUES (?,?,?,?,?,?)", (stamp.isoformat(), "amazon","curl","ürün","ok",100))
        self.history = History.at(self.data.files.database)
        self.history._db()
        self.assertEqual(len(history_module.read_requests(self.data.files.database,"amazon",now-timedelta(hours=1))), 1)
        self.history.clear_statistics()
        self.history.close()
        self.history = History.at(self.data.files.database)
        self.history._db()
        self.assertEqual(len(history_module.read_requests(self.data.files.database,"amazon",now-timedelta(hours=1))), 1)


class WriteTests(HistoryCase):
    def test_price_points_are_recorded_only_on_change(self):
        for price in ("100", "100", "90", "90", "100"):
            self.history.record_price("offer", "amazon", "Ürün", Decimal(price))
        self.assertEqual([price for _, price in read_prices(self.data.files.database, "offer")],
                         [Decimal("100"), Decimal("90"), Decimal("100")])
        # A restart remembers the last recorded price.
        self.history.close()
        History.at(self.data.files.database).record_price("offer", "amazon", "Ürün", Decimal("100"))
        self.assertEqual(len(read_prices(self.data.files.database, "offer")), 3)

    def test_clearing_prices_forgets_the_last_price(self):
        self.history.record_price("offer", "amazon", "Ürün", Decimal("100"))
        self.history.clear_prices()
        self.assertEqual(read_prices(self.data.files.database, "offer"), [])
        self.history.record_price("offer", "amazon", "Ürün", Decimal("100"))
        self.assertEqual(len(read_prices(self.data.files.database, "offer")), 1)

    def test_old_cycles_and_measurements_are_pruned(self):
        now = datetime.now(timezone.utc)
        self.history.record_cycle(5, now - timedelta(days=100))
        self.history._last_prune = 0
        with patch.object(history_module, "_at", side_effect=lambda value=None: (value or now - timedelta(days=40))
                          .astimezone(timezone.utc).isoformat(timespec="seconds")):
            self.history.record_read("amazon", "ok", 100)
            self.history.record_request("amazon", "curl", "ürün", "ok", 100)
        self.history.record_read("amazon", "ok", 200)
        self.history.record_cycle(6, now)
        self.assertEqual([d for _, d in read_cycles(self.data.files.database, now - timedelta(days=365))], [6])
        self.assertEqual(read_site_reads(self.data.files.database, now - timedelta(days=365))[0].total, 1)
        self.assertEqual(read_site_requests(self.data.files.database, now - timedelta(days=365)), [])

    def test_a_broken_database_never_stops_monitoring(self):
        self.data.files.database.write_text("bu bir veritabanı değil", encoding="utf-8")
        broken = History.at(self.data.files.database)
        broken.record_cycle(10)
        broken.record_read("amazon", "ok", 10)
        broken.record_price("offer", "amazon", "Ürün", Decimal("1"))
        broken.migrate_json(self.data.files.state, self.data.files.cycle_history)
        self.assertEqual(sum("Veritabanına yazılamadı" in line for line in LOG_LINES), 1)
        self.assertEqual(read_cycles(self.data.files.database, datetime.now(timezone.utc) - timedelta(days=1)), [])


class ReportTests(HistoryCase):
    def test_site_reads_and_requests_are_summarized(self):
        for site, outcome, ms in (("amazon", "ok", 1000), ("amazon", "ok", 3000), ("amazon", "captcha", 500),
                                  ("amazon", "http_503", 400), ("amazon", "error", 10), ("hepsiburada", "empty", 2000),
                                  ("hepsiburada", "stock", 4000)):
            self.history.record_read(site, outcome, ms)
        for method, outcome in (("curl", "ok"), ("curl", "bot_korumasi"), ("curl", "http_503"), ("browser", "ok"),
                                ("curl", "http_429"), ("curl", "ReadTimeout")):
            self.history.record_request("amazon", method, "ürün", outcome, 2000)
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        amazon, hepsiburada = read_site_reads(self.data.files.database, since)
        self.assertEqual((amazon.site, amazon.total, amazon.ok, amazon.blocked, amazon.errors, amazon.typical_ms),
                         ("amazon", 5, 2, 2, 1, 2000))
        self.assertEqual((hepsiburada.ok, hepsiburada.typical_ms), (2, 3000))
        requests_report = read_site_requests(self.data.files.database, since)[0]
        self.assertEqual((requests_report.total, requests_report.ok, requests_report.captcha, requests_report.http_503,
                          requests_report.http_429, requests_report.other, requests_report.browser), (6, 2, 1, 1, 1, 1, 1))


def amazon_offer(price="90", title="iPhone"):
    return OfferResult(title=title, price=Decimal(price), url=AMAZON, seller="Amazon.com.tr")


class MonitorHistoryTests(HistoryCase):
    def run_cycle(self, cfg, read):
        hermes_monitor = monitor(cfg, self.data, notifier())
        hermes_monitor.providers["amazon"].read = read
        try:
            hermes_monitor.run_cycle()
        finally:
            hermes_monitor.close()

    def run_later(self, cfg, read, minutes):
        later = datetime.now().astimezone() + timedelta(minutes=minutes)
        with patch.object(scheduling, "local_now", return_value=later), patch.object(state_ops, "local_now", return_value=later):
            self.run_cycle(cfg, read)

    def reads(self):
        with sqlite3.connect(self.data.files.database) as db:
            return [row[0] for row in db.execute("SELECT outcome FROM reads ORDER BY rowid")]

    def test_cycle_records_reads_prices_and_its_duration(self):
        rule = watch("iPhone", AMAZON, target="100")
        cfg = config([rule])
        self.run_cycle(cfg, lambda _w, _ctx, _o: [amazon_offer("90")])
        self.run_later(cfg, lambda _w, _ctx, _o: [amazon_offer("90")], 1)
        self.run_later(cfg, lambda _w, _ctx, _o: [amazon_offer("85")], 2)
        self.assertEqual(self.reads(), ["ok", "ok", "ok"])
        offer_key = self.data.state()[key(rule)]["offer_keys"][0]
        self.assertEqual([p for _, p in read_prices(self.data.files.database, offer_key)], [Decimal("90"), Decimal("85")])
        self.assertEqual(len(read_cycles(self.data.files.database, datetime.now(timezone.utc) - timedelta(hours=1),
                                         datetime.now(timezone.utc) + timedelta(hours=1))), 3)
        # state.json keeps its min/max for alerts and a rollback.
        self.assertEqual(self.data.state()[offer_key]["min_price"], "85")

    def test_failed_reads_are_classified(self):
        rule = watch("iPhone", AMAZON, target="100")
        cfg = config([rule])
        outcomes = [BotProtectionHermesError("captcha"), HttpStatusHermesError(503, AMAZON),
                    OutOfStockHermesError("Stokta yok"), RuntimeError("bozuk sayfa"), requests.ReadTimeout("yavaş"),
                    requests.ConnectionError("bağlantı"), PriceUnavailableHermesError("fiyat yok")]
        for minutes, exc in enumerate(outcomes):
            def fail(_w, _ctx, _o, exc=exc):
                raise exc
            # Each read starts after the previous guard (up to 60 min) has passed.
            self.run_later(cfg, fail, minutes * 70)
        self.assertEqual(self.reads(), ["captcha", "http_503", "stock", "error", "timeout", "connection", "unreadable"])
        with sqlite3.connect(self.data.files.database) as db:
            self.assertEqual({row for row in db.execute("SELECT watch_key, priority FROM reads")}, {(key(rule), "cycle")})

    def test_a_wrapped_browser_timeout_counts_as_timeout_and_keeps_its_reason_and_the_pi_load(self):
        class TimeoutException(Exception):
            pass

        def fail(_w, _ctx, _o):
            try:
                raise TimeoutException("page load")
            except TimeoutException as exc:
                raise HermesError("Amazon gerçek tarayıcı sayfası okunamadı (TimeoutException).") from exc

        rule = watch("iPhone", AMAZON, target="100")
        cfg = config([rule])
        with patch.object(cycle_module, "system_load", return_value=SystemLoad(92, 640)):
            self.run_cycle(cfg, fail)
            self.run_later(cfg, lambda _w, _ctx, _o: [amazon_offer("90")], 70)
        with sqlite3.connect(self.data.files.database) as db:
            rows = db.execute("SELECT outcome, detail, cpu_percent, memory_mb FROM reads ORDER BY rowid").fetchall()
        self.assertEqual(rows, [("timeout", "Amazon gerçek tarayıcı sayfası okunamadı (TimeoutException).", 92, 640),
                                ("ok", "", None, None)])
        self.assertTrue(any(line.endswith("(TimeoutException). | sistem yükü %92 · boş bellek 640 MB") for line in LOG_LINES))

    def test_amazon_requests_reach_the_database(self):
        rule = watch("iPhone", AMAZON, target="100")

        def read(_w, ctx, _o):
            ctx.measure("curl", "ürün", "ok", 1500)
            ctx.measure("curl", "ürün", "http_503", 300)
            return [amazon_offer()]

        self.run_cycle(config([rule]), read)
        report = read_site_requests(self.data.files.database, datetime.now(timezone.utc) - timedelta(hours=1))[0]
        self.assertEqual((report.site, report.total, report.ok, report.http_503), ("amazon", 2, 1, 1))

    def test_price_reset_also_clears_database_prices(self):
        rule = watch("iPhone", AMAZON, target="100")
        self.run_cycle(config([rule]), lambda _w, _ctx, _o: [amazon_offer("90")])
        offer_key = self.data.state()[key(rule)]["offer_keys"][0]
        runner.reset_price_history(self.data.files)
        self.assertEqual(read_prices(self.data.files.database, offer_key), [])


class IdleCycleTests(HistoryCase):
    def test_cycles_that_read_nothing_are_not_statistics(self):
        rule = watch("iPhone", AMAZON, target="100")
        hermes_monitor = monitor(config([rule]), self.data, notifier())
        hermes_monitor.providers["amazon"].read = lambda _w, _ctx, _o: [amazon_offer("90")]
        try:
            hermes_monitor.run_cycle()
            published = self.data.summary()["cycle_duration_seconds"]
            hermes_monitor.run_cycle()  # nothing due
            hermes_monitor.run_cycle()
        finally:
            hermes_monitor.close()
        now = datetime.now(timezone.utc)
        self.assertEqual(len(read_cycles(self.data.files.database, now - timedelta(hours=1), now + timedelta(hours=1))), 1)
        self.assertEqual(self.data.summary()["cycle_duration_seconds"], published)

    def test_idle_cycles_from_older_versions_are_removed_once(self):
        now = datetime.now(timezone.utc)
        for seconds, duration in ((10, 1.0), (20, 1.9), (30, 2.4), (40, 270.0)):
            self.history.record_cycle(duration, now - timedelta(seconds=seconds))
        self.history.drop_idle_cycles(1)
        self.history.record_cycle(1.0, now)  # later rows are not touched again
        self.history.drop_idle_cycles(1)
        durations = [d for _, d in read_cycles(self.data.files.database, now - timedelta(hours=1), now + timedelta(hours=1))]
        self.assertEqual(durations, [270.0, 2.4, 1.0])
        self.assertTrue(any("kısa çevrimler çıkarıldı: 2" in line for line in LOG_LINES))


class RequestRestoreTests(HistoryCase):
    def test_monitor_start_restores_amazon_requests_of_the_last_hour(self):
        now = datetime.now(timezone.utc)
        for minutes, outcome in ((10, "ok"), (20, "bot_korumasi"), (50, "ok"), (90, "ok")):
            with patch.object(history_module, "_at", lambda value=None, m=minutes:
                              (now - timedelta(minutes=m)).isoformat(timespec="seconds")):
                self.history.record_request("amazon", "curl", "ürün", outcome, 1500)
        self.history.record_request("hepsiburada", "curl", "ürün", "ok", 1500)  # other sites ignore it
        hermes_monitor = monitor(config([watch("iPhone", AMAZON)]), self.data, notifier())
        try:
            access = hermes_monitor.providers["amazon"].client.access
            self.assertEqual(access.window_count(), 2)  # 10 and 20 minutes ago
            self.assertIn("son 60 dk: istek=3, engel=1", access.stats_line())
        finally:
            hermes_monitor.close()


class QuietLogTests(HistoryCase):
    def test_idle_cycle_logs_no_banner_and_unchanged_table_is_logged_rarely(self):
        rule = watch("iPhone", AMAZON, target="100")
        cfg = config([rule])
        hermes_monitor = monitor(cfg, self.data, notifier())
        hermes_monitor.providers["amazon"].read = lambda _w, _ctx, _o: [amazon_offer("90")]
        try:
            hermes_monitor.run_cycle()
            self.assertTrue(hermes_monitor.last_cycle_read)
            self.assertTrue(any("YENİ KONTROL TURU" in line for line in LOG_LINES))
            LOG_LINES.clear()
            hermes_monitor.run_cycle()  # nothing is due a second later
        finally:
            hermes_monitor.close()
        self.assertFalse(hermes_monitor.last_cycle_read)
        self.assertFalse(any("YENİ KONTROL TURU" in line or "öncelik kapsamı" in line or "Özet:" in line
                             for line in LOG_LINES))

    def test_cycle_with_only_paused_watches_logs_no_banner(self):
        rule = watch("iPhone", AMAZON, target="100")
        cfg = config([rule])
        hermes_monitor = monitor(cfg, self.data, notifier())

        def blocked(_w, _ctx, _o):
            raise BotProtectionHermesError("captcha")

        hermes_monitor.providers["amazon"].read = blocked
        try:
            hermes_monitor.run_cycle()
            LOG_LINES.clear()
            later = datetime.now().astimezone() + timedelta(seconds=5)
            with patch.object(scheduling, "local_now", return_value=later):
                hermes_monitor.run_cycle()  # due again, but the site is paused for 3 minutes
        finally:
            hermes_monitor.close()
        self.assertFalse(hermes_monitor.last_cycle_read)
        self.assertFalse(any("YENİ KONTROL TURU" in line or "öncelik kapsamı" in line for line in LOG_LINES))
        self.assertTrue(any("erişim molasında (captcha)" in line for line in LOG_LINES))  # the pause itself stays visible

    def test_unchanged_table_is_logged_again_after_thirty_minutes(self):
        summary._last_logged_table.update(signature=None, at=0.0)
        row = summary.PriceSummaryRow(seller="Amazon", product_title="iPhone", product_url=AMAZON, price=Decimal("90"),
                                      target_price=Decimal("100"), min_price=Decimal("90"), max_price=Decimal("90"))
        summary.log_price_summary([row], now=10_000)
        summary.log_price_summary([row], now=10_060)
        summary.log_price_summary([row], now=10_000 + summary.UNCHANGED_TABLE_LOG_SECONDS)
        changed = summary.PriceSummaryRow(seller="Amazon", product_title="iPhone", product_url=AMAZON, price=Decimal("85"),
                                          target_price=Decimal("100"), min_price=Decimal("85"), max_price=Decimal("90"))
        summary.log_price_summary([changed], now=10_000 + summary.UNCHANGED_TABLE_LOG_SECONDS + 1)
        self.assertEqual(sum(line.endswith("Özet: eşleşen=1") for line in LOG_LINES), 3)


if __name__ == "__main__":
    unittest.main()
