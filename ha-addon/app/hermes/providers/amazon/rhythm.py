"""Amazon read rhythm: when a watch is due, busy or swept (mixin of AmazonProvider)."""

import time
from dataclasses import dataclass
from typing import Optional, Tuple

from ...constants import DEFAULT_PRIORITY, PRIORITY_INTERVAL_SECONDS, normalize_priority
from ...models import WatchRule


@dataclass
class WatchRhythm:
    """What the provider remembers of one product watch between cycles.

    Since 3.9 a watch is read as a whole (its configured page with the used listing, where Amazon Depo
    offers show up, and every variant of its family) once per search round (a cycle) while it is read every
    minute (red), and every PRIORITY_INTERVAL_SECONDS (5 minutes to 12 hours) otherwise. The
    intervals are stretched by the access governor's speed factor after a block wave.
    """

    main_at: Optional[float] = None
    sweep_at: Optional[float] = None
    main_cycle: int = -1


class RhythmMixin:
    # -- rhythm ----------------------------------------------------------------

    @staticmethod
    def _rhythm_key(watch: WatchRule) -> Tuple:
        return (watch.url, watch.include_variations, watch.official_seller_only, tuple(watch.excluded_terms))

    def next_read_is_main(self, watch: WatchRule) -> bool:
        """The Depo lane reads a product without variants (one page and its used listing). A variant family is
        read in full by the sweep queue, so every page of a red family is read once per round."""
        return not self.is_search_url(watch.url) and not watch.include_variations

    def needs_sweep(self, watch: WatchRule) -> bool:
        """Searches and variant families that are due at their selected interval.

        A product without variants has nothing to sweep: the Depo lane reads its whole page.
        """
        if self.is_search_url(watch.url):
            return True
        if not watch.include_variations:
            return False
        rhythm = self.rhythms.get(self._rhythm_key(watch))
        if rhythm is None or rhythm.sweep_at is None:
            return True
        return time.monotonic() - rhythm.sweep_at >= self.sweep_interval(watch)

    def is_watch_busy(self, watch: WatchRule) -> bool:
        return self._is_busy(self._rhythm_key(watch))

    def _is_busy(self, key: Tuple) -> bool:
        with self._busy_lock:
            started = self.busy.get(key)
        # A read that never finished (an abandoned generator) stops counting after ten minutes.
        return started is not None and time.monotonic() - started < 10 * 60

    def _exclusive(self, key: Tuple, reads):
        """A watch is read by one lane at a time: busy from the start of its read to its end."""
        try:
            yield from reads
        finally:
            with self._busy_lock:
                self.busy.pop(key, None)

    def read_rank(self, watch: WatchRule) -> int:
        """Quick reads go before variant sweeps (many requests)."""
        return 1 if self.needs_sweep(watch) else 0

    @staticmethod
    def category_interval(watch: WatchRule) -> int:
        """Use the shared card interval, independently of its price or variant count."""
        return PRIORITY_INTERVAL_SECONDS[normalize_priority(getattr(watch, "priority", None))]

    def is_red(self, watch: WatchRule) -> bool:
        return normalize_priority(getattr(watch, "priority", None)) == DEFAULT_PRIORITY

    def main_interval(self, watch: WatchRule) -> float:
        return self.category_interval(watch) * self.client.access.speed_factor()

    def sweep_interval(self, watch: WatchRule) -> float:
        """How soon a whole family or search page is read again: the card's interval."""
        return self.main_interval(watch)

    def read_due(self, watch: WatchRule) -> bool:
        """A watch is due after its category's interval; a red one also at most once per search round."""
        key = self._rhythm_key(watch)
        if self._is_busy(key):
            return False
        rhythm = self.rhythms.get(key)
        if rhythm is None or rhythm.main_at is None:
            return True
        if self.is_red(watch) and rhythm.main_cycle == self.cycle_no:
            return False
        return time.monotonic() - rhythm.main_at >= self.main_interval(watch)
