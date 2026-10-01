import json
from pathlib import Path
from typing import Any
from datetime import datetime, timedelta, timezone

from .logging_utils import log
from .utils import parse_iso_datetime


def append_amazon_diagnostics(state: dict, events: list) -> None:
    """Keep seven days of bounded, non-sensitive block measurements in state."""
    if not events:
        return
    meta = state.setdefault("_meta", {})
    if not isinstance(meta, dict):
        meta = {}
        state["_meta"] = meta
    previous = meta.get("amazon_request_diagnostics", [])
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    retained = []
    for event in (previous if isinstance(previous, list) else []) + events:
        at = parse_iso_datetime(event.get("at")) if isinstance(event, dict) else None
        if at and at >= cutoff:
            retained.append(event)
    meta["amazon_request_diagnostics"] = retained[-1000:]


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
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    temp_path.replace(path)
