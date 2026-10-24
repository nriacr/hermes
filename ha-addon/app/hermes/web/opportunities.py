"""Fırsat Geçmişi tab of the home screen: the alerts Hermes sent, newest first, and how long each deal lasted.

Everything comes from the durable outbox of `hermes.db` (kept 30 days), the price changes recorded in the
same database (how long the price stayed at or under the target) and the published summary.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from html import escape
from typing import Dict, List, Optional, Tuple

from ..constants import DATABASE_PATH, STATE_PATH, SUMMARY_PATH
from ..history import _read, read_prices_by_key
from ..monitor import summary as summary_ops
from ..storage import load_json
from ..utils import format_tl, parse_iso_datetime
from .pages import site_theme_class

LIST_LIMIT = 100
DAYS = 30


@dataclass
class SentOpportunity:
    sent_at: datetime
    title: str
    product: str
    url: str
    price: Optional[Decimal]
    target: Optional[Decimal]
    kind: str  # "target", "stock" or "low90"
    offer_key: str = ""
    site: str = ""
    seller: str = ""
    warehouse: bool = False
    held: Optional[timedelta] = None  # how long the price stayed at the alert level; None = no price record
    ongoing: bool = False  # the offer is still listed at or under the ceiling
    at_least: bool = False  # the offer left the list without a higher price: held is only what was seen


def _decimal(value) -> Optional[Decimal]:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def read_sent(path=DATABASE_PATH, now: Optional[datetime] = None, limit: int = LIST_LIMIT) -> List[SentOpportunity]:
    now = now or datetime.now().astimezone()
    since = (now - timedelta(days=DAYS)).timestamp()
    rows = _read(path, "SELECT payload, delivered FROM outbox WHERE state='sent' AND delivered IS NOT NULL AND delivered>=? "
                       "ORDER BY delivered DESC LIMIT ?", (since, limit * 3))
    result = []
    for raw, delivered in rows:
        try:
            payload = json.loads(raw)
        except ValueError:
            continue
        # Only product alerts carry a price; system notices and Telegram matches do not.
        if not isinstance(payload, dict) or payload.get("price") is None or not payload.get("url"):
            continue
        lines = str(payload.get("message") or "").splitlines()
        product = lines[1] if len(lines) > 1 and lines[0].startswith("Site:") else str(payload.get("title") or "")
        kind = "low90" if payload.get("kind") == "low90" else "stock" if payload.get("stock_return") else "target"
        site = lines[0][5:].strip() if lines and lines[0].startswith("Site:") else ""
        opportunity = payload.get("opportunity")
        offer = opportunity[1] if isinstance(opportunity, list) and len(opportunity) > 1 and isinstance(opportunity[1], dict) else {}
        result.append(SentOpportunity(datetime.fromtimestamp(delivered).astimezone(), str(payload.get("title") or ""),
                                      product, str(payload["url"]), _decimal(payload["price"]), _decimal(payload.get("target")), kind,
                                      str(payload.get("offer_key") or ""), site, str(offer.get("seller") or ""),
                                      bool(offer.get("is_warehouse"))))
        if len(result) >= limit:
            break
    return result


Current = Dict[Tuple[str, bool], Decimal]


def add_durations(items: List[SentOpportunity], current: Current, path=DATABASE_PATH, state=None,
                  now: Optional[datetime] = None) -> List[SentOpportunity]:
    """How long each price stayed at the alert level: from the alert price to the first later price above the ceiling.

    The ceiling is the target (the alert price itself for a 90-day low). Prices are stored when they change,
    so an offer still listed at or under the ceiling is "ongoing" until now. An offer that left the list
    (sold out, Depo gone) without a higher price is counted until it was last seen, as a lower bound.
    """
    now = now or datetime.now().astimezone()
    state = state if isinstance(state, dict) else {}
    keys = sorted({item.offer_key for item in items if item.offer_key})
    points = read_prices_by_key(path, keys) if keys else {}
    for item in items:
        history = points.get(item.offer_key) or []
        if item.price is None or not history:
            continue
        ceiling = item.target if item.kind != "low90" and item.target is not None and item.target >= item.price else item.price
        # The price row of this alert is the latest one at the alert price written before the message was delivered.
        start = next((at for at, price in reversed(history) if price == item.price and at <= item.sent_at), None)
        if start is None:
            start = item.sent_at
        end = next((at for at, price in history if at > start and price > ceiling), None)
        if end is None:
            listed = current.get((item.url, item.warehouse))
            item.ongoing = listed is not None and listed <= ceiling
            if not item.ongoing:
                entry = state.get(item.offer_key)
                seen = parse_iso_datetime(entry.get("last_price_checked_at")) if isinstance(entry, dict) else None
                end = seen.astimezone() if seen and seen.tzinfo else start
                item.at_least = True
        item.held = max(timedelta(0), (end or now) - start)
    return items


def duration_label(span: timedelta) -> str:
    minutes = int(span.total_seconds() // 60)
    if minutes < 1:
        return "<1 dk"
    days, rest = divmod(minutes, 1440)
    hours, minutes = divmod(rest, 60)
    parts = [f"{days} gün"] if days else []
    if hours:
        parts.append(f"{hours} sa")
    if minutes and not days:
        parts.append(f"{minutes} dk")
    return " ".join(parts)


def current_prices(summary_path=SUMMARY_PATH) -> Current:
    # A Depo (used) offer shares its page with the new one, so it is told apart by the warehouse flag.
    return {(row.product_url, bool(row.is_warehouse)): row.price
            for row in summary_ops.rows_from_payload(load_json(summary_path, {}))}


KIND_LABELS = {"target": "Hedef fiyat", "stock": "Stoğa döndü", "low90": "90 günün dibi"}


def held_line(item: SentOpportunity) -> Tuple[str, str]:
    """How long the deal lasted and how it ended, as two short lines."""
    if item.held is None:
        return "süre yok", "kayıt yok"
    label = duration_label(item.held)
    if item.at_least:
        return f"en az {label}", "listeden çıktı"
    return label, "sürüyor" if item.ongoing else "bitti"


def render_rows(items: List[SentOpportunity], current: Current) -> str:
    """The Fırsat Geçmişi tab: the same row cards as the stock tab, one fixed layout for every alert."""
    if not items:
        return "<p class='ov-empty'>Son 30 günde gönderilmiş fırsat bildirimi yok.</p>"
    rows = []
    for item in items:
        site = item.site or item.seller or "-"
        price = format_tl(item.price, with_currency=True) if item.price is not None else "-"
        target = format_tl(item.target, with_currency=True) if item.target is not None else "-"
        # "Amazon Depo" says no more than the DEPO tag; a marketplace seller is shown.
        seller = f" · {escape(item.seller)}" if item.seller and not item.seller.casefold().startswith(site.casefold()) else ""
        depo = "<span class='ov-depo'>DEPO</span> " if item.warehouse else ""
        listed = current.get((item.url, item.warehouse))
        now = f" · şimdi {escape(format_tl(listed, with_currency=True))}" if listed is not None else ""
        kind = "" if item.kind == "target" else f"{escape(KIND_LABELS[item.kind])} · "
        held, ended = held_line(item)
        rows.append(
            f"<li class='ov-row ov-hist {site_theme_class(site)}'>"
            f"<div class='ov-av'>{escape(site[:1].upper())}</div><div class='ov-tx'>"
            f"<h4><a href='{escape(item.url, quote=True)}' target='_blank' rel='noopener noreferrer'>{escape(item.product)}</a></h4>"
            f"<span>{escape(item.sent_at.strftime('%d.%m %H:%M'))} · {escape(site)}{seller}</span>"
            f"<span>{depo}{kind}hedef {escape(target)}{now}</span></div>"
            f"<div class='ov-hist-side'><b>{escape(price)}</b><small>{escape(held)}</small><small>{escape(ended)}</small></div></li>")
    return f"<ul class='ov-rows'>{''.join(rows)}</ul>"


def history_pane() -> Tuple[int, str]:
    """Number of alerts and the tab content; a damaged record never breaks the home screen."""
    try:
        current = current_prices()
        items = add_durations(read_sent(), current, state=load_json(STATE_PATH, {}))
    except (OSError, ValueError, RuntimeError):
        return 0, "<p class='ov-empty'>Fırsat geçmişi şu an okunamadı.</p>"
    return len(items), render_rows(items, current)
