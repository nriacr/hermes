"""Fırsat geçmişi: the notifications Hermes sent, newest first, and what each product costs now.

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
from .dashboard import relative_time_text, site_theme_class
from .pages import render_page

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
        return "1 dk'dan az"
    days, rest = divmod(minutes, 1440)
    hours, minutes = divmod(rest, 60)
    parts = [f"{days} gün"] if days else []
    if hours:
        parts.append(f"{hours} sa")
    if minutes and not days:
        parts.append(f"{minutes} dk")
    return " ".join(parts)


def held_html(item: SentOpportunity) -> str:
    if item.held is None:
        return "<span class='ov-chg flat'>kayıt yok</span>"
    label = escape(duration_label(item.held))
    if item.at_least:
        return f"<b>en az {label}</b><br><small>sonra listeden çıktı</small>"
    return f"<b>{label}</b><br><small>{'hâlâ sürüyor' if item.ongoing else 'sonra fiyat yükseldi'}</small>"


def current_prices(summary_path=SUMMARY_PATH) -> Current:
    # A Depo (used) offer shares its page with the new one, so it is told apart by the warehouse flag.
    return {(row.product_url, bool(row.is_warehouse)): row.price
            for row in summary_ops.rows_from_payload(load_json(summary_path, {}))}


def status_text(item: SentOpportunity, current: Current) -> str:
    now = current.get((item.url, item.warehouse))
    if now is None:
        return "<span class='ov-chg flat'>şu an listede yok</span>"
    price = format_tl(now, with_currency=True)
    if item.target is not None and now <= item.target:
        return f"<span class='ov-chg flat'>hâlâ hedefte · {escape(price)}</span>"
    if item.price is not None and now > item.price:
        return f"<span class='ov-chg flat'>yükseldi · {escape(price)}</span>"
    return f"<span class='ov-chg flat'>şimdi {escape(price)}</span>"


KIND_LABELS = {"target": "Hedef fiyat", "stock": "Stoğa döndü", "low90": "90 günün dibi"}


def render_rows(items: List[SentOpportunity], current: Current) -> str:
    if not items:
        return "<p class='ov-empty'>Son 30 günde gönderilmiş fırsat bildirimi yok.</p>"
    rows = []
    for item in items:
        price = format_tl(item.price, with_currency=True) if item.price is not None else "-"
        target = format_tl(item.target, with_currency=True) if item.target is not None else "-"
        site_css = site_theme_class(item.site or item.seller)
        depo = " <span class='ov-depo'>DEPO</span>" if item.warehouse else ""
        seller = f" · satıcı {escape(item.seller)}" if item.seller and item.seller.casefold() != item.site.casefold() else ""
        site = (f"<span class='{site_css}'><span class='ov-site'>{escape(item.site)}</span></span>{depo}{seller}<br>"
                if item.site else "")
        when = f"{item.sent_at.strftime('%d.%m %H:%M')} · {relative_time_text(item.sent_at.strftime('%Y-%m-%d %H:%M:%S'))}"
        rows.append(
            f"<tr><td>{escape(when)}</td>"
            f"<td><a href='{escape(item.url, quote=True)}' target='_blank' rel='noopener noreferrer'>{escape(item.product)}</a>"
            f"<br><small>{site}{escape(KIND_LABELS[item.kind])}</small></td>"
            f"<td>{escape(price)}<br><small>hedef {escape(target)}</small></td><td>{held_html(item)}</td>"
            f"<td>{status_text(item, current)}</td></tr>")
    return ("<div class='table-wrap measure-wrap'><table class='statistics-table measure-table'><thead><tr>"
            "<th>Zaman</th><th>Ürün</th><th>Yakalanan fiyat</th><th>Fırsat süresi</th><th>Şimdi</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></div>")


def render_opportunities_page(base: str, params=None) -> bytes:
    current = current_prices()
    body = ("<div class='statistics-top'><h2 class='page-heading'>Fırsat geçmişi</h2></div>"
            + render_rows(add_durations(read_sent(), current, state=load_json(STATE_PATH, {})), current)
            + "<p class='ov-empty'>Süre, fiyatın hedefin altında kaldığı zamandır. Ürün listeden çıkarsa son görüldüğü ana kadar sayılır.</p>")
    return render_page(base, "opportunities", "Hermes Fırsat geçmişi", body, body_class="public ov")
