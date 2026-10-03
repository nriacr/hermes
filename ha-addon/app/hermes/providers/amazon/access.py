"""Amazon's request budget: a rolling window, its adaptive limit and the slow start.

One `AmazonAccess` lives with the client for the whole process and is the only
place that decides how fast requests may start:

* A rolling window (35 minutes) caps how many requests may start. The limit
  begins at 300 and rises by 5 % after every clean hour in which the window was
  really used. A block records the window count as the *threshold*, lowers the
  limit to 85 % of it and freezes it there. The limit, threshold and last raise
  survive restarts in `amazon_access.json`.
* After a block, and for a few minutes after every start, requests run at half
  speed (the minimum gap doubles).

The pause itself (3 → 6 → 12 → 20 minutes) is the site-wide guard kept in
`state.json`; this module only measures and limits.
"""

import math
import time
from collections import deque
from pathlib import Path
from typing import Callable, Deque, Dict, Optional

from ...constants import (
    AMAZON_RECOVERY_SLOW_FACTOR,
    AMAZON_RECOVERY_SLOW_SECONDS,
    AMAZON_START_SLOW_SECONDS,
    AMAZON_STATS_LOG_SECONDS,
    AMAZON_WINDOW_MAX_LIMIT,
    AMAZON_WINDOW_MIN_LIMIT,
    AMAZON_WINDOW_RAISE_EVERY_SECONDS,
    AMAZON_WINDOW_RAISE_FACTOR,
    AMAZON_WINDOW_RAISE_MIN_USE,
    AMAZON_WINDOW_SECONDS,
    AMAZON_WINDOW_START_LIMIT,
    AMAZON_WINDOW_THRESHOLD_FACTOR,
)
from ...logging_utils import log
from ...storage import load_json, save_json
from ...utils import utc_now

RATE_LOOKBACK_SECONDS = 300
WINDOW_WAIT_STEP_SECONDS = 5.0


class AmazonAccess:
    def __init__(self, path: Optional[Path] = None, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep) -> None:
        self.path = path
        self.clock = clock
        self.wall = wall
        self.sleep = sleep
        self.starts: Deque[float] = deque()
        self.limit = AMAZON_WINDOW_START_LIMIT
        self.threshold: Optional[int] = None
        self.frozen = False
        self.last_raise_at = self.wall()
        self.last_block_at: Optional[float] = None
        self.peak_since_raise = 0
        # A block that no success has followed yet: later blocks continue the
        # same episode and must not lower the limit again.
        self.episode_open = False
        self.slow_until = self.wall() + AMAZON_START_SLOW_SECONDS
        self._last_wait_log = 0.0
        self._last_stats_log = self.clock()
        self.counters: Dict[str, int] = {}
        # (wall time, blocked) of finished requests of the last hour, for the measurement line.
        self.events: Deque[tuple] = deque()
        self._load()

    # -- persistence -----------------------------------------------------------

    def _load(self) -> None:
        if self.path is None:
            return
        stored = load_json(self.path, {})
        if not isinstance(stored, dict):
            return
        try:
            limit = int(stored.get("limit", self.limit))
            self.limit = min(AMAZON_WINDOW_MAX_LIMIT, max(AMAZON_WINDOW_MIN_LIMIT, limit))
            threshold = stored.get("threshold")
            self.threshold = int(threshold) if threshold is not None else None
            self.frozen = bool(stored.get("frozen", False))
            self.last_raise_at = float(stored.get("last_raise_at", self.last_raise_at))
            last_block = stored.get("last_block_at")
            self.last_block_at = float(last_block) if last_block is not None else None
            self.slow_until = max(self.slow_until, float(stored.get("slow_until", 0)))
        except (TypeError, ValueError):
            log(f"Amazon erişim bütçesi dosyası okunamadı, varsayılanlar kullanılacak: {self.path}")

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            save_json(self.path, {
                "limit": self.limit, "threshold": self.threshold, "frozen": self.frozen,
                "last_raise_at": self.last_raise_at, "last_block_at": self.last_block_at,
                "slow_until": self.slow_until, "saved_at": utc_now(),
            })
        except OSError as exc:
            log(f"Amazon erişim bütçesi kaydedilemedi: {exc}")

    # -- the window ------------------------------------------------------------

    def _prune(self, now: float) -> None:
        while self.starts and now - self.starts[0] >= AMAZON_WINDOW_SECONDS:
            self.starts.popleft()

    def window_count(self) -> int:
        self._prune(self.clock())
        return len(self.starts)

    def rate_per_minute(self) -> float:
        now = self.clock()
        recent = sum(1 for started in self.starts if now - started <= RATE_LOOKBACK_SECONDS)
        return recent * 60.0 / RATE_LOOKBACK_SECONDS

    def wait_for_window(self) -> float:
        """Wait until a request may start inside the window; returns the seconds waited."""
        waited = 0.0
        while True:
            now = self.clock()
            self._prune(now)
            if len(self.starts) < self.limit:
                break
            step = min(WINDOW_WAIT_STEP_SECONDS, max(0.05, self.starts[0] + AMAZON_WINDOW_SECONDS - now))
            if now - self._last_wait_log >= 60:
                self._last_wait_log = now
                log(f"Amazon istek penceresi dolu ({len(self.starts)}/{self.limit}, son {AMAZON_WINDOW_SECONDS // 60} dk); "
                    "yeni istek bekliyor.")
            self.sleep(step)
            waited += step
        return waited

    def request_started(self) -> None:
        now = self.clock()
        self.starts.append(now)
        count = len(self.starts)
        self.peak_since_raise = max(self.peak_since_raise, count)
        self._maybe_raise()

    def _maybe_raise(self) -> None:
        if self.frozen or self.limit >= AMAZON_WINDOW_MAX_LIMIT:
            return
        if self.wall() - self.last_raise_at < AMAZON_WINDOW_RAISE_EVERY_SECONDS:
            return
        # A clean hour only counts when the window came close to the limit.
        if self.peak_since_raise >= AMAZON_WINDOW_RAISE_MIN_USE * self.limit:
            before = self.limit
            self.limit = min(AMAZON_WINDOW_MAX_LIMIT, max(self.limit + 1, math.ceil(self.limit * AMAZON_WINDOW_RAISE_FACTOR)))
            log(f"Amazon istek penceresi sınırı yükseltildi: {before} → {self.limit} (engelsiz 1 saat).")
        self.last_raise_at = self.wall()
        self.peak_since_raise = 0
        self._save()

    # -- results -----------------------------------------------------------------

    def gap_multiplier(self) -> float:
        return AMAZON_RECOVERY_SLOW_FACTOR if self.wall() < self.slow_until else 1.0

    def count(self, name: str, amount: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + amount

    def request_finished(self, blocked: bool) -> None:
        self.count("istek")
        now_event = self.wall()
        self.events.append((now_event, blocked))
        while self.events and now_event - self.events[0][0] > 3600:
            self.events.popleft()
        if not blocked:
            self.episode_open = False
            return
        self.count("engel")
        now_wall = self.wall()
        self.slow_until = max(self.slow_until, now_wall + AMAZON_RECOVERY_SLOW_SECONDS)
        self.last_block_at = now_wall
        # A clean hour is counted from the last block.
        self.last_raise_at = now_wall
        self.peak_since_raise = 0
        if not self.episode_open:
            self.episode_open = True
            count = self.window_count()
            self.threshold = count
            before = self.limit
            self.limit = min(self.limit, max(AMAZON_WINDOW_MIN_LIMIT, int(count * AMAZON_WINDOW_THRESHOLD_FACTOR)))
            self.frozen = True
            log(f"Amazon engeli: pencerede {count} istek, anlık {self.rate_per_minute():.1f} istek/dk; "
                f"eşik={count}, pencere sınırı {before} → {self.limit} (sabitlendi).")
        self._save()

    # -- measurement ---------------------------------------------------------------

    def stats_line(self) -> str:
        now_wall = self.wall()
        raised = "-" if self.frozen else f"{(now_wall - self.last_raise_at) / 60:.0f} dk önce"
        slow = max(0, round((self.slow_until - now_wall) / 60))
        last_hour = len(self.events)
        last_hour_blocks = sum(1 for _at, blocked in self.events if blocked)
        return (f"son 60 dk: istek={last_hour}, engel={last_hour_blocks} | "
                f"pencere={self.window_count()}/{self.limit} | eşik={self.threshold if self.threshold is not None else '-'} | "
                f"anlık={self.rate_per_minute():.1f} istek/dk | sınır={'sabit' if self.frozen else 'uyarlanıyor'} | "
                f"son artış sayacı={raised} | yarım hız kalan={slow} dk")

    def stats_due(self) -> bool:
        now = self.clock()
        if now - self._last_stats_log < AMAZON_STATS_LOG_SECONDS:
            return False
        self._last_stats_log = now
        return True
