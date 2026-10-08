"""Home Assistant Supervisor calls: persist add-on options and restart Hermes."""

import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict

from .config import options_with_defaults
from .constants import OPTIONS_PATH
from .logging_utils import log
from .storage import save_json

ADDON_SLUG = "hermes"
SUPERVISOR_BASE_URL = "http://supervisor"


def current_addon_slug() -> str:
    """Repository-qualified slug, e.g. `769724e3_hermes`, from the container hostname."""
    hostname = os.getenv("HOSTNAME", "").strip()
    hyphen_slug = ADDON_SLUG.replace("_", "-")
    if hostname.endswith(f"-{hyphen_slug}"):
        repository_id = hostname[: -(len(hyphen_slug) + 1)]
        if repository_id:
            return f"{repository_id}_{ADDON_SLUG}"
    return hostname.replace("-", "_") if hostname else f"local_{ADDON_SLUG}"


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
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Supervisor API hata verdi: {exc.code} {detail[:240]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Supervisor API bağlantısı kurulamadı: {exc.reason}") from exc


def _addon_path(action: str) -> str:
    return f"/addons/{urllib.parse.quote(current_addon_slug(), safe='')}/{action}"


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
