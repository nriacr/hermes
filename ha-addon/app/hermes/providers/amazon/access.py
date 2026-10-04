"""Amazon's request budget: a rolling window, its adaptive limit, block scope and the slow start.

One `AmazonAccess` lives with the client for the whole process and is the only
place that decides how fast requests may start:

* A rolling window (35 minutes) caps how many requests may start. The limit
  begins at 300 and rises by 5 % after every clean hour in which the window was
  really used (never above 500). The first block records the window count as the
  *threshold* and lowers the limit once to 85 % of it (never below 200); later
  blocks only record the threshold. Two lanes share the window: the Depo lane
  (main pages) may use all of it; the variant sweep may use all of it except the
  part of the Depo lane's 28 % share that the Depo lane has not used yet, and it
  steps aside while the Depo lane waits for a free slot. The limit, threshold
  and last raise survive restarts in `amazon_access.json`.
* After a site-wide block, and for a few minutes after every start, requests run
  at half speed (the minimum gap doubles).
* A block is *site-wide* only when two different pages failed one after the
  other; a single page that fails on its own is a *watch* block and leaves the
  budget, the pause and the slow start alone.

The pause itself (3 → 6 → 12 → 20 minutes) is the site-wide guard kept in
`state.json`; this module only measures, limits and classifies.
"""

import math
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Deque, Dict, List, Optional

from ...constants import (
    AMAZON_MAIN_LANE_SHARE,
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

SCHEMA = 2
RATE_LOOKBACK_SECONDS = 300
WINDOW_WAIT_STEP_SECONDS = 5.0
MAIN_LANE = "main"


class AmazonAccess:
    def __init__(self, path: Optional[Path] = None, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep) -> None:
        self.path = path
        self.clock = clock
        self.wall = wall
        self.sleep = sleep
        self.starts: Deque[float] = deque()
        # The starts of the Depo lane (a subset of `starts`) and how many of its threads wait for a slot.
        self.main_starts: Deque[float] = deque()
        self._main_waiting = 0
        self.limit = AMAZON_WINDOW_START_LIMIT
        self.threshold: Optional[int] = None
        # True once a block has lowered the limit; a later block never lowers it again.
        self.lowered = False
        self.last_raise_at = self.wall()
        self.last_block_at: Optional[float] = None
        self.peak_since_raise = 0
        # A site-wide block that no success has followed yet: later blocks continue the
        # same episode and must not touch the threshold again.
        self.episode_open = False
        self.slow_until = self.wall() + AMAZON_START_SLOW_SECONDS
        self._last_wait_log = 0.0
        self._last_stats_log = self.clock()
        self.counters: Dict[str, int] = {}
        # (wall time, blocked) of finished requests of the last hour, for the measurement line.
        self.events: Deque[tuple] = deque()
        # Pages that failed one after the other since the last success.
        self.failed_run: List[str] = []
        self.last_block_scope: Optional[str] = None
        # Two lane threads (Depo and sweep) share the window and the counters.
        self._lock = threading.RLock()
        self._load()

    # -- persistence -----------------------------------------------------------

    def _load(self) -> None:
        if self.path is None:
            return
        stored = load_json(self.path, {})
        if not isinstance(stored, dict) or not stored:
            return
        if stored.get("schema") != SCHEMA:
            # 3.3.0 lowered the limit with every block (down to 119 in one night); start over.
            log("Amazon erişim bütçesi dosyası eski sürümden; pencere sınırı "
                f"{AMAZON_WINDOW_START_LIMIT}'den yeniden başlıyor.")
            return
        try:
            limit = int(stored.get("limit", self.limit))
            self.limit = min(AMAZON_WINDOW_MAX_LIMIT, max(AMAZON_WINDOW_MIN_LIMIT, limit))
            threshold = stored.get("threshold")
            self.threshold = int(threshold) if threshold is not None else None
            self.lowered = bool(stored.get("lowered", False))
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
                "schema": SCHEMA, "limit": self.limit, "threshold": self.threshold, "lowered": self.lowered,
                "last_raise_at": self.last_raise_at, "last_block_at": self.last_block_at,
                "slow_until": self.slow_until, "saved_at": utc_now(),
            })
        except OSError as exc:
            log(f"Amazon erişim bütçesi kaydedilemedi: {exc}")

    # -- the window ------------------------------------------------------------

    def _prune(self, now: float) -> None:
        while self.starts and now - self.starts[0] >= AMAZON_WINDOW_SECONDS:
            self.starts.popleft()
        while self.main_starts and now - self.main_starts[0] >= AMAZON_WINDOW_SECONDS:
            self.main_starts.popleft()

    def window_count(self) -> int:
        with self._lock:
            self._prune(self.clock())
            return len(self.starts)

    def limit_for(self, lane: str = "") -> int:
        """The Depo lane may use the whole window; the sweep all but the Depo share the Depo lane has not used."""
        if lane == MAIN_LANE:
            return self.limit
        unused_share = max(0, math.ceil(round(self.limit * AMAZON_MAIN_LANE_SHARE, 6)) - len(self.main_starts))
        return max(1, self.limit - unused_share)

    def rate_per_minute(self) -> float:
        now = self.clock()
        with self._lock:
            recent = sum(1 for started in self.starts if now - started <= RATE_LOOKBACK_SECONDS)
        return recent * 60.0 / RATE_LOOKBACK_SECONDS

    def wait_for_window(self, lane: str = "") -> float:
        """Wait until a request of this lane may start inside the window; returns the seconds waited."""
        waited = 0.0
        main = lane == MAIN_LANE
        counted = False
        try:
            while True:
                with self._lock:
                    now = self.clock()
                    self._prune(now)
                    limit = self.limit_for(lane)
                    # A sweep steps aside while the Depo lane waits, so freed slots go to the Depo lane first.
                    if len(self.starts) < limit and (main or not self._main_waiting):
                        return waited
                    if main and not counted:
                        self._main_waiting += 1
                        counted = True
                    step = min(WINDOW_WAIT_STEP_SECONDS,
                               max(0.05, self.starts[0] + AMAZON_WINDOW_SECONDS - now) if self.starts else 0.05)
                    if now - self._last_wait_log >= 60:
                        self._last_wait_log = now
                        log(f"Amazon istek penceresi dolu ({lane or 'tarama'} şeridi {len(self.starts)}/{limit}, "
                            f"son {AMAZON_WINDOW_SECONDS // 60} dk); yeni istek bekliyor.")
                self.sleep(step)
                waited += step
        finally:
            if counted:
                with self._lock:
                    self._main_waiting -= 1

    def request_started(self, lane: str = "") -> None:
        with self._lock:
            now = self.clock()
            self.starts.append(now)
            if lane == MAIN_LANE:
                self.main_starts.append(now)
            self.peak_since_raise = max(self.peak_since_raise, len(self.starts))
            self._maybe_raise()

    def _maybe_raise(self) -> None:
        if self.limit >= AMAZON_WINDOW_MAX_LIMIT:
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
        with self._lock:
            self.counters[name] = self.counters.get(name, 0) + amount

    def request_finished(self, blocked: bool, page: str = "") -> None:
        with self._lock:
            self._request_finished(blocked, page)

    def _request_finished(self, blocked: bool, page: str = "") -> None:
        self.count("istek")
        now = self.wall()
        self.events.append((now, blocked))
        while self.events and now - self.events[0][0] > 3600:
            self.events.popleft()
        if not blocked:
            self.episode_open = False
            self.failed_run.clear()
            return
        self.count("engel")
        self.failed_run.append(page or "?")
        if len(set(self.failed_run)) < 2:
            # One page failed on its own: a watch block. Budget, pause and slow start stay as they are.
            self.last_block_scope = "watch"
            self.count("sayfa_engeli")
            return
        self.last_block_scope = "site"
        self.failed_run.clear()
        self.slow_until = max(self.slow_until, now + AMAZON_RECOVERY_SLOW_SECONDS)
        self.last_block_at = now
        # A clean hour is counted from the last site-wide block.
        self.last_raise_at = now
        self.peak_since_raise = 0
        if not self.episode_open:
            self.episode_open = True
            count = self.window_count()
            self.threshold = count
            rate = self.rate_per_minute()
            if not self.lowered:
                before = self.limit
                self.limit = min(self.limit, max(AMAZON_WINDOW_MIN_LIMIT, int(count * AMAZON_WINDOW_THRESHOLD_FACTOR)))
                self.lowered = True
                log(f"Amazon engeli: pencerede {count} istek, anlık {rate:.1f} istek/dk; "
                    f"eşik={count}, pencere sınırı {before} → {self.limit} (bir kez düşürüldü).")
            else:
                log(f"Amazon engeli: pencerede {count} istek, anlık {rate:.1f} istek/dk; "
                    f"eşik={count}; sınır {self.limit} olarak kalıyor (yalnız bir kez düşer).")
        self._save()

    # -- measurement ---------------------------------------------------------------

    def stats_line(self) -> str:
        now_wall = self.wall()
        slow = max(0, round((self.slow_until - now_wall) / 60))
        last_hour = len(self.events)
        last_hour_blocks = sum(1 for _at, blocked in self.events if blocked)
        return (f"son 60 dk: istek={last_hour}, engel={last_hour_blocks} | "
                f"pencere={self.window_count()}/{self.limit} | eşik={self.threshold if self.threshold is not None else '-'} | "
                f"anlık={self.rate_per_minute():.1f} istek/dk | sınır={'bir kez düşürüldü' if self.lowered else 'hiç düşmedi'} | "
                f"son artıştan beri={(now_wall - self.last_raise_at) / 60:.0f} dk | yarım hız kalan={slow} dk")

    def stats_due(self) -> bool:
        now = self.clock()
        if now - self._last_stats_log < AMAZON_STATS_LOG_SECONDS:
            return False
        self._last_stats_log = now
        return True
