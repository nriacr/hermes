"""When each watch is due and in which order due watches are read."""

from datetime import timezone
from typing import Any, Callable, Dict, List

from ..constants import PRIORITIES, PRIORITY_INTERVAL_SECONDS, normalize_priority, site_priorities
from ..models import WatchRule
from ..utils import local_now, parse_iso_datetime


def watch_priority(watch: WatchRule) -> str:
    return normalize_priority(getattr(watch, "priority", None))


def effective_priority(watch: WatchRule, state_entry: Dict[str, Any]) -> str:
    """The card's interval, or the longer one test mode moved it to after failed reads.

    The step counts only while test mode is on and the card still has the interval it was taken from;
    without test mode the user's choice is never changed."""
    priority = watch_priority(watch)
    stepped = str((state_entry or {}).get("test_priority") or "")
    if (getattr(watch, "test_mode", False) and (state_entry or {}).get("test_base_priority") == priority
            and stepped in site_priorities(watch.site)
            and PRIORITY_INTERVAL_SECONDS[stepped] > PRIORITY_INTERVAL_SECONDS[priority]):
        return stepped
    return priority


def next_test_priority(watch: WatchRule, state_entry: Dict[str, Any]) -> str:
    """The next longer interval the site offers; the longest one stays."""
    choices = site_priorities(watch.site)
    current = effective_priority(watch, state_entry)
    longer = [choice for choice in choices if PRIORITY_INTERVAL_SECONDS[choice] > PRIORITY_INTERVAL_SECONDS[current]]
    return min(longer, key=PRIORITY_INTERVAL_SECONDS.get) if longer else current


def manually_due(watch: WatchRule, state_entry: Dict[str, Any]) -> bool:
    """A settings edit stamps a new token; the card is then read immediately."""
    token = str(getattr(watch, "check_now_token", "") or "")
    return bool(token and token != str(state_entry.get("check_now_token") or ""))


def watch_check_due(watch: WatchRule, state_entry: Dict[str, Any], global_interval_seconds: int) -> bool:
    if manually_due(watch, state_entry):
        return True
    priority_interval = PRIORITY_INTERVAL_SECONDS.get(effective_priority(watch, state_entry), global_interval_seconds)
    interval_seconds = max(global_interval_seconds, priority_interval)
    last_checked = parse_iso_datetime(state_entry.get("last_checked_at"))
    if not last_checked:
        return True
    elapsed_seconds = (local_now().astimezone(timezone.utc) - last_checked).total_seconds()
    return elapsed_seconds >= interval_seconds


def priority_order(watches: List[WatchRule], rank: Callable[[WatchRule], int] = lambda _watch: 0) -> List[WatchRule]:
    """One site's due watches: shortest interval first; stable within a tier.

    Inside a tier the provider's `rank` puts quick reads (Amazon's main-page
    reads) before long ones (a whole variant sweep).
    """
    return sorted(watches, key=lambda watch: (PRIORITIES.index(watch_priority(watch)), rank(watch)))
