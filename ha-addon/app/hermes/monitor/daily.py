"""One short daily summary message: reads, errors and the best opportunities of the last 24 hours."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from ..history import read_reads
from ..utils import format_tl, site_label
from . import summary as summary_ops
from .stall import SUCCESS_OUTCOMES

SUMMARY_HOUR = 9
TOP_DEALS = 3
# Counted neither as success nor as error: Hermes never reveals a deliberately silent CAPTCHA/503 hold.
SILENT_OUTCOMES = frozenset({"captcha", "http_503", "http_429", "interrupted"})


def _count(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def build_daily_summary(database_path, summary_payload: Dict[str, Any], now: Optional[datetime] = None) -> Optional[str]:
    now = now or datetime.now(timezone.utc)
    reads = read_reads(database_path, now - timedelta(hours=24))
    total = len(reads)
    errors: Dict[str, int] = {}
    for read in reads:
        if read.outcome not in SUCCESS_OUTCOMES and read.outcome not in SILENT_OUTCOMES:
            errors[read.site] = errors.get(read.site, 0) + 1
    failed = sum(errors.values())
    lines = ["Son 24 saatin özeti"]
    if total:
        share = failed / total * 100
        lines.append(f"Okuma: {_count(total)} · hata: {_count(failed)} (%{f'{share:.1f}'.replace('.', ',')})")
    else:
        lines.append("Okuma kaydı yok.")
    if errors:
        worst = max(errors, key=errors.get)
        lines.append(f"En çok hata: {site_label(worst)} ({_count(errors[worst])})")
    deals = [row for row in summary_ops.rows_from_payload(summary_payload) if row.target_price and row.price <= row.target_price]
    deals.sort(key=lambda row: (row.price - row.target_price) / row.target_price)
    if deals:
        lines.append(f"Hedefte veya altında: {len(deals)} ürün")
        for row in deals[:TOP_DEALS]:
            name = row.product_title if len(row.product_title) <= 60 else row.product_title[:57] + "…"
            lines.append(f"• {name} — {format_tl(row.price, with_currency=True)} (hedef {format_tl(row.target_price, with_currency=True)})")
    else:
        lines.append("Şu an hedefin altında ürün yok.")
    return "\n".join(lines)


def summary_due(local_now: datetime, last_sent_date: str, enabled: bool) -> bool:
    return enabled and local_now.hour >= SUMMARY_HOUR and local_now.hour < 22 and last_sent_date != local_now.date().isoformat()

