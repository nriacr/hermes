"""Per-site stall detection: a site that reads nothing successfully for a long time.

The Supervisor watchdog restarts Hermes only when a reader makes no progress at all (up to 3 hours
for Amazon). A site can also keep making requests that all fail; this notices that early (45 minutes,
or three times the longest watch interval of the site) and tells the user once per stall.
A CAPTCHA or HTTP 503 hold stays silent by design and is never reported here.
"""

from typing import Dict, Iterable, List, Tuple

STALL_MIN_SECONDS = 45 * 60
STALL_INTERVAL_FACTOR = 3
SUCCESS_OUTCOMES = frozenset({"ok", "partial", "stock", "empty"})


def stall_threshold(longest_interval_seconds: float) -> float:
    return max(STALL_MIN_SECONDS, STALL_INTERVAL_FACTOR * longest_interval_seconds)


class StallWatch:
    def __init__(self, started_at: float):
        self.started_at = started_at
        self.stalled: Dict[str, float] = {}
        self.notified: set = set()

    def update(self, now: float, last_success: Dict[str, float], thresholds: Dict[str, float],
               silent_sites: Iterable[str]) -> Tuple[List[str], List[str]]:
        """Return (sites that just stalled, sites that recovered). `now` and times are monotonic seconds."""
        silent = set(silent_sites)
        began, recovered = [], []
        for site, threshold in thresholds.items():
            since = max(self.started_at, last_success.get(site, self.started_at))
            idle = now - since
            if idle > threshold and site not in silent:
                if site not in self.stalled:
                    self.stalled[site] = since
                    began.append(site)
            elif site in self.stalled:
                del self.stalled[site]
                self.notified.discard(site)
                recovered.append(site)
        for site in set(self.stalled) - set(thresholds):
            del self.stalled[site]
            self.notified.discard(site)
        return began, recovered
