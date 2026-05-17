import json
from pathlib import Path
from typing import Any

from .logging_utils import log


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
