"""Price history of one summary row, drawn as the small tile line and the large detail chart.

A row of the published summary is matched to its offer in `state.json` (same link, same
condition, same tracking card); the points come from the `prices` table of `hermes.db`,
which stores a price only when it changes, so lines are drawn as steps.
"""

import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Sequence, Tuple

from ..history import read_prices_by_key, read_targets_by_card
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


def load_targets(database_path, rows: Sequence[Dict[str, Any]]) -> List[List[Point]]:
    """The recorded target price changes of each row's tracking card (empty before any was recorded)."""
    cards = sorted({str(row.get("tracking_id") or "") for row in rows} - {""})
    stored = read_targets_by_card(database_path, cards) if cards else {}
    return [[(moment, float(value)) for moment, value in stored.get(str(row.get("tracking_id") or ""), [])] for row in rows]


def target_series(targets: List[Point], current: float, start: datetime, end: datetime) -> List[Point]:
    """The target as a step line over [start, end]: the value in force at `start`, then each change, then `end`.

    Before the first recorded change the first known target applies; without records the current target is flat."""
    if not targets or abs(targets[-1][1] - current) > 0.5:
        targets = list(targets) + [(max(end, targets[-1][0]) if targets else start, current)]
    holding = [value for moment, value in targets if moment <= start]
    opening = holding[-1] if holding else targets[0][1]
    inside = [(moment, value) for moment, value in targets if start < moment <= end]
    series = [(start, opening)] + inside
    return series + [(end, targets[-1][1])]  # a change after the last price point shows as the final level


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
    for previous, point in zip(points, points[1:], strict=False):
        path += f" L{x(point[0]):.1f} {y(previous[1]):.1f} L{x(point[0]):.1f} {y(point[1]):.1f}"
    return path


def _scales(points: List[Point], left: float, right: float, top: float, bottom: float,
            extra: Sequence[float] = (), headroom: float = 0.0, also: Sequence[Point] = ()):
    """Maps time to x and price to y; `headroom` widens the price range by that share.

    `also` is a second line (the Depo or the new offer of the same product) drawn on the same scales."""
    started = min([points[0][0].timestamp()] + [moment.timestamp() for moment, _ in also[:1]])
    ended = max([points[-1][0].timestamp()] + [moment.timestamp() for moment, _ in also[-1:]])
    prices = [price for _, price in points] + [price for _, price in also] + list(extra)
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


PAIR_COLORS = {"Yeni": "#8ad8ff", "Depo": "#ffb347"}


def partner_legend(names: Tuple[str, str]) -> str:
    """Which line is which when the new offer and the Depo offer share one chart."""
    return "<div class='ov-legend'>" + "".join(
        f"<span><i class='ov-sw' style='background:{PAIR_COLORS[name]}'></i>{name}</span>" for name in names) + "</div>"


def detail_chart(points: List[Point], target: float, other: List[Point] = (),
                 names: Tuple[str, str] = ("Yeni", "Depo"), targets: List[Point] = ()) -> str:
    """The detail sheet chart: price steps, the target line and the first and last day.

    With `other` the second offer of the product (Depo next to new, or the reverse) is a separate amber line."""
    other = list(other)
    own_color = PAIR_COLORS[names[0]] if other else "currentColor"
    other_color = PAIR_COLORS[names[1]]
    pad = CHART_PAD
    begin = min([points[0][0]] + [moment for moment, _ in other[:1]])
    finish = max([points[-1][0]] + [moment for moment, _ in other[-1:]])
    goal = target_series(list(targets), target, begin, finish)
    x, y, low, high = _scales(points, pad["left"], CHART_WIDTH - pad["right"], pad["top"], CHART_HEIGHT - pad["bottom"],
                              [value for _, value in goal], headroom=0.14, also=other)
    floor = CHART_HEIGHT - pad["bottom"]
    grid = "".join(
        f"<line x1='{pad['left']}' x2='{CHART_WIDTH - pad['right']}' y1='{y(level):.1f}' y2='{y(level):.1f}' stroke='rgba(255,255,255,.07)'/>"
        f"<text x='{pad['left'] - 8}' y='{y(level) + 4:.1f}' text-anchor='end' font-size='11' fill='#9a95b8'>{_thousands(level)}</text>"
        for level in (low + (high - low) * step / 3 for step in range(4))
    )
    steps = _steps(points, x, y)
    area = f"{steps} L{x(points[-1][0]):.1f} {floor} L{x(points[0][0]):.1f} {floor} Z"
    dots = "".join(f"<circle class='ov-fade' cx='{x(moment):.1f}' cy='{y(price):.1f}' r='4' fill='{own_color}' stroke='#0a0814' stroke-width='2'/>"
                   for moment, price in points)
    first = min([points[0][0]] + [moment for moment, _ in other[:1]])
    last = max([points[-1][0]] + [moment for moment, _ in other[-1:]])
    partner = ""
    partner_hover = ""
    if other:
        partner = (f"<path d='{_steps(other, x, y)}' fill='none' stroke='{other_color}' stroke-width='3' "
                   "stroke-linejoin='round' stroke-linecap='round'/>"
                   + "".join(f"<circle cx='{x(moment):.1f}' cy='{y(price):.1f}' r='4' fill='{other_color}' "
                             "stroke='#0a0814' stroke-width='2'/>" for moment, price in other))
        partner_hover = " data-points-b='" + json.dumps([[round(x(moment), 1), round(y(price), 1), int(moment.timestamp()), round(price)]
                                                        for moment, price in other]) + "'"
    labels = (f"<text x='{x(first):.1f}' y='{CHART_HEIGHT - 7}' font-size='11' fill='#9a95b8'>{_day(first)}</text>"
              f"<text x='{x(last):.1f}' y='{CHART_HEIGHT - 7}' text-anchor='end' font-size='11' fill='#9a95b8'>{_day(last)}</text>")
    goal_path = _steps(goal, x, y) if len(goal) > 1 else f"M{x(begin):.1f} {y(target):.1f}"
    # Each stored change as [x, y, epoch seconds, price]: the page script answers "what was the price then" on hover.
    hover = json.dumps([[round(x(moment), 1), round(y(price), 1), int(moment.timestamp()), round(price)] for moment, price in points])
    return (
        f"<svg class='ov-chart' viewBox='0 0 {CHART_WIDTH} {CHART_HEIGHT}' role='img' aria-label='Fiyat geçmişi' "
        f"data-points='{hover}'{partner_hover} data-top='{pad['top']}' data-bottom='{floor}'>"
        f"{grid}<path d='{goal_path}' fill='none' stroke='#b6ff3b' stroke-dasharray='6 5' stroke-width='1.5'/>"
        f"<path class='ov-fade' d='{area}' fill='{own_color}' fill-opacity='.22'/>"
        f"<path class='ov-draw' pathLength='1' d='{steps}' fill='none' stroke='{own_color}' stroke-width='3' "
        f"stroke-linejoin='round' stroke-linecap='round'/>{dots}{partner}{labels}"
        "<line class='ov-cross' x1='0' x2='0' stroke='rgba(255,255,255,.35)' stroke-dasharray='3 3' opacity='0'/>"
        "<circle class='ov-cross-dot' r='6' fill='currentColor' stroke='#fff' stroke-width='2' opacity='0'/></svg>"
    )


RANGE_DAYS = (7, 30, 90)


def window_points(points: List[Point], now: datetime, days: int) -> List[Point]:
    """The line of the last `days`: it starts at the price that held when the window opened."""
    start = now - timedelta(days=days)
    inside = [point for point in points if point[0] >= start]
    before = [point for point in points if point[0] < start]
    if before:
        inside.insert(0, (start, before[-1][1]))
    return inside


def ranged_chart(points: List[Point], target: float, now: datetime, other: List[Point] = (),
                 names: Tuple[str, str] = ("Yeni", "Depo"), targets: List[Point] = ()) -> str:
    """The detail chart with a 7/30/90-day/all switch; only windows shorter than the history are offered.

    `other` is the product's second offer (see `detail_chart`), `names` the labels of (this line, that line)."""
    other = list(other)
    legend = partner_legend(names) if other else ""
    start = min([points[0][0]] + [moment for moment, _ in other[:1]]) if points else None
    end = max([points[-1][0]] + [moment for moment, _ in other[-1:]]) if points else None
    span = (end - start).days if points else 0
    options = [(f"{days} gün", str(days), window_points(points, now, days)) for days in RANGE_DAYS if days < span]
    if not options:
        return legend + detail_chart(points, target, other, names, targets)
    options.append(("Tümü", "all", points))
    default = "all" if span <= RANGE_DAYS[-1] else str(RANGE_DAYS[-1])
    buttons = "".join(f"<button type='button' class='ov-range' data-range='{key}' aria-pressed='{str(key == default).lower()}'>{label}</button>"
                      for label, key, _ in options)
    charts = "".join(f"<div class='ov-chartwrap' data-range-pane='{key}'{'' if key == default else ' hidden'}>"
                     f"{detail_chart(chosen if len(chosen) > 1 else points, target, _other_window(other, now, key), names, targets)}</div>"
                     for _, key, chosen in options)
    return f"<div class='ov-ranges' role='group' aria-label='Zaman aralığı'>{buttons}</div>{legend}{charts}"


def _other_window(other: List[Point], now: datetime, key: str) -> List[Point]:
    return other if key == "all" or not other else window_points(other, now, int(key))


def _day(moment: datetime) -> str:
    months = ("Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara")
    return f"{moment.day} {months[moment.month - 1]}"
