"""Amazon's request budget: a rolling window, its adaptive limit, block scope, the speed governor and the slow start.

One `AmazonAccess` lives with the client for the whole process and is the only
place that decides how fast requests may start:

* A rolling window (35 minutes) caps how many requests may start. The limit
  begins at 400 and rises by 5 % after every clean hour in which the window was
  really used (never above 600). The first block records the window count as the
  *threshold* and lowers the limit once to 85 % of it (never below 200); later
  blocks only record the threshold. Two lanes share the window: the Depo lane
  (main pages) may use all of it; the variant sweep may use all of it except the
  part of the Depo lane's 28 % share that the Depo lane has not used yet, and it
  steps aside while the Depo lane waits for a free slot. The limit, threshold
  and last raise survive restarts in `amazon_access.json`.
* For a few minutes after every start the minimum gap between requests doubles.
* Every block is site-wide (3.6.0): a block marks the visitor, not the page.
  For `AMAZON_BLOCK_HOLD_SECONDS` after it the client sends nothing at all, so
  a read already under way in the other lane does not collect more blocks.
* Speed governor (3.7.0): a block wave doubles the reading interval of every
  category (x2, at most x`AMAZON_SLOWDOWN_MAX`); each clean
  `AMAZON_SLOWDOWN_RECOVER_SECONDS` halves it again until the normal speed is
  back, with no one to switch it. `speed_factor()` is what the provider asks.
* Each block wave (the first block after a success) is logged with its cause
  and the requests of the hour before it, and counted per day.

The pause itself (5 → 10 → 20 → 30 minutes) is the site-wide guard kept in
`state.json`; this module only measures, limits and holds requests back.
"""

import math
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Deque, Dict, Optional

from ...constants import (
    AMAZON_BLOCK_HOLD_SECONDS,
    AMAZON_MAIN_LANE_FLOOR,
    AMAZON_MAIN_LANE_SHARE,
    AMAZON_SLOWDOWN_MAX,
    AMAZON_SLOWDOWN_RECOVER_SECONDS,
    AMAZON_START_SLOW_FACTOR,
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
from ...utils import local_now, utc_now

SCHEMA = 3
RATE_LOOKBACK_SECONDS = 300
WINDOW_WAIT_STEP_SECONDS = 5.0
MAIN_LANE = "main"
# Request outcomes (client.block_reason) that count as a block: a challenge page, HTTP 429 or 503.
PROTECTION_OUTCOMES = ("bot_korumasi", "http_429", "http_503")


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
        # Block waves of the current local day: (date, count); survives restarts.
        self.waves_day = ""
        self.waves_count = 0
        # Speed governor: how many times slower than normal the categories read, and since when.
        self.slowdown = 1.0
        self.slowdown_since = self.wall()
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
            # An older schema (3.3.0 lowered the limit with every block; 3.7.0 starts higher): start over.
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
            self.waves_day = str(stored.get("waves_day") or "")
            self.waves_count = int(stored.get("waves_count") or 0)
            self.slowdown = min(AMAZON_SLOWDOWN_MAX, max(1.0, float(stored.get("slowdown") or 1.0)))
            self.slowdown_since = float(stored.get("slowdown_since") or self.slowdown_since)
        except (TypeError, ValueError):
            log(f"Amazon erişim bütçesi dosyası okunamadı, varsayılanlar kullanılacak: {self.path}")

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            save_json(self.path, {
                "schema": SCHEMA, "limit": self.limit, "threshold": self.threshold, "lowered": self.lowered,
                "last_raise_at": self.last_raise_at, "last_block_at": self.last_block_at,
                "slow_until": self.slow_until, "waves_day": self.waves_day, "waves_count": self.waves_count,
                "slowdown": self.slowdown, "slowdown_since": self.slowdown_since, "saved_at": utc_now(),
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

    def depo_reserve(self) -> int:
        """What the sweep leaves the Depo lane: what it used in the last 35 minutes, a floor, at most its share."""
        cap = math.ceil(round(self.limit * AMAZON_MAIN_LANE_SHARE, 6))
        return min(cap, max(AMAZON_MAIN_LANE_FLOOR, len(self.main_starts)))

    def limit_for(self, lane: str = "") -> int:
        """The Depo lane may use the whole window; the sweep all but the part of the Depo reserve not used yet."""
        if lane == MAIN_LANE:
            return self.limit
        unused = max(0, self.depo_reserve() - len(self.main_starts))
        return max(1, self.limit - unused)

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

    def restore(self, requests) -> None:
        """Rebuild the window and the last hour from the database after a restart.

        `requests` are (finished at, duration ms, outcome) of Amazon's network
        requests. Without this a restart starts the window at zero, and the first
        block would record only the requests since the start as its threshold.
        The Depo lane's own share is not stored, so it starts unused.
        """
        with self._lock:
            now_wall, now_clock = self.wall(), self.clock()
            starts, events = [], []
            for finished_at, duration_ms, outcome in requests:
                finished = finished_at.timestamp()
                started = finished - max(0, duration_ms) / 1000
                if 0 <= now_wall - started < AMAZON_WINDOW_SECONDS:
                    starts.append(now_clock - (now_wall - started))
                if 0 <= now_wall - finished <= 3600:
                    events.append((finished, outcome in PROTECTION_OUTCOMES))
            self.starts = deque(sorted(starts) + list(self.starts))
            self.events = deque(sorted(events) + list(self.events))
            self.peak_since_raise = max(self.peak_since_raise, len(self.starts))
        log(f"Amazon istek penceresi veritabanından geri yüklendi: son {AMAZON_WINDOW_SECONDS // 60} dk={len(starts)} istek | "
            f"son 60 dk={len(events)} istek, {sum(blocked for _at, blocked in events)} engel")

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

    def speed_factor(self) -> float:
        """1.0 at normal speed; 2.0 or 4.0 while the governor holds the categories back after a block wave.

        Every clean AMAZON_SLOWDOWN_RECOVER_SECONDS halves the factor; it is read often, so recovery needs no timer.
        """
        with self._lock:
            now = self.wall()
            changed = False
            while self.slowdown > 1.0 and now - self.slowdown_since >= AMAZON_SLOWDOWN_RECOVER_SECONDS:
                self.slowdown = max(1.0, self.slowdown / 2)
                self.slowdown_since += AMAZON_SLOWDOWN_RECOVER_SECONDS
                changed = True
                if self.slowdown > 1.0:
                    log(f"Amazon hız kademesi gevşiyor: x{self.slowdown:g} (engelsiz {AMAZON_SLOWDOWN_RECOVER_SECONDS // 60} dk).")
                else:
                    log("Amazon normal hıza döndü (engelsiz süre doldu).")
            if changed:
                self._save()
            return self.slowdown

    def gap_multiplier(self) -> float:
        """The minimum request gap doubles for a few minutes after every start and stretches with the governor.

        A red watch has no timer of its own (it is read every search round), so the governor slows
        it down through the gap between requests; the other categories also wait longer intervals.
        """
        start = AMAZON_START_SLOW_FACTOR if self.wall() < self.slow_until else 1.0
        return max(start, self.speed_factor())

    def count(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self.counters[name] = self.counters.get(name, 0) + amount

    def hold_remaining(self) -> float:
        """Seconds the client still sends nothing after the last block."""
        with self._lock:
            if self.last_block_at is None:
                return 0.0
            return max(0.0, self.last_block_at + AMAZON_BLOCK_HOLD_SECONDS - self.wall())

    def waves_today(self) -> int:
        return self.waves_count if self.waves_day == local_now().date().isoformat() else 0

    def request_finished(self, blocked: bool, page: str = "", cause: str = "") -> None:
        with self._lock:
            self._request_finished(blocked, page, cause)

    def _request_finished(self, blocked: bool, page: str = "", cause: str = "") -> None:
        self.count("istek")
        now = self.wall()
        self.events.append((now, blocked))
        while self.events and now - self.events[0][0] > 3600:
            self.events.popleft()
        if not blocked:
            self.episode_open = False
            return
        self.count("engel")
        previous_block_at = self.last_block_at
        self.last_block_at = now
        self.slowdown_since = now
        # A clean hour is counted from the last site-wide block.
        self.last_raise_at = now
        self.peak_since_raise = 0
        if not self.episode_open:
            self.episode_open = True
            self.slowdown = min(AMAZON_SLOWDOWN_MAX, self.slowdown * 2)
            log(f"Amazon hız kademesi: x{self.slowdown:g} (engel sonrası); her {AMAZON_SLOWDOWN_RECOVER_SECONDS // 60} dk "
                "engelsiz geçince bir kademe gevşer.")
            self._log_wave(now, previous_block_at, page, cause)
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

    def _log_wave(self, now: float, previous_block_at: Optional[float], page: str, cause: str) -> None:
        """One line per block wave: its cause, the hour before it and the calm time since the previous wave."""
        today = local_now().date().isoformat()
        if self.waves_day != today:
            self.waves_day, self.waves_count = today, 0
        self.waves_count += 1
        last_hour = len(self.events) - 1
        calm = f"{(now - previous_block_at) / 60:.0f} dk" if previous_block_at is not None else "-"
        log(f"Amazon engel dalgası: bugün #{self.waves_count} | sebep={cause or '-'} | sayfa={page or '-'} | "
            f"son 60 dk istek={last_hour} | önceki engelden beri={calm}")

    # -- measurement ---------------------------------------------------------------

    def stats_line(self) -> str:
        now_wall = self.wall()
        slow = max(0, round((self.slow_until - now_wall) / 60))
        last_hour = len(self.events)
        last_hour_blocks = sum(1 for _at, blocked in self.events if blocked)
        return (f"son 60 dk: istek={last_hour}, engel={last_hour_blocks} | bugünkü engel dalgası={self.waves_today()} | "
                f"pencere={self.window_count()}/{self.limit} (tarama şeridi sınırı={self.limit_for('')}, "
                f"depo şeridi son 35 dk={len(self.main_starts)}) | eşik={self.threshold if self.threshold is not None else '-'} | "
                f"anlık={self.rate_per_minute():.1f} istek/dk | sınır={'bir kez düşürüldü' if self.lowered else 'hiç düşmedi'} | "
                f"son artıştan beri={(now_wall - self.last_raise_at) / 60:.0f} dk | hız kademesi=x{self.speed_factor():g} | "
                f"başlangıç yarım hızı kalan={slow} dk")

    def stats_due(self) -> bool:
        # Both lane threads ask; only one of them may write the line.
        with self._lock:
            now = self.clock()
            if now - self._last_stats_log < AMAZON_STATS_LOG_SECONDS:
                return False
            self._last_stats_log = now
            return True
