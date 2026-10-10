"""SQLite store for history and measurements: cycles, price points, site reads and requests.

`hermes.db` lives next to the JSON files. One `History` per database file is
the only writer (one connection behind a lock, WAL journal); the panel reads
through separate read-only connections, which WAL lets run alongside writes.

`state.json` stays the source of truth for alert suppression, guards and the
min/max fields, so a rollback to an older Hermes keeps working. On first start
the JSON history (cycle durations and the min/max/last prices in the state) is
copied into the database; the JSON files themselves are never changed here.
"""

import sqlite3
import statistics
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .logging_utils import log, redact
from .database import Database
from .storage import load_json
from .utils import SystemLoad, parse_iso_datetime

SCHEMA_VERSION = 5
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cycles (checked_at TEXT NOT NULL, duration_seconds REAL NOT NULL);
CREATE INDEX IF NOT EXISTS cycles_checked_at ON cycles (checked_at);
CREATE TABLE IF NOT EXISTS prices (
    offer_key TEXT NOT NULL, site TEXT NOT NULL, title TEXT NOT NULL, price TEXT NOT NULL, checked_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS prices_offer ON prices (offer_key, checked_at);
CREATE TABLE IF NOT EXISTS reads (
    at TEXT NOT NULL, site TEXT NOT NULL, outcome TEXT NOT NULL, duration_ms INTEGER NOT NULL,
    watch_key TEXT NOT NULL DEFAULT '', priority TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '', cpu_percent INTEGER, memory_mb INTEGER
);
CREATE INDEX IF NOT EXISTS reads_at ON reads (at);
CREATE TABLE IF NOT EXISTS requests (
    at TEXT NOT NULL, site TEXT NOT NULL, method TEXT NOT NULL, kind TEXT NOT NULL, outcome TEXT NOT NULL,
    duration_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS requests_at ON requests (at);
CREATE TABLE IF NOT EXISTS request_window (
    at TEXT NOT NULL, site TEXT NOT NULL, duration_ms INTEGER NOT NULL, outcome TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS request_window_at ON request_window (at);

"""
READ_COLUMNS = (
    ("watch_key", "TEXT NOT NULL DEFAULT ''"), ("priority", "TEXT NOT NULL DEFAULT ''"),
    ("detail", "TEXT NOT NULL DEFAULT ''"), ("cpu_percent", "INTEGER"), ("memory_mb", "INTEGER"),
)
DETAIL_MAX_CHARS = 300
# Price points are kept for good; the rest is pruned once a day.
CYCLE_KEEP_DAYS = 90
MEASUREMENT_KEEP_DAYS = 30
PRUNE_EVERY_SECONDS = 24 * 60 * 60
MIGRATED_KEY = "json_migrated_at"
IDLE_DROPPED_KEY = "idle_cycles_dropped_at"
# Read and request outcomes that mean the site refused us (back-off material).
BLOCKED_OUTCOMES = ("captcha", "http_429", "http_503")
# Read outcomes that are not a failure: a price, a valid empty search, out of stock.
SUCCESS_OUTCOMES = ("ok", "empty", "stock")


def _at(value: Optional[datetime] = None) -> str:
    """UTC timestamps in one fixed format, so text order is time order."""
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")


def _decimal(value: Any) -> Optional[Decimal]:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() and number > 0 else None


class History:
    """The single writer of one database file; failures are logged, never raised."""

    _instances: Dict[Path, "History"] = {}
    _instances_guard = threading.Lock()

    @classmethod
    def at(cls, path: Path) -> "History":
        with cls._instances_guard:
            path = Path(path)
            if path not in cls._instances:
                cls._instances[path] = cls(path)
            return cls._instances[path]

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.database = Database.at(self.path)
        self._lock = self.database.lock
        self._connection: Optional[sqlite3.Connection] = None
        self._last_prune = 0.0
        self._failed = False

    # -- connection ---------------------------------------------------------------

    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = self.database.connect()
            # Commit each observation durably, including sudden power loss.
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(SCHEMA)
            # Version 2 (3.4): which watch was read and its priority. Version 3
            # (3.10): why a read failed and how busy the Pi was. Older rows keep
            # empty values; older Hermes versions name their columns and keep working.
            columns = {row[1] for row in connection.execute("PRAGMA table_info(reads)")}
            for column, definition in READ_COLUMNS:
                if column not in columns:
                    connection.execute(f"ALTER TABLE reads ADD COLUMN {column} {definition}")
            request_columns = {row[1] for row in connection.execute("PRAGMA table_info(requests)")}
            if "job_id" not in request_columns:
                connection.execute("ALTER TABLE requests ADD COLUMN job_id TEXT NOT NULL DEFAULT ''")
            # This bounded operational window survives a statistics reset and a restart.
            # Seed older databases once; never reimport erased statistical history.
            connection.execute("BEGIN IMMEDIATE")
            try:
                if not connection.execute("SELECT 1 FROM meta WHERE key='request_window_migrated_at'").fetchone():
                    connection.execute("INSERT INTO request_window SELECT at,site,duration_ms,outcome FROM requests WHERE at>=?",
                                       (_at(datetime.now(timezone.utc)-timedelta(hours=1)),))
                    connection.execute("INSERT INTO meta VALUES ('request_window_migrated_at',?)", (_at(),))
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            self._connection = connection
        return self._connection

    def _write(self, label: str, statements: Iterable[Tuple[str, tuple]]) -> bool:
        with self._lock:
            try:
                db = self._db()
                db.execute("BEGIN")
                try:
                    for sql, params in statements:
                        db.execute(sql, params)
                    db.execute("COMMIT")
                except BaseException:
                    db.execute("ROLLBACK")
                    raise
            except sqlite3.Error as exc:
                if not self._failed:
                    log(f"Veritabanına yazılamadı ({label}); izleme devam ediyor: {exc}")
                self._failed = True
                return False
            if self._failed:
                log("Veritabanı yazımı yeniden çalışıyor.")
                self._failed = False
            return True

    def close(self) -> None:
        with self._instances_guard:
            if History._instances.get(self.path) is self:
                del History._instances[self.path]
        with self._lock:
            self.database.close()
            self._connection = None

    # -- writes -------------------------------------------------------------------

    def record_cycle(self, duration_seconds: float, checked_at: Optional[datetime] = None) -> None:
        statements = [("INSERT INTO cycles VALUES (?, ?)", (_at(checked_at), max(0.0, float(duration_seconds))))]
        if time.monotonic() - self._last_prune >= PRUNE_EVERY_SECONDS or not self._last_prune:
            now = checked_at or datetime.now(timezone.utc)
            cycle_cutoff = _at(now - timedelta(days=CYCLE_KEEP_DAYS))
            measurement_cutoff = _at(now - timedelta(days=MEASUREMENT_KEEP_DAYS))
            statements += [
                ("DELETE FROM cycles WHERE checked_at < ?", (cycle_cutoff,)),
                ("DELETE FROM reads WHERE at < ?", (measurement_cutoff,)),
                ("DELETE FROM requests WHERE at < ?", (measurement_cutoff,)),
            ]
            self._last_prune = time.monotonic()
        self._write("çevrim", statements)

    def record_read(self, site: str, outcome: str, duration_ms: int, watch_key: str = "", priority: str = "",
                    detail: str = "", load: Optional[SystemLoad] = None) -> None:
        """One watch read: ok, empty, stock, captcha, http_<status>, timeout, connection, unreadable or error.

        A failed read also keeps its error text and the Pi's load at that moment.
        """
        load = load or SystemLoad()
        self._write("okuma", [("INSERT INTO reads (at, site, outcome, duration_ms, watch_key, priority, detail, cpu_percent, "
                               "memory_mb) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (_at(), site, outcome, max(0, int(duration_ms)), watch_key, priority,
                                redact(str(detail or ""))[:DETAIL_MAX_CHARS], load.cpu_percent, load.memory_mb))])

    def record_request(self, site: str, method: str, kind: str, outcome: str, duration_ms: int, job_id: str = "") -> None:
        """One network request of a site that reports them (Amazon)."""
        now = datetime.now(timezone.utc)
        at = _at()
        duration = max(0, int(duration_ms))
        self._write("istek", [
            ("INSERT INTO requests(at,site,method,kind,outcome,duration_ms,job_id) VALUES (?,?,?,?,?,?,?)",
             (at, site, method, kind, outcome, duration, job_id)),
            ("INSERT INTO request_window VALUES (?,?,?,?)", (at, site, duration, outcome)),
            ("DELETE FROM request_window WHERE at<?", (_at(now-timedelta(hours=1)),)),
        ])

    def price_statement(self, offer_key, site, title, price, checked_at=None):
        # The price and its durable notification intent can share one transaction.
        return ("INSERT INTO prices SELECT ?,?,?,?,? WHERE COALESCE((SELECT price FROM prices "
                "WHERE offer_key=? ORDER BY checked_at DESC,rowid DESC LIMIT 1),'') != ?",
                (offer_key, site, title, str(price), _at(checked_at), offer_key, str(price)))

    def record_price(self, offer_key: str, site: str, title: str, price: Decimal, checked_at: Optional[datetime] = None) -> None:
        self._write("fiyat", [self.price_statement(offer_key, site, title, price, checked_at)])

    def clear_prices(self) -> None:
        self._write("fiyat sıfırlama", [("DELETE FROM prices", ())])

    def clear_statistics(self) -> int:
        """Reset statistical observations atomically, retaining live operational state.

        Panel commands run between active rounds. Unfinished jobs and open incidents
        are operational state, not archived history. Prices, snapshots, outbox,
        notification suppression and the bounded request window are untouched.
        """
        import json
        with self._lock:
            db = self._db()
            db.execute("BEGIN IMMEDIATE")
            try:
                targets = {"reads": "", "requests": "", "cycles": "",
                           "jobs": " WHERE finished IS NOT NULL",
                           "incidents": " WHERE resolved IS NOT NULL"}
                cleared = {}
                for table, condition in targets.items():
                    cleared[table] = db.execute(f"SELECT count(*) FROM {table}{condition}").fetchone()[0]
                    db.execute(f"DELETE FROM {table}{condition}")
                at = time.time()
                receipt = {"at": at, "cleared": cleared,
                           "remaining": {table: db.execute(f"SELECT count(*) FROM {table}{condition}").fetchone()[0]
                                         for table, condition in targets.items()},
                           "price_points": db.execute("SELECT count(*) FROM prices").fetchone()[0],
                           "open_incidents": db.execute("SELECT count(*) FROM incidents WHERE resolved IS NULL").fetchone()[0]}
                db.execute("INSERT OR REPLACE INTO meta VALUES ('statistics_reset',?)", (json.dumps(receipt),))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return cleared["reads"]

    # -- first start ----------------------------------------------------------------

    def drop_idle_cycles(self, interval_seconds: float) -> None:
        """Once: remove cycles recorded before 3.2.2 that read nothing.

        Such a cycle lasted only the wait interval (plus under a second); a cycle
        that read a page takes longer. `cycle_history.json` keeps the originals.
        """
        with self._lock:
            try:
                done = self._db().execute("SELECT value FROM meta WHERE key = ?", (IDLE_DROPPED_KEY,)).fetchone()
                count = self._db().execute("SELECT COUNT(*) FROM cycles WHERE duration_seconds < ?",
                                           (float(interval_seconds) + 1,)).fetchone()[0]
            except sqlite3.Error as exc:
                log(f"Veritabanı açılamadı; boş çevrimler temizlenemedi: {exc}")
                return
        if done:
            return
        if self._write("boş çevrim temizliği", [
            ("DELETE FROM cycles WHERE duration_seconds < ?", (float(interval_seconds) + 1,)),
            ("INSERT OR REPLACE INTO meta VALUES (?, ?)", (IDLE_DROPPED_KEY, _at())),
        ]):
            log(f"İstatistikten okuma yapmayan kısa çevrimler çıkarıldı: {count}")

    def migrate_json(self, state_path: Path, cycle_history_path: Path) -> None:
        """Copy the JSON history once; the JSON files stay as they are for a rollback."""
        with self._lock:
            try:
                done = self._db().execute("SELECT value FROM meta WHERE key = ?", (MIGRATED_KEY,)).fetchone()
            except sqlite3.Error as exc:
                log(f"Veritabanı açılamadı; geçmiş JSON'dan aktarılamadı: {exc}")
                return
        if done:
            return
        cycles = list(_json_cycles(load_json(cycle_history_path, [])))
        state = load_json(state_path, {})
        prices = list(_json_prices(state if isinstance(state, dict) else {}))
        statements = [("INSERT INTO cycles VALUES (?, ?)", cycle) for cycle in cycles]
        statements += [("INSERT INTO prices VALUES (?, ?, ?, ?, ?)", point) for point in prices]
        statements.append(("INSERT OR REPLACE INTO meta VALUES (?, ?)", (MIGRATED_KEY, _at())))
        if self._write("JSON aktarımı", statements):
            log(f"Geçmiş veritabanına aktarıldı: çevrim={len(cycles)} | fiyat noktası={len(prices)} (JSON dosyaları korunuyor)")


def _json_cycles(raw: Any) -> Iterable[tuple]:
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        checked_at = parse_iso_datetime(str(item.get("checked_at") or ""))
        try:
            duration = float(item.get("duration_seconds"))
        except (TypeError, ValueError):
            continue
        if checked_at and duration == duration and 0 <= duration < float("inf"):
            yield _at(checked_at), duration


def _json_prices(state: Dict[str, Any]) -> Iterable[tuple]:
    """Min, max and last price of every offer entry, oldest first, without repeats."""
    for key, entry in state.items():
        if key == "_meta" or not isinstance(entry, dict):
            continue
        points = set()
        for price_field, time_fields in (
            ("min_price", ("min_price_at",)),
            ("max_price", ("max_price_at",)),
            ("last_price", ("last_price_checked_at", "last_checked_at")),
        ):
            price = _decimal(entry.get(price_field))
            checked_at = next((parse_iso_datetime(str(entry.get(name) or "")) for name in time_fields if entry.get(name)), None)
            if price is not None and checked_at is not None:
                points.add((_at(checked_at), str(price)))
        title = str(entry.get("title") or entry.get("watch_name") or "")
        previous = None
        for checked_at, price in sorted(points):
            if price != previous:
                yield key, str(entry.get("site") or ""), title, price, checked_at
            previous = price


# -- panel reads ------------------------------------------------------------------------


def _read(path: Path, sql: str, params: tuple = ()) -> List[tuple]:
    if not Path(path).exists():
        return []
    try:
        connection = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True, timeout=5)
        try:
            return connection.execute(sql, params).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        log(f"Veritabanı okunamadı: {exc}")
        return []


def read_cycles(path: Path, since: datetime, until: Optional[datetime] = None) -> List[Tuple[datetime, float]]:
    rows = _read(path, "SELECT checked_at, duration_seconds FROM cycles WHERE checked_at >= ? AND checked_at <= ? "
                       "ORDER BY checked_at", (_at(since), _at(until or datetime.now(timezone.utc))))
    return [(parse_iso_datetime(checked_at).astimezone(), float(duration)) for checked_at, duration in rows]


def read_prices(path: Path, offer_key: str) -> List[Tuple[datetime, Decimal]]:
    rows = _read(path, "SELECT checked_at, price FROM prices WHERE offer_key = ? ORDER BY checked_at, rowid", (offer_key,))
    return [(parse_iso_datetime(checked_at).astimezone(), Decimal(price)) for checked_at, price in rows]


def read_prices_by_key(path: Path, offer_keys: List[str]) -> Dict[str, List[Tuple[datetime, Decimal]]]:
    """Price points of several offers in one read (the home screen draws a line per row)."""
    found: Dict[str, List[Tuple[datetime, Decimal]]] = {key: [] for key in offer_keys}
    for start in range(0, len(offer_keys), 200):
        chunk = offer_keys[start:start + 200]
        rows = _read(path, "SELECT offer_key, checked_at, price FROM prices WHERE offer_key IN "
                           f"({', '.join('?' * len(chunk))}) ORDER BY checked_at, rowid", tuple(chunk))
        for key, checked_at, price in rows:
            found[key].append((parse_iso_datetime(checked_at).astimezone(), Decimal(price)))
    return found


def read_requests(path: Path, site: str, since: datetime) -> List[Tuple[datetime, int, str]]:
    """Bounded operational request window for restoring a provider after restart."""
    rows = _read(path, "SELECT at, duration_ms, outcome FROM request_window WHERE site = ? AND at >= ? ORDER BY at, rowid",
                 (site, _at(since)))
    return [(parse_iso_datetime(at), int(ms), outcome) for at, ms, outcome in rows]


@dataclass
class Read:
    at: datetime
    site: str
    outcome: str
    duration_ms: int
    watch_key: str
    priority: str
    detail: str = ""
    load: SystemLoad = SystemLoad()


def read_reads(path: Path, since: datetime) -> List[Read]:
    rows = _read(path, "SELECT at, site, outcome, duration_ms, watch_key, priority, detail, cpu_percent, memory_mb FROM reads "
                       "WHERE at >= ? ORDER BY at, rowid", (_at(since),))
    return [Read(parse_iso_datetime(at).astimezone(), site, outcome, int(ms), key, priority, detail or "",
                 SystemLoad(cpu, memory))
            for at, site, outcome, ms, key, priority, detail, cpu, memory in rows]


@dataclass
class SiteReads:
    site: str
    total: int = 0
    ok: int = 0
    blocked: int = 0
    errors: int = 0
    typical_ms: Optional[float] = None


@dataclass
class SiteRequests:
    site: str
    total: int = 0
    ok: int = 0
    captcha: int = 0
    http_503: int = 0
    http_429: int = 0
    other: int = 0
    browser: int = 0
    typical_ms: Optional[float] = None


def read_site_reads(path: Path, since: datetime) -> List[SiteReads]:
    """Per site: watch reads, successes (incl. empty and out of stock), refusals and errors."""
    by_site: Dict[str, SiteReads] = {}
    durations: Dict[str, List[int]] = {}
    for site, outcome, duration_ms in _read(path, "SELECT site, outcome, duration_ms FROM reads WHERE at >= ?", (_at(since),)):
        report = by_site.setdefault(site, SiteReads(site))
        report.total += 1
        if outcome in SUCCESS_OUTCOMES:
            report.ok += 1
            durations.setdefault(site, []).append(duration_ms)
        elif outcome in BLOCKED_OUTCOMES:
            report.blocked += 1
        else:
            report.errors += 1
    for site, values in durations.items():
        by_site[site].typical_ms = statistics.median(values)
    return sorted(by_site.values(), key=lambda report: (-report.total, report.site))


def read_site_requests(path: Path, since: datetime) -> List[SiteRequests]:
    by_site: Dict[str, SiteRequests] = {}
    durations: Dict[str, List[int]] = {}
    rows = _read(path, "SELECT site, method, outcome, duration_ms FROM requests WHERE at >= ?", (_at(since),))
    for site, method, outcome, duration_ms in rows:
        report = by_site.setdefault(site, SiteRequests(site))
        report.total += 1
        report.browser += method == "browser"
        if outcome == "ok":
            report.ok += 1
            durations.setdefault(site, []).append(duration_ms)
        elif outcome in ("captcha", "bot_korumasi"):
            report.captcha += 1
        elif outcome == "http_503":
            report.http_503 += 1
        elif outcome == "http_429":
            report.http_429 += 1
        else:
            report.other += 1
    for site, values in durations.items():
        by_site[site].typical_ms = statistics.median(values)
    return sorted(by_site.values(), key=lambda report: (-report.total, report.site))
