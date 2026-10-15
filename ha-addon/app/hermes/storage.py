"""Crash-safe persistence for Hermes data under /data.

Since 4.12 the JSON files listed in SQL_BACKED are authoritative in `hermes.db` (the `snapshots`
table, written in one transaction); the JSON file next to it is only an export kept for a rollback to
an older Hermes. A file that has no snapshot yet is read from disk and adopted on the first start
(`adopt_json_files`). `options.json` is owned by Home Assistant and `web_access.json` is read
directly, so neither is listed.
"""

import contextlib
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

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


def export_json(path: Path, payload: Any) -> None:
    """Write the compatible JSON projection after its authoritative SQL commit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        )
        try:
            with handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(handle.name, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(handle.name)
            raise
