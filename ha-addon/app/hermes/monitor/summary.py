"""The published price table (`latest_price_summary.json`) and its log copy."""

import time
from pathlib import Path
from typing import Any, Dict, List

from ..constants import normalize_priority
from ..errors import HermesError
from ..logging_utils import log
from ..models import PriceSummaryRow, StockSummaryRow, WatchRule
from ..storage import load_json, save_json
from ..utils import (
    canonical_tracking_url,
    format_duration,
    format_local_datetime,
    format_signed_tl,
    format_tl,
    local_now,
    log_cell,
    normalize_item_key,
    parse_decimal,
    tracking_offer_identity,
    tracking_offer_title_identity,
)
from .state import sanitized_price_bounds, state_decimal

# The log repeats an unchanged table at most this often; idle cycles run every
# few seconds and would otherwise fill the log with identical tables.
UNCHANGED_TABLE_LOG_SECONDS = 30 * 60
_last_logged_table: Dict[str, Any] = {"signature": None, "at": 0.0}


def result_group_for_watch(watch: WatchRule, fallback_label: str = "") -> tuple[str, str]:
    """Group all outputs from one configured watch without mixing sites or watches."""
    label = str(watch.name or fallback_label or "").strip()
    if not label:
        return "", ""
    # Named cards share one group across their links; unnamed product links
    # remain isolated by their configured URL.
    identity = watch.tracking_id or watch.name or watch.url
    return normalize_item_key("watch_result_group", watch.site, identity, str(watch.target_price)), label


# -- rows kept from earlier reads -----------------------------------------------


def summary_row_from_state(watch: WatchRule, state_entry: Dict[str, Any], seller: str):
    price = state_decimal(state_entry.get("last_price"))
    if price is None:
        return None
    min_price, max_price = sanitized_price_bounds(state_entry, price, watch.target_price)
    product_title = str(state_entry.get("title") or watch.name or watch.url)
    search_group = str(state_entry.get("search_group") or "")
    search_group_label = str(state_entry.get("search_group_label") or "")
    if not search_group:
        search_group, search_group_label = result_group_for_watch(watch, product_title)
    return PriceSummaryRow(
        seller=seller,
        product_title=product_title,
        product_url=str(state_entry.get("url") or state_entry.get("configured_url") or watch.url),
        price=price,
        target_price=watch.target_price,
        min_price=min_price,
        max_price=max_price,
        search_group=search_group,
        search_group_label=search_group_label,
        tracking_id=str(state_entry.get("tracking_id") or watch.tracking_id or ""),
        is_warehouse=bool(state_entry.get("is_warehouse", False)),
        # The card's current priority: a state entry keeps the value of its last read.
        priority=normalize_priority(watch.priority),
        # Older offer entries only stored the read time as last_checked_at.
        price_checked_at=str(state_entry.get("last_price_checked_at") or state_entry.get("last_checked_at") or ""),
    )


def cached_summary_rows(watch: WatchRule, key: str, state: Dict[str, Any], seller: str) -> List[PriceSummaryRow]:
    """Last successful rows of a watch that is not read in this cycle."""
    base_entry = state.get(key, {})
    if not isinstance(base_entry, dict):
        base_entry = {}
    offer_keys = [str(item) for item in base_entry.get("offer_keys", []) if item] if isinstance(base_entry.get("offer_keys"), list) else []
    if not offer_keys and "offer_keys" not in base_entry:
        offer_keys = [key]
    rows = []
    for item_key in offer_keys:
        entry = state.get(item_key, {})
        row = summary_row_from_state(watch, entry, seller) if isinstance(entry, dict) else None
        if row:
            rows.append(row)
    return rows


def _stock_checked_at(entry: Dict[str, Any]) -> str:
    # Older entries predate unavailable_checked_at; the out-of-stock or read time is the closest record.
    return str(entry.get("unavailable_checked_at") or entry.get("last_out_of_stock_at")
               or entry.get("last_checked_at") or "")


def cached_stock_rows(watch: WatchRule, entry: Dict[str, Any], seller: str) -> List[StockSummaryRow]:
    unavailable = entry.get("unavailable_variants", [])
    if not isinstance(unavailable, list):
        return []
    return [
        StockSummaryRow(seller, str(item["product_title"]), str(item["product_url"]), watch.target_price,
                        str(item.get("reason") or "Stokta yok"), _stock_checked_at(entry))
        for item in unavailable
        if isinstance(item, dict) and item.get("product_title") and item.get("product_url")
    ]


def price_row_identity(row: PriceSummaryRow) -> str:
    """Keep normal and verified Amazon Depo offers separate in live updates."""
    identity = tracking_offer_identity(row.product_url, row.is_warehouse)
    if identity and row.tracking_id:
        return f"{row.tracking_id}|{identity}"
    return identity


def cached_offer_ids(watch: WatchRule, key: str, state: Dict[str, Any], seller: str) -> set[str]:
    """This watch's published offer identities before its state is replaced."""
    return {identity for row in cached_summary_rows(watch, key, state, seller) if (identity := price_row_identity(row))}


# -- deduplication and ordering -------------------------------------------------


def sorted_summary_rows(rows: List[PriceSummaryRow]) -> List[PriceSummaryRow]:
    return sorted(rows, key=lambda row: (row.seller.casefold(), abs(row.difference), row.price))


def sorted_stock_rows(rows: List[StockSummaryRow]) -> List[StockSummaryRow]:
    return sorted(rows, key=lambda row: (row.seller.casefold(), row.product_title.casefold(), row.product_url))


def deduplicate_summary_rows(rows: List[PriceSummaryRow]) -> List[PriceSummaryRow]:
    """Keep one row per product offer without collapsing normal and Warehouse stock."""
    unique_rows: Dict[str, PriceSummaryRow] = {}
    title_keys: Dict[str, str] = {}
    for row in rows:
        row_key = tracking_offer_identity(row.product_url, row.is_warehouse)
        if not row_key:
            unique_rows[f"__missing_url__:{len(unique_rows)}"] = row
            continue
        if row.tracking_id:
            row_key = f"{row.tracking_id}|{row_key}"
        title_key = tracking_offer_title_identity(row.product_title, row.tracking_id, row.seller, row.is_warehouse)
        existing_key = title_keys.get(title_key, row_key) if title_key else row_key
        current = unique_rows.get(existing_key)
        if current is None:
            unique_rows[row_key] = row
            if title_key:
                title_keys[title_key] = row_key
            continue
        # Preserve the best visible price and the complete price range when
        # normal and Warehouse search paths report the same offer.
        if row.price < current.price:
            row.min_price = min(row.min_price, current.min_price)
            row.max_price = max(row.max_price, current.max_price)
            unique_rows[existing_key] = row
        elif row.price == current.price:
            current.min_price = min(current.min_price, row.min_price)
            current.max_price = max(current.max_price, row.max_price)
            if row.price_checked_at > current.price_checked_at:
                row.min_price = current.min_price
                row.max_price = current.max_price
                unique_rows[existing_key] = row
        if title_key:
            title_keys[title_key] = existing_key
    return list(unique_rows.values())


# -- the summary file -----------------------------------------------------------


def _row_payload(index: int, row: PriceSummaryRow) -> Dict[str, Any]:
    price_range = f"{format_tl(row.min_price, with_currency=True)} / {format_tl(row.max_price, with_currency=True)}"
    return {
        "no": index,
        "seller": row.seller,
        "product_title": row.product_title,
        "product_url": row.product_url,
        "price": format_tl(row.price, with_currency=True),
        "target": format_tl(row.target_price, with_currency=True),
        "difference": format_signed_tl(row.difference, with_currency=True),
        "min_price": format_tl(row.min_price, with_currency=True),
        "max_price": format_tl(row.max_price, with_currency=True),
        "price_range": price_range,
        "is_target_hit": row.price <= row.target_price,
        "search_group": row.search_group,
        "search_group_label": row.search_group_label,
        "is_warehouse": row.is_warehouse,
        "tracking_id": row.tracking_id,
        "priority": row.priority,
        "price_checked_at": row.price_checked_at,
    }


def save_price_summary(path: Path, rows: List[PriceSummaryRow], stock_rows: List[StockSummaryRow] | None = None,
                       cycle_duration_seconds: float | None = None, scan_duration_seconds: float | None = None) -> None:
    sorted_rows = sorted_summary_rows(deduplicate_summary_rows(rows))
    sorted_stock = sorted_stock_rows(stock_rows or [])
    previous = load_json(path, {})
    previous = previous if isinstance(previous, dict) else {}
    if cycle_duration_seconds is None:
        cycle_duration_seconds = previous.get("cycle_duration_seconds")
    if scan_duration_seconds is None:
        scan_duration_seconds = previous.get("scan_duration_seconds")
    save_json(path, {
        "checked_at": format_local_datetime(local_now()),
        "row_count": len(sorted_rows),
        "stock_row_count": len(sorted_stock),
        "cycle_duration_seconds": cycle_duration_seconds,
        "cycle_duration_minutes": format_duration(cycle_duration_seconds),
        "scan_duration_seconds": scan_duration_seconds,
        "scan_duration_minutes": format_duration(scan_duration_seconds),
        "rows": [_row_payload(index, row) for index, row in enumerate(sorted_rows, start=1)],
        "stock_rows": [
            {"no": index, "seller": row.seller, "product_title": row.product_title, "product_url": row.product_url,
             "target": format_tl(row.target_price, with_currency=True), "reason": row.reason,
             "checked_at": row.checked_at}
            for index, row in enumerate(sorted_stock, start=1)
        ],
    })


def rows_from_payload(payload: Dict[str, Any]) -> List[PriceSummaryRow]:
    """Restore the published rows so an alert can update only its fresh results."""
    rows = []
    for raw in payload.get("rows", []) if isinstance(payload, dict) and isinstance(payload.get("rows"), list) else []:
        if not isinstance(raw, dict):
            continue
        try:
            rows.append(PriceSummaryRow(
                seller=str(raw.get("seller") or ""),
                product_title=str(raw.get("product_title") or ""),
                product_url=str(raw.get("product_url") or ""),
                price=parse_decimal(str(raw.get("price") or "")),
                target_price=parse_decimal(str(raw.get("target") or "")),
                min_price=parse_decimal(str(raw.get("min_price") or raw.get("price") or "")),
                max_price=parse_decimal(str(raw.get("max_price") or raw.get("price") or "")),
                search_group=str(raw.get("search_group") or ""),
                search_group_label=str(raw.get("search_group_label") or ""),
                is_warehouse=bool(raw.get("is_warehouse", False)),
                tracking_id=str(raw.get("tracking_id") or ""),
                priority=normalize_priority(raw.get("priority")),
                price_checked_at=str(raw.get("price_checked_at") or ""),
            ))
        except HermesError:
            continue
    return rows


def stock_rows_from_payload(payload: Dict[str, Any]) -> List[StockSummaryRow]:
    rows = []
    for raw in payload.get("stock_rows", []) if isinstance(payload, dict) and isinstance(payload.get("stock_rows"), list) else []:
        if not isinstance(raw, dict):
            continue
        try:
            rows.append(StockSummaryRow(
                seller=str(raw.get("seller") or ""),
                product_title=str(raw.get("product_title") or ""),
                product_url=str(raw.get("product_url") or ""),
                target_price=parse_decimal(str(raw.get("target") or "")),
                reason=str(raw.get("reason") or ""),
                checked_at=str(raw.get("checked_at") or ""),
            ))
        except HermesError:
            continue
    return rows


def _row_urls(rows) -> set[str]:
    return {url for row in rows if (url := canonical_tracking_url(row.product_url))}


def save_incremental_summary(path: Path, fresh_rows: List[PriceSummaryRow], fresh_stock_rows: List[StockSummaryRow] | None = None,
                             removed_price_ids: set[str] | None = None) -> None:
    """Publish fresh rows immediately without hiding rows pending later in the cycle."""
    previous = load_json(path, {})
    previous = previous if isinstance(previous, dict) else {}
    fresh_stock_rows = fresh_stock_rows or []
    fresh_ids = {identity for row in fresh_rows if (identity := price_row_identity(row))}
    fresh_price_urls = _row_urls(fresh_rows)
    fresh_stock_urls = _row_urls(fresh_stock_rows)
    removed = {str(item) for item in (removed_price_ids or set()) if item}
    merged_rows = [
        row for row in rows_from_payload(previous)
        if price_row_identity(row) not in fresh_ids
        and canonical_tracking_url(row.product_url) not in fresh_stock_urls
        and price_row_identity(row) not in removed
    ] + list(fresh_rows)
    merged_stock = [
        row for row in stock_rows_from_payload(previous)
        if canonical_tracking_url(row.product_url) not in fresh_price_urls
        and canonical_tracking_url(row.product_url) not in fresh_stock_urls
    ] + list(fresh_stock_rows)
    save_price_summary(path, merged_rows, merged_stock)


def reset_summary_price_ranges(path: Path) -> None:
    """After a min/max reset the table restarts its range at the current price."""
    summary = load_json(path, {})
    if not isinstance(summary, dict) or not isinstance(summary.get("rows"), list):
        return
    for row in summary["rows"]:
        price = str(row.get("price") or "").strip() if isinstance(row, dict) else ""
        if price:
            row["min_price"] = price
            row["max_price"] = price
            row["price_range"] = f"{price} / {price}"
    save_json(path, summary)


def log_price_summary(rows: List[PriceSummaryRow], now: float | None = None) -> None:
    """Log the table when it changed, otherwise at most every 30 minutes."""
    unique_rows = sorted_summary_rows(deduplicate_summary_rows(rows))
    signature = tuple((row.seller, row.product_title, str(row.price), str(row.target_price)) for row in unique_rows)
    now = time.monotonic() if now is None else now
    if signature == _last_logged_table["signature"] and now - _last_logged_table["at"] < UNCHANGED_TABLE_LOG_SECONDS:
        return
    _last_logged_table.update(signature=signature, at=now)
    log(f"Özet: eşleşen={len(unique_rows)}")
    if not unique_rows:
        return
    widths = (3, 12, 40, 13)
    log(f"{'No':>{widths[0]}} | {log_cell('Satıcı', widths[1])} | {log_cell('Ürün Adı', widths[2])} | "
        f"{'Fiyat':>{widths[3]}} | {'Hedef':>{widths[3]}} | {'Fark':>{widths[3]}}")
    log("-+-".join("-" * width for width in (widths[0], widths[1], widths[2], widths[3], widths[3], widths[3])))
    for index, row in enumerate(unique_rows, start=1):
        log(f"{index:>{widths[0]}} | {log_cell(row.seller, widths[1])} | {log_cell(row.product_title, widths[2])} | "
            f"{format_tl(row.price, with_currency=True):>{widths[3]}} | "
            f"{format_tl(row.target_price, with_currency=True):>{widths[3]}} | "
            f"{format_signed_tl(row.difference, with_currency=True):>{widths[3]}}")


def publish_price_summary(path: Path, rows: List[PriceSummaryRow], stock_rows: List[StockSummaryRow] | None = None,
                          cycle_duration_seconds: float | None = None, scan_duration_seconds: float | None = None) -> None:
    unique_rows = deduplicate_summary_rows(rows)
    if len(rows) != len(unique_rows):
        log(f"Özet tablodan aynı ürün linki tekrarları ayıklandı: adet={len(rows) - len(unique_rows)}")
    save_price_summary(path, unique_rows, stock_rows, cycle_duration_seconds, scan_duration_seconds)
    log_price_summary(unique_rows)

