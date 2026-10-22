"""One durable SQLite writer for observations, snapshots and delivery jobs."""

import json
import sqlite3
import zlib
import threading
import time
from pathlib import Path
from typing import Any

from . import writestats
from .logging_utils import log

CHECKPOINT_EVERY_SECONDS = 60 * 60

# Large snapshots (the whole watch state, the price table) are stored deflated in the same
# `snapshots.payload` column. The marker tells a compressed value from a plain JSON text, so rows
# written by older versions are still read and small snapshots stay plain text.
COMPRESSED_MARKER = b"HZ1"
COMPRESS_FROM_BYTES = 4096


def encode_payload(text: str):
    """Deflate a large snapshot; the result is read back and compared before it is used."""
    raw = text.encode("utf-8")
    if len(raw) < COMPRESS_FROM_BYTES:
        return text
    try:
        packed = COMPRESSED_MARKER + zlib.compress(raw, 6)
        if zlib.decompress(packed[len(COMPRESSED_MARKER):]) != raw:
            return text
    except zlib.error:
        return text
    return packed if len(packed) < len(raw) else text


def decode_payload(value) -> str:
    if isinstance(value, (bytes, bytearray, memoryview)):
        value = bytes(value)
        if value.startswith(COMPRESSED_MARKER):
            return zlib.decompress(value[len(COMPRESSED_MARKER):]).decode("utf-8")
        return value.decode("utf-8")
    return value

SCHEMA = """
CREATE TABLE IF NOT EXISTS successful_reads(component TEXT PRIMARY KEY, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS snapshots(name TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS outbox(
 id TEXT PRIMARY KEY, payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
 attempts INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL, created REAL NOT NULL,
 delivered REAL, error TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS outbox_due ON outbox(state,due);
CREATE TABLE IF NOT EXISTS incidents(
 id TEXT PRIMARY KEY, component TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL,
 opened REAL NOT NULL, updated REAL NOT NULL, count INTEGER NOT NULL DEFAULT 1,
 resolved REAL, recovery TEXT NOT NULL DEFAULT '', context TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS jobs(
 id TEXT PRIMARY KEY, site TEXT NOT NULL, watch_key TEXT NOT NULL, version TEXT NOT NULL,
 started REAL NOT NULL, planned REAL, delay_ms INTEGER NOT NULL DEFAULT 0, finished REAL, outcome TEXT, duration_ms INTEGER,
 detail TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS jobs_started ON jobs(started);
"""


class Database:
    _instances: dict[Path, "Database"] = {}
    _guard = threading.Lock()

    @classmethod
    def at(cls, path: Path) -> "Database":
        path = Path(path).resolve()
        with cls._guard:
            if path not in cls._instances:
                cls._instances[path] = cls(path)
            return cls._instances[path]

    @classmethod
    def latest_text(cls, path: Path, name: str):
        """The last snapshot text this process committed, or None (then the file is read)."""
        with cls._guard:
            instance = cls._instances.get(Path(path).resolve())
        return instance._written_snapshots.get(name) if instance else None

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.connection: sqlite3.Connection | None = None
        # Set when the startup check finds damage. Nothing is deleted or recreated; Hermes keeps
        # running on a best-effort basis and the log says what to restore.
        self.integrity_error = ""
        self._last_checkpoint = time.monotonic()
        self._state_written = 0.0
        # Last text committed per snapshot name; an identical rewrite is skipped.
        self._written_snapshots: dict[str, str] = {}

    def connect(self) -> sqlite3.Connection:
        with self.lock:
            if self.connection is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                db = sqlite3.connect(self.path, timeout=10, check_same_thread=False, isolation_level=None)
                try:
                    db.execute("PRAGMA journal_mode=WAL")
                    db.execute("PRAGMA synchronous=FULL")
                    self._check_integrity(db)
                    db.executescript(SCHEMA)
                    incident_columns = {row[1] for row in db.execute("PRAGMA table_info(incidents)")}
                    if "context" not in incident_columns:
                        db.execute("ALTER TABLE incidents ADD COLUMN context TEXT NOT NULL DEFAULT '{}'")
                    columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
                    for name, definition in (("planned", "REAL"), ("delay_ms", "INTEGER NOT NULL DEFAULT 0")):
                        if name not in columns:
                            db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
                except BaseException:
                    db.close()
                    raise
                self.connection = db
            return self.connection

    def _check_integrity(self, db: sqlite3.Connection) -> None:
        """Quick structural check at open; a damaged file is reported, never rewritten or removed."""
        try:
            rows = [str(row[0]) for row in db.execute("PRAGMA quick_check(5)")]
        except sqlite3.DatabaseError as exc:
            rows = [f"kontrol çalışmadı: {exc}"]
        if rows == ["ok"]:
            self.integrity_error = ""
            return
        self.integrity_error = "; ".join(rows)[:300]
        log(f"VERİTABANI SAĞLIKSIZ ({self.path.name}): {self.integrity_error}. Dosya silinmedi veya değiştirilmedi; "
            "Hermes çalışmayı sürdürüyor ama kayıtlar güvenilir olmayabilir. Gerekirse en son günlük yedekten dönülmeli.")

    def checkpoint(self, force: bool = False) -> None:
        """Fold the WAL back into the main file about hourly so it cannot grow without bound."""
        with self.lock:
            if self.connection is None:
                return
            if not force and time.monotonic() - self._last_checkpoint < CHECKPOINT_EVERY_SECONDS:
                return
            self._last_checkpoint = time.monotonic()
            try:
                busy, _log_frames, _checkpointed = self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            except sqlite3.Error as exc:
                log(f"Veritabanı WAL temizliği yapılamadı: {exc}")
                return
            if busy:
                log("Veritabanı WAL temizliği şu an meşgul olduğu için sonraya bırakıldı.")
            if self.integrity_error:
                log(f"Veritabanı hâlâ sağlıksız ({self.path.name}): {self.integrity_error}")

    def state_snapshot_age(self) -> float:
        """Seconds since the whole state was last committed to the database."""
        return time.monotonic() - self._state_written

    def transaction(self, statements):
        statements = list(statements)
        prepared = []
        committed_texts = {}
        for sql, params in statements:
            if sql.startswith("INSERT OR REPLACE INTO snapshots"):
                name, text = params[0], params[1]
                if name == "state.json":
                    self._state_written = time.monotonic()
                if isinstance(text, str) and self._written_snapshots.get(name) == text:
                    continue  # nothing changed since the last commit of this snapshot
                payload = encode_payload(text) if isinstance(text, str) else text
                writestats.record(f"{name} SQL", len(payload))
                if isinstance(text, str):
                    committed_texts[name] = text
                params = (name, payload)
            prepared.append((sql, params))
        writestats.record("diğer SQL", writestats.statement_bytes(
            item for item in prepared if not item[0].startswith("INSERT OR REPLACE INTO snapshots")))
        if not prepared:
            return
        with self.lock:
            db = self.connect()
            db.execute("BEGIN IMMEDIATE")
            try:
                for sql, params in prepared:
                    db.execute(sql, params)
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            self._written_snapshots.update(committed_texts)

    def snapshot(self, name: str, payload: Any):
        self.transaction([("INSERT OR REPLACE INTO snapshots VALUES (?,?)",
                           (name, json.dumps(payload, ensure_ascii=False)))])

    def close(self):
        with self.lock:
            if self.connection is not None:
                self.connection.close()
                self.connection = None
        with self._guard:
            self._instances.pop(self.path, None)


def read_snapshot(path: Path, name: str):
    if not path.exists():
        return None
    db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
    try:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='snapshots'").fetchone()
        row = db.execute("SELECT payload FROM snapshots WHERE name=?", (name,)).fetchone() if exists else None
        return json.loads(decode_payload(row[0])) if row else None
    finally:
        db.close()
