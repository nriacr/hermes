"""When each watch is due and in which order due watches are read."""

from datetime import timezone
from typing import Any, Dict, List

from ..constants import PRIORITIES, PRIORITY_INTERVAL_SECONDS
from ..models import WatchRule
from ..utils import local_now, parse_iso_datetime


def watch_priority(watch: WatchRule) -> str:
    priority = str(getattr(watch, "priority", "high") or "high").casefold()
    return priority if priority in PRIORITIES else "high"


def manually_due(watch: WatchRule, state_entry: Dict[str, Any]) -> bool:
    """A settings edit stamps a new token; the card is then read immediately."""
    token = str(getattr(watch, "check_now_token", "") or "")
    return bool(token and token != str(state_entry.get("check_now_token") or ""))


def watch_check_due(watch: WatchRule, state_entry: Dict[str, Any], global_interval_seconds: int) -> bool:
    if manually_due(watch, state_entry):
        return True
    legacy_interval = getattr(watch, "check_interval_minutes", None)
    if legacy_interval:
        interval_seconds = legacy_interval * 60
    else:
        priority_interval = PRIORITY_INTERVAL_SECONDS.get(watch_priority(watch), global_interval_seconds)
        interval_seconds = max(global_interval_seconds, priority_interval)
    last_checked = parse_iso_datetime(state_entry.get("last_checked_at"))
    if not last_checked:
        return True
    elapsed_seconds = (local_now().astimezone(timezone.utc) - last_checked).total_seconds()
    return elapsed_seconds >= interval_seconds


def balanced_request_order(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Alternate sites so one site never receives a long run of requests."""
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    site_order: List[str] = []
    for item in items:
        site = str(item.get("site") or "unknown").strip().lower() or "unknown"
        if site not in buckets:
            buckets[site] = []
            site_order.append(site)
        buckets[site].append(item)

    ordered: List[Dict[str, Any]] = []
    last_site = ""
    while any(buckets.values()):
        candidates = [site for site in site_order if buckets[site] and site != last_site]
        if not candidates:
            candidates = [site for site in site_order if buckets[site]]
        selected_site = max(candidates, key=lambda site: (len(buckets[site]), -site_order.index(site)))
        ordered.append(buckets[selected_site].pop(0))
        last_site = selected_site
    return ordered


def priority_request_order(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Prioritize high watches while still balancing sites within each tier."""
    ordered: List[Dict[str, Any]] = []
    for priority in PRIORITIES:
        ordered.extend(balanced_request_order([item for item in items if watch_priority(item["watch"]) == priority]))
    return ordered
