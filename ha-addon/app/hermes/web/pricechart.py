"""Price history of one summary row, drawn as the small tile line and the large detail chart.

A row of the published summary is matched to its offer in `state.json` (same link, same
condition, same tracking card); the points come from the `prices` table of `hermes.db`,
which stores a price only when it changes, so lines are drawn as steps.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Sequence, Tuple

from ..history import read_prices_by_key
from ..utils import parse_bool

Point = Tuple[datetime, float]

TILE_SIZE = (64, 30)
SPOT_SIZE = (72, 26)
CHART_WIDTH, CHART_HEIGHT = 700, 250
CHART_PAD = {"left": 52, "right": 14, "top": 14, "bottom": 28}


def offer_index(state: Dict[str, Any]) -> Dict[tuple, List[str]]:
    """State keys of every offer entry, by (link, Amazon Depo, tracking card)."""
    index: Dict[tuple, List[str]] = {}
    for key, entry in state.items() if isinstance(state, dict) else []:
        if key == "_meta" or not isinstance(entry, dict) or not entry.get("url"):
            continue
        identity = (str(entry["url"]), parse_bool(entry.get("is_warehouse"), default=False), str(entry.get("tracking_id") or ""))
        index.setdefault(identity, []).append(key)
    return index


def row_offer_keys(index: Dict[tuple, List[str]], row: Dict[str, Any]) -> List[str]:
    identity = (str(row.get("product_url") or ""), parse_bool(row.get("is_warehouse"), default=False),
                str(row.get("tracking_id") or ""))
    return index.get(identity, [])


def load_histories(database_path, index: Dict[tuple, List[str]], rows: Sequence[Dict[str, Any]]) -> List[List[Point]]:
    """The longest recorded price line of each row (a link can be known under older keys too)."""
    wanted = {key for row in rows for key in row_offer_keys(index, row)}
    stored = read_prices_by_key(database_path, sorted(wanted)) if wanted else {}
    lines = []
    for row in rows:
        candidates = [stored.get(key, []) for key in row_offer_keys(index, row)]
        best = max(candidates, key=len, default=[])
        lines.append([(moment, float(price)) for moment, price in best])
    return lines


def with_current(points: List[Point], price: Decimal, checked_at: datetime) -> List[Point]:
    """The recorded line ends with today's price even when no change was stored yet."""
    current = float(price)
    if not points:
        return [(checked_at, current)]
    if abs(points[-1][1] - current) > 1:
        return points + [(max(checked_at, points[-1][0]), current)]
    return points


def _steps(points: List[Point], x, y) -> str:
    path = f"M{x(points[0][0]):.1f} {y(points[0][1]):.1f}"
    for previous, point in zip(points, points[1:]):
        path += f" L{x(point[0]):.1f} {y(previous[1]):.1f} L{x(point[0]):.1f} {y(point[1]):.1f}"
    return path


def _scales(points: List[Point], left: float, right: float, top: float, bottom: float,
            extra: Sequence[float] = (), headroom: float = 0.0):
    """Maps time to x and price to y; `headroom` widens the price range by that share."""
    started, ended = points[0][0].timestamp(), points[-1][0].timestamp()
    prices = [price for _, price in points] + list(extra)
    low, high = min(prices), max(prices)
    margin = (high - low) * headroom or (high * 0.02 if headroom else 0.0)
    low, high = low - margin, high + margin
    if high == low:
        low, high = low - 1, high + 1
    span = (ended - started) or 1.0

    def x(moment: datetime) -> float:
        return left + (moment.timestamp() - started) / span * (right - left)

    def y(price: float) -> float:
        return top + (1 - (price - low) / (high - low)) * (bottom - top)

    return x, y, low, high


def sparkline(points: List[Point], size: Tuple[int, int] = TILE_SIZE) -> str:
    """A small step line in the card's color; one point is drawn as a dotted 'new' mark."""
    width, height = size
    if len(points) < 2:
        return (f"<svg class='ov-spark' viewBox='0 0 {width} {height}' aria-hidden='true'>"
                f"<line x1='2' x2='{width - 6}' y1='{height / 2:g}' y2='{height / 2:g}' stroke='currentColor' "
                "stroke-opacity='.35' stroke-width='2' stroke-dasharray='2 4' stroke-linecap='round'/>"
                f"<circle cx='{width - 4}' cy='{height / 2:g}' r='3' fill='currentColor'/></svg>")
    x, y, _, _ = _scales(points, 3, width - 6, 4, height - 4)
    return (f"<svg class='ov-spark' viewBox='0 0 {width} {height}' aria-hidden='true'>"
            f"<path class='ov-draw' pathLength='1' d='{_steps(points, x, y)}' fill='none' stroke='currentColor' "
            "stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round'/>"
            f"<circle class='ov-fade' cx='{x(points[-1][0]):.1f}' cy='{y(points[-1][1]):.1f}' r='3' fill='currentColor'/></svg>")


def _thousands(value: float) -> str:
    return f"{value / 1000:.1f}".replace(".", ",") + "K"


def detail_chart(points: List[Point], target: float) -> str:
    """The detail sheet chart: price steps, the target line and the first and last day."""
    pad = CHART_PAD
    x, y, low, high = _scales(points, pad["left"], CHART_WIDTH - pad["right"], pad["top"], CHART_HEIGHT - pad["bottom"],
                              [target], headroom=0.14)
    floor = CHART_HEIGHT - pad["bottom"]
    grid = "".join(
        f"<line x1='{pad['left']}' x2='{CHART_WIDTH - pad['right']}' y1='{y(level):.1f}' y2='{y(level):.1f}' stroke='rgba(255,255,255,.07)'/>"
        f"<text x='{pad['left'] - 8}' y='{y(level) + 4:.1f}' text-anchor='end' font-size='11' fill='#9a95b8'>{_thousands(level)}</text>"
        for level in (low + (high - low) * step / 3 for step in range(4))
    )
    steps = _steps(points, x, y)
    area = f"{steps} L{x(points[-1][0]):.1f} {floor} L{x(points[0][0]):.1f} {floor} Z"
    dots = "".join(f"<circle class='ov-fade' cx='{x(moment):.1f}' cy='{y(price):.1f}' r='4' fill='currentColor' stroke='#0a0814' stroke-width='2'/>"
                   for moment, price in points)
    first, last = points[0][0], points[-1][0]
    labels = (f"<text x='{x(first):.1f}' y='{CHART_HEIGHT - 7}' font-size='11' fill='#9a95b8'>{_day(first)}</text>"
              f"<text x='{x(last):.1f}' y='{CHART_HEIGHT - 7}' text-anchor='end' font-size='11' fill='#9a95b8'>{_day(last)}</text>")
    target_y = y(target)
    return (
        f"<svg class='ov-chart' viewBox='0 0 {CHART_WIDTH} {CHART_HEIGHT}' role='img' aria-label='Fiyat geçmişi'>"
        f"{grid}<line x1='{pad['left']}' x2='{CHART_WIDTH - pad['right']}' y1='{target_y:.1f}' y2='{target_y:.1f}' "
        "stroke='#b6ff3b' stroke-dasharray='6 5' stroke-width='1.5'/>"
        f"<path class='ov-fade' d='{area}' fill='currentColor' fill-opacity='.22'/>"
        f"<path class='ov-draw' pathLength='1' d='{steps}' fill='none' stroke='currentColor' stroke-width='3' "
        f"stroke-linejoin='round' stroke-linecap='round'/>{dots}{labels}</svg>"
    )


def _day(moment: datetime) -> str:
    months = ("Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara")
    return f"{moment.day} {months[moment.month - 1]}"
