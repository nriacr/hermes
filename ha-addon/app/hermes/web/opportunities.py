"""Fırsat geçmişi: the notifications Hermes sent, newest first, and what each product costs now.

Everything comes from the durable outbox of `hermes.db` (kept 30 days) and the published summary.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from html import escape
from typing import Dict, List, Optional

from ..constants import DATABASE_PATH, SUMMARY_PATH
from ..history import _read
from ..monitor import summary as summary_ops
from ..storage import load_json
from ..utils import format_tl
from .dashboard import relative_time_text
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
        result.append(SentOpportunity(datetime.fromtimestamp(delivered).astimezone(), str(payload.get("title") or ""),
                                      product, str(payload["url"]), _decimal(payload["price"]), _decimal(payload.get("target")), kind))
        if len(result) >= limit:
            break
    return result


def current_prices(summary_path=SUMMARY_PATH) -> Dict[str, Decimal]:
    return {row.product_url: row.price for row in summary_ops.rows_from_payload(load_json(summary_path, {}))}


def status_text(item: SentOpportunity, current: Dict[str, Decimal]) -> str:
    now = current.get(item.url)
    if now is None:
        return "<span class='ov-chg flat'>şu an listede yok</span>"
    price = format_tl(now, with_currency=True)
    if item.target is not None and now <= item.target:
        return f"<span class='ov-chg down'>hâlâ hedefte · {escape(price)}</span>"
    if item.price is not None and now > item.price:
        return f"<span class='ov-chg up'>yükseldi · {escape(price)}</span>"
    return f"<span class='ov-chg flat'>şimdi {escape(price)}</span>"


KIND_LABELS = {"target": "Hedef fiyat", "stock": "Stoğa döndü", "low90": "90 günün dibi"}


def render_rows(items: List[SentOpportunity], current: Dict[str, Decimal]) -> str:
    if not items:
        return "<p class='ov-empty'>Son 30 günde gönderilmiş fırsat bildirimi yok.</p>"
    rows = []
    for item in items:
        price = format_tl(item.price, with_currency=True) if item.price is not None else "-"
        target = format_tl(item.target, with_currency=True) if item.target is not None else "-"
        when = f"{item.sent_at.strftime('%d.%m %H:%M')} · {relative_time_text(item.sent_at.strftime('%Y-%m-%d %H:%M:%S'))}"
        rows.append(
            f"<tr><td>{escape(when)}</td>"
            f"<td><a href='{escape(item.url, quote=True)}' target='_blank' rel='noopener noreferrer'>{escape(item.product)}</a>"
            f"<br><small>{escape(KIND_LABELS[item.kind])}</small></td>"
            f"<td>{escape(price)}<br><small>hedef {escape(target)}</small></td><td>{status_text(item, current)}</td></tr>")
    return ("<div class='table-wrap measure-wrap'><table class='statistics-table measure-table'><thead><tr>"
            "<th>Zaman</th><th>Ürün</th><th>Bildirimde</th><th>Şimdi</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></div>")


def render_opportunities_page(base: str, params=None) -> bytes:
    body = ("<div class='statistics-top'><h2 class='page-heading'>Fırsat geçmişi</h2></div>"
            + render_rows(read_sent(), current_prices()))
    return render_page(base, "opportunities", "Hermes Fırsat geçmişi", body, body_class="public ov")
