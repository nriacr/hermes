"""Manual per-site pause set from Telegram (`/duraklat amazon 6`); it always ends by itself."""

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from . import constants
from .storage import file_lock, load_json, save_json
from .utils import local_now, parse_iso_datetime

DEFAULT_HOURS = 6
MAX_HOURS = 72


def _active(now: datetime) -> Dict[str, str]:
    payload = load_json(constants.SITE_PAUSE_PATH, {})
    if not isinstance(payload, dict):
        return {}
    kept = {}
    for site, until in payload.items():
        moment = parse_iso_datetime(until)
        if moment and moment > now:
            kept[str(site)] = str(until)
    return kept


def is_paused(site: str) -> bool:
    return str(site) in _active(local_now().astimezone(timezone.utc))


def active_pauses() -> Dict[str, datetime]:
    now = local_now().astimezone(timezone.utc)
    return {site: parse_iso_datetime(until) for site, until in _active(now).items()}


def pause(site: str, hours: Optional[float] = None) -> datetime:
    hours = DEFAULT_HOURS if hours is None else min(max(float(hours), 0.5), MAX_HOURS)
    until = local_now().astimezone(timezone.utc) + timedelta(hours=hours)
    with file_lock(constants.SITE_PAUSE_PATH):
        pauses = _active(local_now().astimezone(timezone.utc))
        pauses[str(site)] = until.isoformat()
        save_json(constants.SITE_PAUSE_PATH, pauses)
    return until


def resume(site: str) -> bool:
    with file_lock(constants.SITE_PAUSE_PATH):
        pauses = _active(local_now().astimezone(timezone.utc))
        found = pauses.pop(str(site), None) is not None
        save_json(constants.SITE_PAUSE_PATH, pauses)
    return found
