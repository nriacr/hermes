"""Crash-safe persistence for Hermes data under /data.

Since 4.12 the JSON files listed in SQL_BACKED are authoritative in `hermes.db` (the `snapshots`
table, written in one transaction); the JSON file next to it is only an export kept for a rollback to
an older Hermes. A file that has no snapshot yet is read from disk and adopted on the first start
(`adopt_json_files`). `options.json` is owned by Home Assistant and `web_access.json` is read
directly, so neither is listed.
"""

import atexit
import contextlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from . import writestats
from .logging_utils import log
from .database import Database, read_snapshot

SQL_BACKED = frozenset({
    "state.json", "latest_price_summary.json", "amazon_access.json", "amazon_cookies.json",
    "status.json", "seen_messages.json", "error_events.json", "telegram_quick_add.json", "login_state.json",
})

# Writers of the same file (monitor, web actions, Telegram) are serialized.
_FILE_LOCKS: dict[Path, threading.RLock] = {}
_FILE_LOCKS_GUARD = threading.Lock()


def file_lock(path: Path) -> threading.RLock:
    with _FILE_LOCKS_GUARD:
        return _FILE_LOCKS.setdefault(Path(path), threading.RLock())


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.name in SQL_BACKED:
            # This process wrote the newest copy: reuse its text instead of reading and inflating the blob.
            text = Database.latest_text(path.parent / "hermes.db", path.name)
            if text is not None:
                return json.loads(text)
            snapshot = read_snapshot(path.parent / "hermes.db", path.name)
            if snapshot is not None:
                return snapshot
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        log(f"JSON dosyası okunamadı, varsayılan değer kullanılacak: {path} | {exc}")
        if path.name == "state.json":
            raise RuntimeError("Takip hafızası bozuk; mevcut dosya korunuyor.") from exc
        return default
    except OSError as exc:
        log(f"JSON dosyasına erişilemedi, varsayılan değer kullanılacak: {path} | {exc}")
        if path.name == "state.json":
            raise RuntimeError("Takip hafızası okunamıyor; mevcut dosya korunuyor.") from exc
        return default


def save_json(path: Path, payload: Any) -> None:
    """Replace a file atomically; a crash leaves either the old or the new copy."""
    if path.name in SQL_BACKED:
        Database.at(path.parent / "hermes.db").snapshot(path.name, payload)
    export_json(path, payload)


def adopt_json_files(data_dir: Path) -> list:
    """Copy JSON files that have no snapshot yet into the database (idempotent; files stay)."""
    adopted = []
    database = Database.at(data_dir / "hermes.db")
    for name in sorted(SQL_BACKED):
        path = data_dir / name
        try:
            if not path.exists() or read_snapshot(data_dir / "hermes.db", name) is not None:
                continue
            with path.open("r", encoding="utf-8") as handle:
                database.snapshot(name, json.load(handle))
            adopted.append(name)
        except (OSError, ValueError) as exc:
            log(f"JSON dosyası veritabanına alınamadı, dosya okunmaya devam edecek: {name} | {exc}")
    if adopted:
        log(f"JSON dosyaları veritabanına alındı (dosyalar yedek olarak korunuyor): {', '.join(adopted)}")
    return adopted


# The JSON copies only serve a rollback to an older Hermes, so each file is rewritten at most once
# per EXPORT_EVERY_SECONDS (the newest content waits in memory and is flushed by a timer, on stop
# and at exit). The authoritative copy in `hermes.db` is written separately and is not delayed.
EXPORT_EVERY_SECONDS = 300
_PENDING: dict[Path, str] = {}
_LAST_EXPORT: dict[Path, float] = {}
_LAST_TEXT: dict[Path, str] = {}  # content last written per file; an identical rewrite is skipped
_PENDING_GUARD = threading.Lock()
_flusher_started = False


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        if _LAST_TEXT.get(path) == text and path.exists():
            with _PENDING_GUARD:
                _LAST_EXPORT[path] = time.monotonic()
            return
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        )
        try:
            with handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(handle.name, path)
            _LAST_TEXT[path] = text
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(handle.name)
            raise
    with _PENDING_GUARD:
        _LAST_EXPORT[path] = time.monotonic()
    writestats.record(f"{path.name} JSON", len(text))


def flush_exports(only_due: bool = False) -> None:
    """Write the JSON copies that are waiting (all of them, or only those past their delay)."""
    with _PENDING_GUARD:
        now = time.monotonic()
        due = [(path, text) for path, text in _PENDING.items()
               if not only_due or now - _LAST_EXPORT.get(path, 0.0) >= EXPORT_EVERY_SECONDS]
        for path, _text in due:
            del _PENDING[path]
    for path, text in due:
        try:
            _write_text(path, text)
        except OSError as exc:
            log(f"JSON kopyası yazılamadı: {path.name} | {exc}")


def _flush_loop() -> None:
    while True:
        time.sleep(min(30, EXPORT_EVERY_SECONDS / 2))
        flush_exports(only_due=True)


def export_json(path: Path, payload: Any, *, now: bool = False) -> None:
    """Write the compatible JSON projection after its authoritative SQL commit."""
    global _flusher_started
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    path = Path(path)
    if now or path.name not in SQL_BACKED:
        with _PENDING_GUARD:
            _PENDING.pop(path, None)
        _write_text(path, text)
        return
    with _PENDING_GUARD:
        waiting = time.monotonic() - _LAST_EXPORT.get(path, 0.0) < EXPORT_EVERY_SECONDS
        if waiting:
            _PENDING[path] = text
            if not _flusher_started:
                _flusher_started = True
                threading.Thread(target=_flush_loop, name="json-export-flush", daemon=True).start()
            return
        _PENDING.pop(path, None)
    _write_text(path, text)


atexit.register(flush_exports)
