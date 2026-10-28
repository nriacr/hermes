"""Price statistics over a recent window, from the stored price changes.

`prices` keeps a point only when the price changes, so the history is a step line: a price holds
until the next point. The window average is therefore weighted by how long each price lasted.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Sequence, Tuple

WINDOW_DAYS = 90
MIN_HISTORY_DAYS = 7

Point = Tuple[datetime, float]


@dataclass(frozen=True)
class WindowStats:
    low: float
    average: float
    days: float  # how much of the window the history really covers


def window_stats(points: Sequence[Point], now: datetime, days: int = WINDOW_DAYS) -> Optional[WindowStats]:
    """Lowest price and time-weighted average of the last `days`; the line ends at `now`."""
    if not points:
        return None
    start = max(now - timedelta(days=days), points[0][0])
    if now <= start:
        return None
    # Segments: price held from its own moment (clipped to the window) to the next point or `now`.
    low = float("inf")
    weighted = 0.0
    for index, (moment, price) in enumerate(points):
        end = points[index + 1][0] if index + 1 < len(points) else now
        begin = max(moment, start)
        end = min(end, now)
        if end <= begin:
            continue
        low = min(low, price)
        weighted += price * (end - begin).total_seconds()
    seconds = (now - start).total_seconds()
    if low == float("inf") or seconds <= 0:
        return None
    return WindowStats(low=low, average=weighted / seconds, days=seconds / 86400)
