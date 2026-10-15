"""Home Assistant Supervisor calls: persist add-on options and restart Hermes."""

import json
import os
import threading
import urllib.error
import urllib.request
from typing import Any, Dict

from .config import options_with_defaults
from .constants import OPTIONS_PATH
from .logging_utils import log
from .storage import save_json

SUPERVISOR_BASE_URL = "http://supervisor"
# One writer at a time: a read-change-save of the option set must not interleave with another.
OPTIONS_LOCK = threading.RLock()


def _headers() -> Dict[str, str]:
    token = os.getenv("SUPERVISOR_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Supervisor token bulunamadı. Hermes'i Home Assistant içinde yeniden başlat.")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _post(path: str, payload: Any = None, timeout: int = 8) -> bytes:
    request = urllib.request.Request(
        f"{SUPERVISOR_BASE_URL}{path}",
        data=json.dumps(payload or {}).encode("utf-8"),
        method="POST",
        headers=_headers(),
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Supervisor API hata verdi: HTTP {exc.code}. Ayarları ve bağlantıyı kontrol et.") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Supervisor API bağlantısı kurulamadı: {exc.reason}") from exc


def _addon_path(action: str) -> str:
    # `self` is the add-on's own endpoint; it works with the default Supervisor role, no manager rights needed.
    return f"/addons/self/{action}"


def restart_addon() -> None:
    try:
        _post(_addon_path("restart"), {}, timeout=5)
    except Exception as exc:  # noqa: BLE001
        if _is_timeout(exc):
            # The Supervisor stops this container before it answers the restart call (measured 2026-10-08:
            # the timeout comes ~7 s after the request and Hermes is up again ~20 s later); not a failure.
            log("Yeniden başlatma isteği Home Assistant'a iletildi; Hermes kapanıyor.")
            return
        log(f"Hermes Home Assistant üzerinden yeniden başlatılamadı: {exc}")


def _is_timeout(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", None)
    return isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError) or "timed out" in str(exc)


def schedule_restart(delay_seconds: float = 2.0) -> None:
    """Restart the add-on through the Supervisor shortly, so the current page response is sent first."""
    timer = threading.Timer(delay_seconds, restart_addon)
    timer.daemon = True
    timer.start()


def save_options_and_restart(options: Dict[str, Any], restart_delay_seconds: float = 2.0) -> Dict[str, Any]:
    """Persist the complete option set, then restart once the save succeeded."""
    saved_options = options_with_defaults(options)
    _post(_addon_path("options"), {"options": saved_options})
    save_json(OPTIONS_PATH, saved_options)
    schedule_restart(restart_delay_seconds)
    return saved_options


def migrate_card_ids(options):
    cards = options.get("takip_edilenler", [])
    if not any(isinstance(item, dict) and not item.get("id") for item in cards):
        return
    saved = options_with_defaults(options)
    if os.getenv("SUPERVISOR_TOKEN"):
        _post(_addon_path("options"), {"options": saved})
    save_json(OPTIONS_PATH, saved)
