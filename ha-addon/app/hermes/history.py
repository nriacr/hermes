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

from .logging_utils import log
from .storage import load_json
from .utils import parse_iso_datetime

SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cycles (checked_at TEXT NOT NULL, duration_seconds REAL NOT NULL);
CREATE INDEX IF NOT EXISTS cycles_checked_at ON cycles (checked_at);
CREATE TABLE IF NOT EXISTS prices (
    offer_key TEXT NOT NULL, site TEXT NOT NULL, title TEXT NOT NULL, price TEXT NOT NULL, checked_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS prices_offer ON prices (offer_key, checked_at);
CREATE TABLE IF NOT EXISTS reads (at TEXT NOT NULL, site TEXT NOT NULL, outcome TEXT NOT NULL, duration_ms INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS reads_at ON reads (at);
CREATE TABLE IF NOT EXISTS requests (
    at TEXT NOT NULL, site TEXT NOT NULL, method TEXT NOT NULL, kind TEXT NOT NULL, outcome TEXT NOT NULL,
    duration_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS requests_at ON requests (at);
"""
# Price points are kept for good; the rest is pruned once a day.
CYCLE_KEEP_DAYS = 90
MEASUREMENT_KEEP_DAYS = 30
PRUNE_EVERY_SECONDS = 24 * 60 * 60
MIGRATED_KEY = "json_migrated_at"
# Read and request outcomes that mean the site refused us (back-off material).
BLOCKED_OUTCOMES = ("captcha", "http_429", "http_503")


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
            return cls._instances.setdefault(Path(path), cls(Path(path)))

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._connection: Optional[sqlite3.Connection] = None
        self._last_prices: Dict[str, str] = {}
        self._last_prune = 0.0
        self._failed = False

    # -- connection ---------------------------------------------------------------

    def _db(self) -> sqlite3.Connection:
        if self._connection is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=10, check_same_thread=False, isolation_level=None)
            connection.execute("PRAGMA journal_mode=WAL")
            # With WAL, NORMAL syncs at checkpoints only; a power cut may lose the
            # last few rows but never corrupts the file.
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.executescript(SCHEMA)
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
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
            if self._connection is not None:
                self._connection.close()
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

    def record_read(self, site: str, outcome: str, duration_ms: int) -> None:
        """One watch read by the monitor: ok, empty, stock, captcha, http_<status> or error."""
        self._write("okuma", [("INSERT INTO reads VALUES (?, ?, ?, ?)", (_at(), site, outcome, max(0, int(duration_ms))))])

    def record_request(self, site: str, method: str, kind: str, outcome: str, duration_ms: int) -> None:
        """One network request of a site that reports them (Amazon)."""
        self._write("istek", [("INSERT INTO requests VALUES (?, ?, ?, ?, ?, ?)",
                              (_at(), site, method, kind, outcome, max(0, int(duration_ms))))])

    def record_price(self, offer_key: str, site: str, title: str, price: Decimal, checked_at: Optional[datetime] = None) -> None:
        """A price point, only when the offer's price differs from its last recorded one."""
        text = str(price)
        with self._lock:
            if offer_key not in self._last_prices:
                try:
                    row = self._db().execute(
                        "SELECT price FROM prices WHERE offer_key = ? ORDER BY checked_at DESC, rowid DESC LIMIT 1", (offer_key,)
                    ).fetchone()
                except sqlite3.Error:
                    row = None
                self._last_prices[offer_key] = row[0] if row else ""
            if self._last_prices[offer_key] == text:
                return
        if self._write("fiyat", [("INSERT INTO prices VALUES (?, ?, ?, ?, ?)", (offer_key, site, title, text, _at(checked_at)))]):
            with self._lock:
                self._last_prices[offer_key] = text

    def clear_prices(self) -> None:
        with self._lock:
            self._last_prices.clear()
        self._write("fiyat sıfırlama", [("DELETE FROM prices", ())])

    # -- first start ----------------------------------------------------------------

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
        if outcome in ("ok", "empty", "stock"):
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
