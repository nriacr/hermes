"""Crash-safe JSON persistence for files under /data."""

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from .logging_utils import log

# Writers of the same file (monitor, web actions, Telegram) are serialized.
_FILE_LOCKS: dict[Path, threading.RLock] = {}
_FILE_LOCKS_GUARD = threading.Lock()


def file_lock(path: Path) -> threading.RLock:
    with _FILE_LOCKS_GUARD:
        return _FILE_LOCKS.setdefault(Path(path), threading.RLock())


def load_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        log(f"JSON dosyası okunamadı, varsayılan değer kullanılacak: {path} | {exc}")
        return default
    except OSError as exc:
        log(f"JSON dosyasına erişilemedi, varsayılan değer kullanılacak: {path} | {exc}")
        return default


def save_json(path: Path, payload: Any) -> None:
    """Replace a file atomically; a crash leaves either the old or the new copy."""
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
            try:
                os.unlink(handle.name)
            except OSError:
                pass
            raise
