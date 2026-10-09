"""One durable SQLite writer for observations, snapshots and delivery jobs."""

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots(name TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS outbox(
 id TEXT PRIMARY KEY, payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
 attempts INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL, created REAL NOT NULL,
 delivered REAL, error TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS outbox_due ON outbox(state,due);
CREATE TABLE IF NOT EXISTS incidents(
 id TEXT PRIMARY KEY, component TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL,
 opened REAL NOT NULL, updated REAL NOT NULL, count INTEGER NOT NULL DEFAULT 1,
 resolved REAL, recovery TEXT NOT NULL DEFAULT '');
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

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.connection: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        with self.lock:
            if self.connection is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                db = sqlite3.connect(self.path, timeout=10, check_same_thread=False, isolation_level=None)
                try:
                    db.execute("PRAGMA journal_mode=WAL")
                    db.execute("PRAGMA synchronous=FULL")
                    db.executescript(SCHEMA)
                    columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
                    for name, definition in (("planned", "REAL"), ("delay_ms", "INTEGER NOT NULL DEFAULT 0")):
                        if name not in columns:
                            db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
                except BaseException:
                    db.close()
                    raise
                self.connection = db
            return self.connection

    def transaction(self, statements):
        with self.lock:
            db = self.connect()
            db.execute("BEGIN IMMEDIATE")
            try:
                for sql, params in statements:
                    db.execute(sql, params)
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

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
        return json.loads(row[0]) if row else None
    finally:
        db.close()
