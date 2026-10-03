"""When each watch is due and in which order due watches are read."""

from datetime import timezone
from typing import Any, Callable, Dict, List

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


def priority_order(watches: List[WatchRule], rank: Callable[[WatchRule], int] = lambda _watch: 0) -> List[WatchRule]:
    """One site's due watches: high first, then medium, then low; stable within a tier.

    Inside a tier the provider's `rank` puts quick reads (Amazon's main-page
    reads) before long ones (a whole variant sweep).
    """
    return sorted(watches, key=lambda watch: (PRIORITIES.index(watch_priority(watch)), rank(watch)))
