import os
import random
import time
from datetime import timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List

import requests

from .config_loader import load_config
from .constants import (
    AMAZON_SEARCH_ERROR_NOTIFICATION_HOUR,
    APP_VERSION,
    NOTIFY_REPEAT_SECONDS,
    SITE_AMAZON,
    SITE_HEPSIBURADA,
    SITE_HM,
    SITE_NORDBRON,
    SITE_ZARA,
    STATE_PATH,
    SUMMARY_PATH,
)
from .errors import HermesError, OutOfStockHermesError
from .http_client import (
    cleaned_html,
    fetch_amazon_page,
    fetch_hepsiburada_page,
    fetch_hm_page,
    fetch_with_retries,
    fetch_zara_page,
)
from .logging_utils import log
from .models import HermesConfig, OfferResult, PriceSummaryRow, SearchResultItem, StockSummaryRow, WatchRule
from .notifier import send_pushover
from .providers import hepsiburada as hepsiburada_provider
from .providers import hm as hm_provider
from .providers import zara as zara_provider
from .providers.registry import extract_offer
from .search_amazon import (
    dedupe_results,
    extract_result_candidates,
    filter_matching_results,
    title_matches_any_keyword,
)
from .storage import load_json, save_json
from .telegram_listener import start_telegram_listener
from .utils import (
    format_local_datetime,
    format_signed_tl,
    format_tl,
    is_amazon_search_url,
    local_now,
    log_cell,
    normalize_item_key,
    normalize_key,
    normalize_offer_text,
    parse_iso_datetime,
    site_label,
    utc_now,
)


AGE_VERIFICATION_MARKERS = (
    "yas dogrulamasi",
    "yaş doğrulaması",
    "18 yasindan buyuk musunuz",
    "18 yaşından büyük müsünüz",
)
PRODUCT_MISSING_PRICE_MARKERS = (
    "fiyat bulunamadi",
    "fiyat bulunamadı",
    "okunabilir fiyat bulunamadi",
    "okunabilir fiyat bulunamadı",
    "fiyat yakalanamadi",
    "fiyat yakalanamadı",
    "stokta degil",
    "stokta değil",
)
SUMMARY_DROP_MIN_DELTA = 4
SUMMARY_DROP_RATIO_DIVISOR = 4
SUMMARY_DROP_ALERT_COOLDOWN_SECONDS = 60 * 60
SUMMARY_DROP_QUIET_START_HOUR = 22
SUMMARY_DROP_QUIET_END_HOUR = 8
AMAZON_EMPTY_ALERT_MIN_PAGES = 2
AMAZON_EMPTY_ALERT_MIN_FAILED_LINKS = 3
PRICE_HISTORY_SPIKE_RATIO = Decimal("5")
PRICE_HISTORY_SPIKE_ABSOLUTE_TL = Decimal("50000")
PRICE_HISTORY_KEYS = ("min_price", "max_price", "min_price_at", "max_price_at")


def raise_if_age_verification(html: str) -> None:
    normalized = normalize_offer_text(html)
    if any(marker in normalized for marker in AGE_VERIFICATION_MARKERS):
        raise HermesError("Yaş doğrulaması gerekiyor. Bu sayfa otomatik takip edilemiyor.")


def is_bot_protection_page(site: str, html: str) -> bool:
    lowered = html.lower()
    normalized = normalize_offer_text(html)
    if site == SITE_ZARA:
        return "bm-verify" in normalized and "_sec/verify" in normalized
    if site == SITE_HM:
        return any(
            marker in normalized
            for marker in (
                "access denied",
                "sec-if-cpt",
                "you don't have permission to access",
                "akamai bot manager",
                "akamai security",
            )
        )
    if site == SITE_HEPSIBURADA and any(
        marker in normalized
        for marker in (
            "hepsiburada guvenlik",
            "hbblockandcaptcha",
            "static hepsiburada net security",
        )
    ):
        return True
    if "captcha" not in lowered or "robot" not in lowered:
        return False
    if site == SITE_NORDBRON and "product-detail_price" in lowered:
        return False
    return True


def _clear_price_history_fields(value: Any) -> int:
    if isinstance(value, list):
        return sum(_clear_price_history_fields(item) for item in value)
    if not isinstance(value, dict):
        return 0
    cleared_count = 0
    for field_name in PRICE_HISTORY_KEYS:
        if field_name in value:
            value.pop(field_name, None)
            cleared_count += 1
    for child in value.values():
        cleared_count += _clear_price_history_fields(child)
    return cleared_count


def reset_price_history() -> int:
    state = load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    meta = state.get("_meta")
    if not isinstance(meta, dict):
        meta = {}
    cleared_count = 0
    for key, value in state.items():
        if key == "_meta":
            continue
        cleared_count += _clear_price_history_fields(value)
    meta["price_history_reset_at"] = utc_now()
    state["_meta"] = meta
    save_json(STATE_PATH, state)

    summary = load_json(SUMMARY_PATH, {})
    if isinstance(summary, dict) and isinstance(summary.get("rows"), list):
        for row in summary["rows"]:
            if not isinstance(row, dict):
                continue
            price = str(row.get("price") or "").strip()
            if not price:
                continue
            row["min_price"] = price
            row["max_price"] = price
            row["price_range"] = f"{price} / {price}"
        save_json(SUMMARY_PATH, summary)

    log(f"Min/maks fiyat gecmisi sifirlandi: alan={cleared_count}")
    return cleared_count


def sorted_summary_rows(rows: List[PriceSummaryRow]) -> List[PriceSummaryRow]:
    return sorted(rows, key=lambda row: (row.seller.casefold(), abs(row.difference), row.price))


def deduplicate_summary_rows(rows: List[PriceSummaryRow]) -> List[PriceSummaryRow]:
    """Keep one row per exact product URL without collapsing distinct variants."""
    unique_rows: Dict[str, PriceSummaryRow] = {}
    for row in rows:
        url = str(row.product_url or "").strip()
        if not url:
            unique_rows[f"__missing_url__:{len(unique_rows)}"] = row
            continue
        current = unique_rows.get(url)
        if current is None or row.price < current.price:
            unique_rows[url] = row
    return list(unique_rows.values())


def sorted_stock_rows(rows: List[StockSummaryRow]) -> List[StockSummaryRow]:
    return sorted(rows, key=lambda row: (row.seller.casefold(), row.product_title.casefold(), row.product_url))


def _state_decimal(value: Any):
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _history_reference_price(state_entry: Dict[str, Any], target_price: Decimal | None = None) -> Decimal | None:
    references = []
    if target_price is not None and target_price > 0:
        references.append(target_price)
    last_price = _state_decimal(state_entry.get("last_price"))
    if last_price is not None and last_price > 0:
        if target_price is None or not (
            last_price > target_price * PRICE_HISTORY_SPIKE_RATIO
            and last_price - target_price >= PRICE_HISTORY_SPIKE_ABSOLUTE_TL
        ):
            references.append(last_price)
    return max(references) if references else None


def _is_absurd_current_price(
    state_entry: Dict[str, Any], current_price: Decimal, target_price: Decimal | None = None
) -> bool:
    reference = _history_reference_price(state_entry, target_price)
    if reference is None:
        return False
    return current_price > reference * PRICE_HISTORY_SPIKE_RATIO and current_price - reference >= PRICE_HISTORY_SPIKE_ABSOLUTE_TL


def sanitized_price_bounds(
    state_entry: Dict[str, Any],
    current_price: Decimal,
    target_price: Decimal | None = None,
    context: str = "",
):
    min_price = _state_decimal(state_entry.get("min_price"))
    max_price = _state_decimal(state_entry.get("max_price"))
    if _is_absurd_current_price(state_entry, current_price, target_price):
        if context:
            log(f"Supheli fiyat min/maks gecmisine eklenmedi: {context} | fiyat={current_price}")
        if min_price is not None and max_price is not None:
            return min_price, max_price
    if min_price is None or current_price < min_price:
        min_price = current_price
    if max_price is None or current_price > max_price:
        max_price = current_price
    return min_price, max_price


def watch_check_due(watch: WatchRule, state_entry: Dict[str, Any], global_interval_seconds: int) -> bool:
    interval_seconds = watch.check_interval_minutes * 60 if watch.check_interval_minutes else global_interval_seconds
    last_checked = parse_iso_datetime(state_entry.get("last_checked_at"))
    if not last_checked:
        return True
    elapsed_seconds = (local_now().astimezone(timezone.utc) - last_checked).total_seconds()
    return elapsed_seconds >= interval_seconds


def summary_row_from_state(watch: WatchRule, state_entry: Dict[str, Any], seller: str):
    price = _state_decimal(state_entry.get("last_price"))
    if price is None:
        return None
    min_price, max_price = sanitized_price_bounds(state_entry, price, watch.target_price)
    product_title = str(state_entry.get("title") or watch.name or watch.url)
    if watch.site == SITE_HEPSIBURADA:
        product_title = hepsiburada_provider.clean_display_title(product_title)
    return PriceSummaryRow(
        seller=seller,
        product_title=product_title,
        product_url=str(state_entry.get("url") or state_entry.get("configured_url") or watch.url),
        price=price,
        target_price=watch.target_price,
        min_price=min_price,
        max_price=max_price,
    )


def cached_summary_rows_for_watch(
    watch: WatchRule,
    watch_key: str,
    state: Dict[str, Any],
    seller: str,
) -> List[PriceSummaryRow]:
    base_entry = state.get(watch_key, {})
    offer_keys = []
    if isinstance(base_entry, dict) and isinstance(base_entry.get("offer_keys"), list):
        offer_keys = [str(key) for key in base_entry["offer_keys"] if key]
    if not offer_keys:
        offer_keys = [watch_key]

    rows = []
    for offer_key in offer_keys:
        entry = state.get(offer_key, {})
        if not isinstance(entry, dict):
            continue
        row = summary_row_from_state(watch, entry, seller)
        if row:
            rows.append(row)
    return rows


def format_minutes(seconds: float | int | None) -> str:
    if seconds is None:
        return "-"
    total_seconds = max(0, int(round(float(seconds))))
    minutes, remaining_seconds = divmod(total_seconds, 60)
    if minutes:
        return f"{minutes} dk {remaining_seconds} sn"
    return f"{remaining_seconds} sn"


def save_price_summary(
    rows: List[PriceSummaryRow],
    stock_rows: List[StockSummaryRow] | None = None,
    cycle_duration_seconds: float | None = None,
    scan_duration_seconds: float | None = None,
) -> None:
    unique_rows = deduplicate_summary_rows(rows)
    sorted_rows = sorted_summary_rows(unique_rows)
    sorted_stock = sorted_stock_rows(stock_rows or [])
    previous_payload = load_json(SUMMARY_PATH, {})
    if not isinstance(previous_payload, dict):
        previous_payload = {}
    if cycle_duration_seconds is None:
        cycle_duration_seconds = previous_payload.get("cycle_duration_seconds")
    if scan_duration_seconds is None:
        scan_duration_seconds = previous_payload.get("scan_duration_seconds")
    payload = {
        "checked_at": format_local_datetime(local_now()),
        "row_count": len(sorted_rows),
        "stock_row_count": len(sorted_stock),
        "cycle_duration_seconds": cycle_duration_seconds,
        "cycle_duration_minutes": format_minutes(cycle_duration_seconds),
        "scan_duration_seconds": scan_duration_seconds,
        "scan_duration_minutes": format_minutes(scan_duration_seconds),
        "rows": [
            {
                "no": idx,
                "seller": row.seller,
                "product_title": row.product_title,
                "product_url": row.product_url,
                "price": format_tl(row.price),
                "target": format_tl(row.target_price),
                "difference": format_signed_tl(row.difference),
                "min_price": format_tl(row.min_price),
                "max_price": format_tl(row.max_price),
                "price_range": f"{format_tl(row.min_price)} / {format_tl(row.max_price)}",
                "is_target_hit": row.price <= row.target_price,
            }
            for idx, row in enumerate(sorted_rows, start=1)
        ],
        "stock_rows": [
            {
                "no": idx,
                "seller": row.seller,
                "product_title": row.product_title,
                "product_url": row.product_url,
                "target": format_tl(row.target_price),
                "reason": row.reason,
            }
            for idx, row in enumerate(sorted_stock, start=1)
        ],
    }
    save_json(SUMMARY_PATH, payload)


def log_price_summary(rows: List[PriceSummaryRow]) -> None:
    unique_rows = deduplicate_summary_rows(rows)
    if not unique_rows:
        log("Özet: eslesen=0")
        return
    no_width = 3
    seller_width = 12
    product_width = 40
    price_width = 10
    header = (
        f"{'No':>{no_width}} | "
        f"{log_cell('Satıcı', seller_width)} | "
        f"{log_cell('Ürün Adı', product_width)} | "
        f"{'Fiyat':>{price_width}} | "
        f"{'Hedef':>{price_width}} | "
        f"{'Fark':>{price_width}}"
    )
    separator = (
        f"{'-' * no_width}-+-"
        f"{'-' * seller_width}-+-"
        f"{'-' * product_width}-+-"
        f"{'-' * price_width}-+-"
        f"{'-' * price_width}-+-"
        f"{'-' * price_width}"
    )
    sorted_rows = sorted_summary_rows(unique_rows)
    log(f"Özet: eslesen={len(sorted_rows)}")
    log(header)
    log(separator)
    for idx, row in enumerate(sorted_rows, start=1):
        log(
            f"{idx:>{no_width}} | "
            f"{log_cell(row.seller, seller_width)} | "
            f"{log_cell(row.product_title, product_width)} | "
            f"{format_tl(row.price):>{price_width}} | "
            f"{format_tl(row.target_price):>{price_width}} | "
            f"{format_signed_tl(row.difference):>{price_width}}"
        )


def publish_price_summary(
    rows: List[PriceSummaryRow],
    stock_rows: List[StockSummaryRow] | None = None,
    cycle_duration_seconds: float | None = None,
    scan_duration_seconds: float | None = None,
) -> None:
    unique_rows = deduplicate_summary_rows(rows)
    duplicate_count = len(rows) - len(unique_rows)
    if duplicate_count:
        log(f"Özet tablodan ayni urun linki tekrarları ayıklandı: adet={duplicate_count}")
    save_price_summary(unique_rows, stock_rows, cycle_duration_seconds, scan_duration_seconds)
    log_price_summary(unique_rows)


def should_alert(
    state_entry: Dict[str, Any],
    current_price: Decimal,
    target_price: Decimal,
    repeat_after_24h: bool,
) -> bool:
    if current_price > target_price:
        return False
    last_alerted_price = state_entry.get("last_alerted_price")
    if last_alerted_price is None:
        return True
    try:
        if current_price < Decimal(str(last_alerted_price)):
            return True
    except InvalidOperation:
        return True
    if repeat_after_24h:
        last_alerted_at = parse_iso_datetime(state_entry.get("last_alerted_at"))
        if not last_alerted_at:
            return False
        elapsed = (local_now().astimezone(timezone.utc) - last_alerted_at).total_seconds()
        return elapsed >= NOTIFY_REPEAT_SECONDS
    return not state_entry.get("was_below_target", False)


def update_state_entry(
    state_entry: Dict[str, Any],
    current_price: Decimal,
    target_price: Decimal,
    alert_sent: bool,
    context: str = "",
) -> Dict[str, Any]:
    previous_min_price = _state_decimal(state_entry.get("min_price"))
    previous_max_price = _state_decimal(state_entry.get("max_price"))
    min_price, max_price = sanitized_price_bounds(state_entry, current_price, target_price, context)
    updated = dict(state_entry)
    updated["last_price"] = str(current_price)
    updated["min_price"] = str(min_price)
    updated["max_price"] = str(max_price)
    updated["last_checked_at"] = utc_now()
    if previous_min_price != min_price:
        updated["min_price_at"] = updated["last_checked_at"]
    elif not updated.get("min_price_at"):
        updated["min_price_at"] = updated["last_checked_at"]
    if previous_max_price != max_price:
        updated["max_price_at"] = updated["last_checked_at"]
    elif not updated.get("max_price_at"):
        updated["max_price_at"] = updated["last_checked_at"]
    updated["was_below_target"] = current_price <= target_price
    if alert_sent:
        updated["last_alerted_price"] = str(current_price)
        updated["last_alerted_at"] = utc_now()
    return updated


def _clear_alert_suppression(entry: Dict[str, Any], force_product_due: bool = False) -> bool:
    changed = False
    for field_name in ("last_alerted_price", "last_alerted_at"):
        if field_name in entry:
            entry.pop(field_name, None)
            changed = True
    if force_product_due and entry.get("last_checked_at"):
        entry.pop("last_checked_at", None)
        changed = True
    return changed


def reset_notification_suppression() -> int:
    state = load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    reset_count = 0
    for key, value in list(state.items()):
        if key == "_meta" or not isinstance(value, dict):
            continue
        if _clear_alert_suppression(value, force_product_due=True):
            reset_count += 1

    save_json(STATE_PATH, state)
    log(f"Bildirim susturma hafizasi sifirlandi: kayit={reset_count}")
    return reset_count


def amazon_protection_cooldown_seconds() -> int:
    return 0


def amazon_protection_state(state: Dict[str, Any]) -> Dict[str, Any]:
    meta = state.get("_meta")
    if not isinstance(meta, dict):
        meta = {}
        state["_meta"] = meta
    protection = meta.get("amazon_protection")
    if not isinstance(protection, dict):
        protection = {}
        meta["amazon_protection"] = protection
    return protection


def is_amazon_protection_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code in {429, 503}:
        return True
    message = normalize_key(str(exc))
    return any(marker in message for marker in ("captcha", "robot", "bot_korumasi", "koruma_sayfasi"))


def amazon_protection_remaining_seconds(state: Dict[str, Any], key: str) -> int:
    guard = amazon_protection_state(state).get(key)
    if not isinstance(guard, dict):
        return 0
    blocked_at = parse_iso_datetime(guard.get("blocked_at"))
    if not blocked_at:
        return 0
    elapsed = (local_now().astimezone(timezone.utc) - blocked_at).total_seconds()
    return max(0, amazon_protection_cooldown_seconds() - int(elapsed))


def note_amazon_protection(state: Dict[str, Any], key: str, source: str, exc: Exception) -> None:
    protection = amazon_protection_state(state)
    protection[key] = {
        "blocked_at": utc_now(),
        "source": source,
        "message": str(exc)[:300],
    }


def should_send_search_error_notification(state_entry: Dict[str, Any]) -> bool:
    now = local_now()
    if now.hour != AMAZON_SEARCH_ERROR_NOTIFICATION_HOUR:
        return False
    last_notified = parse_iso_datetime(state_entry.get("last_error_notified_at"))
    if not last_notified:
        return True
    return last_notified.astimezone().date() < now.date()


def wait_before_request(label: str, config: HermesConfig) -> None:
    delay = random.randint(config.request_delay_min_seconds, config.request_delay_max_seconds)
    log(f"{label} istegi oncesi {delay} saniye bekleniyor.")
    if delay > 0:
        time.sleep(delay)


def request_log_label(source: str, name: str = "", detail: str = "") -> str:
    parts = [str(source or "İstek").strip()]
    for value in (name, detail):
        text = str(value or "").strip()
        if text:
            parts.append(text[:61] + "..." if len(text) > 64 else text)
    return " | ".join(parts)


def balanced_request_order(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    site_order: List[str] = []
    for item in items:
        site = str(item.get("site") or "unknown").strip().lower() or "unknown"
        if site not in buckets:
            buckets[site] = []
            site_order.append(site)
        buckets[site].append(item)

    ordered: List[Dict[str, Any]] = []
    last_site = ""
    while any(buckets.values()):
        candidates = [site for site in site_order if buckets[site] and site != last_site]
        if not candidates:
            candidates = [site for site in site_order if buckets[site]]

        selected_site = max(candidates, key=lambda site: (len(buckets[site]), -site_order.index(site)))
        ordered.append(buckets[selected_site].pop(0))
        last_site = selected_site
    return ordered


def update_error_notification_state(state_entry: Dict[str, Any]) -> Dict[str, Any]:
    updated = dict(state_entry)
    updated["last_error_notified_at"] = utc_now()
    return updated


def should_reset_product_alert_on_error(exc: Exception) -> bool:
    normalized = normalize_offer_text(str(exc))
    return any(marker in normalized for marker in PRODUCT_MISSING_PRICE_MARKERS)


def reset_product_alert_after_missing(
    state_entry: Dict[str, Any], seller: str, product_name: str
) -> Dict[str, Any]:
    updated = dict(state_entry)
    if updated.get("last_alerted_price") is None and updated.get("last_alerted_at") is None:
        return updated
    updated.pop("last_alerted_price", None)
    updated.pop("last_alerted_at", None)
    updated["last_missing_at"] = utc_now()
    log(f"Ürün stok/fiyat kayboldu, tekrar bildirim için hazırlandı: {seller} | {product_name}")
    return updated


def summary_config_signature(config: HermesConfig) -> str:
    watch_part = ",".join(
        f"{watch.site}:{watch.url}:{watch.target_price}:{watch.size}:{watch.active}"
        for watch in config.watches
    )
    return f"watches={watch_part}"


def summary_drop_threshold(expected_count: int) -> int:
    ratio_threshold = (expected_count + SUMMARY_DROP_RATIO_DIVISOR - 1) // SUMMARY_DROP_RATIO_DIVISOR
    return max(SUMMARY_DROP_MIN_DELTA, ratio_threshold)


def summary_drop_quiet_hours(now) -> bool:
    return now.hour >= SUMMARY_DROP_QUIET_START_HOUR or now.hour < SUMMARY_DROP_QUIET_END_HOUR


def summary_drop_cooldown_passed(meta: Dict[str, Any], now) -> bool:
    last_alerted = parse_iso_datetime(meta.get("last_summary_drop_alert_at"))
    if not last_alerted:
        return True
    elapsed = (now.astimezone(timezone.utc) - last_alerted).total_seconds()
    return elapsed >= SUMMARY_DROP_ALERT_COOLDOWN_SECONDS


def amazon_empty_alert_cooldown_passed(meta: Dict[str, Any], now) -> bool:
    last_alerted = parse_iso_datetime(meta.get("last_amazon_empty_search_alert_at"))
    if not last_alerted:
        return True
    elapsed = (now.astimezone(timezone.utc) - last_alerted).total_seconds()
    return elapsed >= SUMMARY_DROP_ALERT_COOLDOWN_SECONDS


def maybe_alert_summary_drop(
    state: Dict[str, Any], rows: List[PriceSummaryRow], config: HermesConfig, session: requests.Session
) -> None:
    meta = dict(state.get("_meta", {})) if isinstance(state.get("_meta"), dict) else {}
    current_count = len(rows)
    signature = summary_config_signature(config)
    if meta.get("summary_config_signature") != signature:
        meta["summary_config_signature"] = signature
        meta["summary_expected_row_count"] = current_count
        meta["summary_last_row_count"] = current_count
        state["_meta"] = meta
        log(f"Özet takip referansı güncellendi: beklenen_urun={current_count}")
        return

    expected_count = int(meta.get("summary_expected_row_count") or current_count)
    threshold = summary_drop_threshold(expected_count)
    drop_count = expected_count - current_count
    is_unexpected_drop = expected_count > 0 and drop_count >= threshold
    now = local_now()

    if is_unexpected_drop:
        if summary_drop_quiet_hours(now):
            log(
                "Özet ürün sayısı beklenenden düşük, sessiz saat nedeniyle bildirim atlandı: "
                f"beklenen={expected_count} | bu_tur={current_count} | fark={drop_count}"
            )
        elif not summary_drop_cooldown_passed(meta, now):
            log(
                "Özet ürün sayısı beklenenden düşük, 1 saatlik sınır nedeniyle bildirim atlandı: "
                f"beklenen={expected_count} | bu_tur={current_count} | fark={drop_count}"
            )
        elif config.pushover_user_key and config.pushover_api_token:
            message = (
                "Özet tablodaki ürün sayısı beklenenden fazla düştü.\n"
                f"Beklenen ürün sayısı: {expected_count}\n"
                f"Bu tur bulunan ürün sayısı: {current_count}\n"
                f"Fark: -{drop_count}\n"
                "Config'i, özellikle Amazon arama linklerini kontrol etmeni öneririm. "
                "Amazon arama linkleri geçici olarak boş veya eksik dönmüş olabilir."
            )
            try:
                send_pushover(
                    session,
                    config.pushover_user_key,
                    config.pushover_api_token,
                    "Hermes özet uyarısı",
                    message,
                    "",
                    config.request_timeout_seconds,
                )
                meta["last_summary_drop_alert_at"] = utc_now()
                log(
                    "Özet ürün sayısı uyarısı gönderildi: "
                    f"beklenen={expected_count} | bu_tur={current_count} | fark={drop_count}"
                )
            except Exception as exc:  # noqa: BLE001
                log(f"Özet ürün sayısı uyarısı gönderilemedi: {exc}")
        else:
            log("Özet ürün sayısı uyarısı atlandı: Pushover ayarları eksik.")
    else:
        expected_count = current_count

    if current_count > expected_count:
        expected_count = current_count
    meta["summary_expected_row_count"] = expected_count
    meta["summary_last_row_count"] = current_count
    meta["summary_config_signature"] = signature
    state["_meta"] = meta


def maybe_alert_amazon_empty_searches(
    state: Dict[str, Any],
    events: List[Dict[str, Any]],
    config: HermesConfig,
    session: requests.Session,
) -> None:
    if not events:
        return

    affected_pages = sorted({str(event.get("page") or "") for event in events if event.get("page")})
    failed_link_count = sum(int(event.get("failed_links") or 0) for event in events)
    if len(affected_pages) < AMAZON_EMPTY_ALERT_MIN_PAGES and failed_link_count < AMAZON_EMPTY_ALERT_MIN_FAILED_LINKS:
        log(
            "Amazon bos arama uyarisi atlandi, esik altinda: "
            f"sayfa={len(affected_pages)} | link={failed_link_count}"
        )
        return

    meta = dict(state.get("_meta", {})) if isinstance(state.get("_meta"), dict) else {}
    now = local_now()
    if summary_drop_quiet_hours(now):
        log(
            "Amazon bos arama uyarisi sessiz saat nedeniyle atlandi: "
            f"sayfa={len(affected_pages)} | link={failed_link_count}"
        )
        state["_meta"] = meta
        return
    if not amazon_empty_alert_cooldown_passed(meta, now):
        log(
            "Amazon bos arama uyarisi 1 saatlik sinir nedeniyle atlandi: "
            f"sayfa={len(affected_pages)} | link={failed_link_count}"
        )
        state["_meta"] = meta
        return
    if not config.pushover_user_key or not config.pushover_api_token:
        log("Amazon bos arama uyarisi atlandi: Pushover ayarlari eksik.")
        state["_meta"] = meta
        return

    event_lines = []
    for event in events[:8]:
        page_name = str(event.get("page") or "-")
        failed_links = int(event.get("failed_links") or 0)
        mode = "tamamen bos" if event.get("full_empty") else "kismi bos"
        event_lines.append(f"- {page_name}: {mode}, bos link={failed_links}")
    if len(events) > 8:
        event_lines.append(f"- +{len(events) - 8} ek Amazon arama kaydi")

    message = (
        "Amazon arama sayfalarinda anlamli sayida bos donus yakalandi.\n"
        f"Etkilenen arama sayfasi: {len(affected_pages)}\n"
        f"Bos donen link sayisi: {failed_link_count}\n"
        "Okunamayan urunler bu tur ozet tablodan cikarildi; config/Amazon linklerini kontrol etmeni oneririm.\n"
        + "\n".join(event_lines)
    )
    try:
        send_pushover(
            session,
            config.pushover_user_key,
            config.pushover_api_token,
            "Hermes Amazon arama uyarısı",
            message[:900],
            "",
            config.request_timeout_seconds,
        )
        meta["last_amazon_empty_search_alert_at"] = utc_now()
        log(
            "Amazon bos arama uyarisi gonderildi: "
            f"sayfa={len(affected_pages)} | link={failed_link_count}"
        )
    except Exception as exc:  # noqa: BLE001
        log(f"Amazon bos arama uyarisi gonderilemedi: {exc}")
    state["_meta"] = meta


def offers_from_amazon_search_results(results: List[SearchResultItem], product_name: str) -> List[OfferResult]:
    matches = filter_matching_results(results, product_name) if product_name else results
    if not matches:
        raise HermesError("Amazon arama sayfasında ürün adına uyan fiyatlı ürün bulunamadı.")
    return [
        OfferResult(title=item.title, price=item.price, seller="Amazon", url=item.url)
        for item in matches
    ]


def _amazon_detail_result_cache(session: requests.Session) -> Dict[str, SearchResultItem]:
    cache = getattr(session, "_hermes_amazon_detail_result_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        setattr(session, "_hermes_amazon_detail_result_cache", cache)
    return cache


def _fetch_amazon_search_watch_offers(
    session: requests.Session,
    watch: WatchRule,
    config: HermesConfig,
) -> List[OfferResult]:
    response = fetch_amazon_page(
        session,
        watch.url,
        config.request_timeout_seconds,
        expect_search=True,
    )
    html = cleaned_html(response)
    raise_if_age_verification(html)
    if "captcha" in html.lower() and "robot" in html.lower():
        raise HermesError("Amazon bot korumasi nedeniyle captcha sayfasi dondu.")

    candidates = extract_result_candidates(html, watch.max_items_to_scan)
    target_keywords = [watch.name] if watch.name else []
    results: List[SearchResultItem] = []
    skipped_detail_count = 0
    for candidate in candidates:
        if candidate.price is not None:
            results.append(SearchResultItem(title=candidate.title, url=candidate.url, price=candidate.price))
            continue
        if target_keywords and not title_matches_any_keyword(candidate.title, target_keywords):
            skipped_detail_count += 1
            continue
        try:
            results.append(_fetch_amazon_detail_result(session, candidate, config))
        except Exception as exc:  # noqa: BLE001
            log(f"Amazon product arama detay fiyatı okunamadı: {log_cell(candidate.title, 60)} | {exc}")

    if skipped_detail_count:
        log(f"Amazon product arama detay fiyatı atlandı: eslesmeyen_urun={skipped_detail_count}")
    if not results:
        raise HermesError("Amazon arama sayfasında okunabilir fiyat bulunamadı.")
    offers = offers_from_amazon_search_results(dedupe_results(results), watch.name)
    log(
        "Amazon arama linki okundu: "
        f"{watch.name or watch.url} | eslesen_urun={len(offers)}"
    )
    return offers


def _hepsiburada_variant_scan_limit(watch: WatchRule) -> int:
    return max(1, min(int(watch.max_items_to_scan or 1), 100))


def _enrich_hepsiburada_search_offer_titles(
    session: requests.Session,
    offers: List[OfferResult],
    config: HermesConfig,
) -> List[OfferResult]:
    enriched: List[OfferResult] = []
    for offer in offers:
        if not offer.url or not hepsiburada_provider.is_product_url(offer.url):
            enriched.append(offer)
            continue
        try:
            response = fetch_hepsiburada_page(session, offer.url, config.request_timeout_seconds)
            html = cleaned_html(response)
            raise_if_age_verification(html)
            if is_bot_protection_page(SITE_HEPSIBURADA, html):
                raise HermesError("Hepsiburada bot korumasi nedeniyle captcha sayfasi dondu.")
            variant_label = (
                hepsiburada_provider.extract_selected_variant_label(html)
                or hepsiburada_provider.extract_embedded_variant_label(html, offer.url)
            )
            title = hepsiburada_provider.title_with_variant_label(offer.title, variant_label)
            title = hepsiburada_provider.clean_display_title(title)
            enriched.append(OfferResult(title=title, price=offer.price, seller=offer.seller, url=offer.url))
        except Exception as exc:  # noqa: BLE001
            log(f"Hepsiburada arama karti varyasyon etiketi tamamlanamadi: {offer.url} | {exc}")
            enriched.append(offer)
    return enriched


def _fetch_hepsiburada_watch_offers(
    session: requests.Session,
    watch: WatchRule,
    config: HermesConfig,
) -> List[OfferResult]:
    response = fetch_hepsiburada_page(session, watch.url, config.request_timeout_seconds)
    html = cleaned_html(response)
    raise_if_age_verification(html)
    if is_bot_protection_page(SITE_HEPSIBURADA, html):
        raise HermesError("Hepsiburada bot korumasi nedeniyle captcha sayfasi dondu.")

    if not hepsiburada_provider.is_product_url(watch.url):
        offers = hepsiburada_provider.extract_search_offers(
            html,
            source_url=watch.url,
            limit=_hepsiburada_variant_scan_limit(watch),
        )
        offers = _enrich_hepsiburada_search_offer_titles(session, offers, config)
        log(f"Hepsiburada arama kartlari okundu: {watch.name or watch.url} | adet={len(offers)}")
        return offers

    variant_urls = hepsiburada_provider.extract_variant_urls(
        html,
        watch.url,
        _hepsiburada_variant_scan_limit(watch),
    )
    if len(variant_urls) > 1:
        log(f"Hepsiburada varyasyonlari bulundu: {watch.name or watch.url} | adet={len(variant_urls)}")

    offers: List[OfferResult] = []
    errors: List[str] = []
    seen_offer_keys: set[tuple[str, str, str, str]] = set()

    def lower_offer(first: OfferResult | None, second: OfferResult | None) -> OfferResult:
        if first is None and second is None:
            raise HermesError("Hepsiburada sayfasından fiyat bulunamadı.")
        if first is None:
            return second
        if second is None:
            return first
        return second if second.price < first.price else first

    for variant_url in variant_urls or [watch.url]:
        try:
            variant_label = hepsiburada_provider.extract_embedded_variant_label(html, variant_url)
            embedded_offer = hepsiburada_provider.extract_embedded_variant_offer(html, variant_url)
            if variant_url == watch.url:
                variant_html = html
                visible_offer = extract_offer(SITE_HEPSIBURADA, variant_html, source_url=variant_url)
                offer = lower_offer(embedded_offer, visible_offer)
            else:
                variant_html = ""
                visible_offer = None
                if embedded_offer:
                    offer = embedded_offer
                    try:
                        variant_response = fetch_hepsiburada_page(
                            session,
                            variant_url,
                            config.request_timeout_seconds,
                        )
                        variant_html = cleaned_html(variant_response)
                        raise_if_age_verification(variant_html)
                        if is_bot_protection_page(SITE_HEPSIBURADA, variant_html):
                            raise HermesError("Hepsiburada bot korumasi nedeniyle captcha sayfasi dondu.")
                        visible_offer = extract_offer(SITE_HEPSIBURADA, variant_html, source_url=variant_url)
                        offer = lower_offer(embedded_offer, visible_offer)
                    except Exception as exc:  # noqa: BLE001
                        log(f"Hepsiburada varyasyon sayfasi premium kontrolu atlandi: {variant_url} | {exc}")
                else:
                    variant_response = fetch_hepsiburada_page(
                        session,
                        variant_url,
                        config.request_timeout_seconds,
                    )
                    variant_html = cleaned_html(variant_response)
                    raise_if_age_verification(variant_html)
                    if is_bot_protection_page(SITE_HEPSIBURADA, variant_html):
                        raise HermesError("Hepsiburada bot korumasi nedeniyle captcha sayfasi dondu.")
                    offer = extract_offer(SITE_HEPSIBURADA, variant_html, source_url=variant_url)
            if variant_html:
                variant_label = hepsiburada_provider.extract_selected_variant_label(variant_html) or variant_label
            offer_title = hepsiburada_provider.title_with_variant_label(offer.title, variant_label)
            variant_identity = normalize_offer_text(variant_label) or hepsiburada_provider.product_id_from_url(
                variant_url
            )
            dedupe_key = (
                variant_identity,
                normalize_offer_text(offer.seller or ""),
                str(offer.price),
                normalize_offer_text(offer_title),
            )
            if dedupe_key in seen_offer_keys:
                log(
                    "Hepsiburada varyasyon kopyasi atlandi: "
                    f"{variant_label or offer_title} | {offer.seller or '-'} | {offer.price} TL"
                )
                continue
            seen_offer_keys.add(dedupe_key)
            offers.append(
                OfferResult(
                    title=offer_title,
                    price=offer.price,
                    seller=offer.seller,
                    url=variant_url,
                )
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{variant_url} | {exc}")
            log(f"Hepsiburada varyasyon okunamadi: {variant_url} | {exc}")

    if offers:
        return offers
    if errors:
        raise HermesError(errors[-1])
    raise HermesError("Hepsiburada sayfasından fiyat bulunamadı.")


def _fetch_zara_watch_offers(
    session: requests.Session,
    watch: WatchRule,
    config: HermesConfig,
) -> List[OfferResult]:
    response = fetch_zara_page(session, watch.url, config.request_timeout_seconds)
    html = cleaned_html(response)
    raise_if_age_verification(html)
    if is_bot_protection_page(SITE_ZARA, html):
        raise HermesError("Zara bot korumasi nedeniyle captcha sayfasi dondu.")
    offers = zara_provider.extract_offers(html, source_url=watch.url, size=watch.size)
    if watch.size:
        log(f"Zara beden kontrol edildi: {watch.name or watch.url} | beden={watch.size} | adet={len(offers)}")
    return offers


def _fetch_hm_watch_offers(
    session: requests.Session,
    watch: WatchRule,
    config: HermesConfig,
) -> List[OfferResult]:
    response = fetch_hm_page(session, watch.url, config.request_timeout_seconds)
    html = cleaned_html(response)
    raise_if_age_verification(html)
    if is_bot_protection_page(SITE_HM, html):
        raise HermesError("H&M bot korumasi nedeniyle ürün verisi okunamadi.")
    offers = hm_provider.extract_offers(html, source_url=watch.url, size=watch.size)
    if watch.size:
        log(f"H&M beden kontrol edildi: {watch.name or watch.url} | beden={watch.size} | adet={len(offers)}")
    return offers


def _fetch_watch_offers(session: requests.Session, watch: WatchRule, config: HermesConfig) -> List[OfferResult]:
    site = watch.site
    url = watch.url
    timeout = config.request_timeout_seconds
    if site == SITE_AMAZON and is_amazon_search_url(url):
        return _fetch_amazon_search_watch_offers(session, watch, config)
    if site == SITE_HEPSIBURADA:
        return _fetch_hepsiburada_watch_offers(session, watch, config)
    if site == SITE_ZARA:
        return _fetch_zara_watch_offers(session, watch, config)
    if site == SITE_HM:
        return _fetch_hm_watch_offers(session, watch, config)
    elif site == SITE_AMAZON:
        response = fetch_amazon_page(session, url, timeout)
    else:
        response = fetch_with_retries(session, url, timeout)
    html = cleaned_html(response)
    raise_if_age_verification(html)
    if is_bot_protection_page(site, html):
        raise HermesError(f"{site_label(site)} bot korumasi nedeniyle captcha sayfasi dondu.")
    return [extract_offer(site, html, source_url=url)]


def _fetch_amazon_detail_result(session: requests.Session, candidate, config: HermesConfig) -> SearchResultItem:
    cache = _amazon_detail_result_cache(session)
    cache_key = str(candidate.url or "").strip()
    if cache_key in cache:
        return cache[cache_key]

    wait_before_request(request_log_label("Amazon detay", candidate.title), config)
    response = fetch_amazon_page(session, candidate.url, config.request_timeout_seconds)
    html = cleaned_html(response)
    raise_if_age_verification(html)
    if "captcha" in html.lower() and "robot" in html.lower():
        raise HermesError("Amazon bot korumasi nedeniyle captcha sayfasi dondu.")
    offer = extract_offer(SITE_AMAZON, html)
    title = offer.title or candidate.title
    url = offer.url or candidate.url
    log(
        "Amazon arama fiyatı ürün detayından tamamlandı: "
        f"{log_cell(title, 60)} | fiyat={offer.price} TL"
    )
    result = SearchResultItem(title=title, url=url, price=offer.price)
    if cache_key:
        cache[cache_key] = result
    return result


def check_once(config: HermesConfig) -> None:
    cycle_started_at = time.monotonic()
    state = load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    session = requests.Session()
    summary_rows: List[PriceSummaryRow] = []
    stock_rows: List[StockSummaryRow] = []
    amazon_empty_events: List[Dict[str, Any]] = []
    request_tasks: List[Dict[str, Any]] = []

    def check_watch(watch: WatchRule, watch_key: str, state_entry: Dict[str, Any], seller: str) -> None:
        is_amazon_search_watch = watch.site == SITE_AMAZON and is_amazon_search_url(watch.url)
        if watch.site == SITE_AMAZON:
            remaining = amazon_protection_remaining_seconds(state, watch_key)
            if remaining > 0:
                summary_rows.extend(cached_summary_rows_for_watch(watch, watch_key, state, seller))
                minutes = max(1, round(remaining / 60))
                log(
                    f"Amazon linki gecici koruma nedeniyle atlandi: "
                    f"{watch.name or watch.url} | kalan={minutes} dk"
                )
                return
        try:
            display_name = watch.name or watch.url
            wait_before_request(request_log_label(seller, display_name), config)
            offers = _fetch_watch_offers(session, watch, config)
            offer_keys: List[str] = []
            for offer in offers:
                offer_display_name = offer.title or watch.name or watch.url
                if watch.site == SITE_HEPSIBURADA:
                    offer_display_name = hepsiburada_provider.clean_display_title(offer_display_name)
                matched_url = offer.url or watch.url
                offer_key = normalize_item_key("watch_offer", watch.site, watch.name, matched_url, watch.size)
                offer_state_entry = state.get(offer_key, {})
                if not isinstance(offer_state_entry, dict):
                    offer_state_entry = {}
                offer_keys.append(offer_key)

                min_price, max_price = sanitized_price_bounds(
                    offer_state_entry,
                    offer.price,
                    watch.target_price,
                    f"{seller} | {offer_display_name}",
                )
                summary_rows.append(
                    PriceSummaryRow(
                        seller=seller,
                        product_title=offer_display_name,
                        product_url=matched_url,
                        price=offer.price,
                        target_price=watch.target_price,
                        min_price=min_price,
                        max_price=max_price,
                    )
                )
                log(
                    f"Kontrol edildi: {seller} | {offer_display_name} | fiyat={offer.price} TL | "
                    f"hedef={watch.target_price} TL"
                )

                alert_sent = False
                if should_alert(offer_state_entry, offer.price, watch.target_price, watch.notify_once_in_24h):
                    seller_note = f" ({offer.seller})" if offer.seller and watch.site == SITE_HEPSIBURADA else ""
                    message = (
                        f"Site: {seller}\n"
                        f"{offer_display_name}\n"
                        f"Guncel fiyat: {offer.price} TL{seller_note}\n"
                        f"Hedef fiyat: {watch.target_price} TL"
                    )
                    send_pushover(
                        session,
                        config.pushover_user_key,
                        config.pushover_api_token,
                        f"{seller} fiyat alarmi",
                        message,
                        matched_url,
                        config.request_timeout_seconds,
                    )
                    alert_sent = True
                    log(f"Bildirim gonderildi: {seller} | {offer_display_name}")
                    save_price_summary(summary_rows, stock_rows)
                elif offer.price <= watch.target_price and watch.notify_once_in_24h:
                    log(
                        f"Bildirim atlandi, 24 saat dolmadi veya fiyat daha dusuk degil: "
                        f"{seller} | {matched_url}"
                    )

                state[offer_key] = update_state_entry(
                    offer_state_entry,
                    offer.price,
                    watch.target_price,
                    alert_sent,
                    f"{seller} | {offer_display_name}",
                )
                state[offer_key]["title"] = offer_display_name
                state[offer_key]["url"] = matched_url
                state[offer_key]["configured_url"] = watch.url
                state[offer_key]["watch_name"] = watch.name
                state[offer_key]["size"] = watch.size
                state[offer_key]["site"] = watch.site
                state[offer_key]["last_error"] = None
                state[offer_key]["last_error_status"] = None

            state[watch_key] = {
                **dict(state_entry),
                "site": watch.site,
                "watch_name": watch.name,
                "configured_url": watch.url,
                "size": watch.size,
                "offer_keys": offer_keys,
                "last_error": None,
                "last_error_status": None,
                "last_checked_at": utc_now(),
            }
        except OutOfStockHermesError as exc:
            stock_title = getattr(exc, "product_title", "") or watch.name or watch.url
            stock_url = getattr(exc, "product_url", "") or watch.url
            log(f"Stokta yok: {seller} | {stock_title} | {exc}")
            stock_rows.append(
                StockSummaryRow(
                    seller=seller,
                    product_title=stock_title,
                    product_url=stock_url,
                    target_price=watch.target_price,
                    reason=str(exc),
                )
            )
            failed = reset_product_alert_after_missing(dict(state_entry), seller, stock_title)
            failed["site"] = watch.site
            failed["watch_name"] = watch.name
            failed["configured_url"] = watch.url
            failed["size"] = watch.size
            failed["offer_keys"] = []
            failed["last_error"] = None
            failed["last_error_status"] = None
            failed["last_checked_at"] = utc_now()
            failed["last_out_of_stock_at"] = utc_now()
            state[watch_key] = failed
        except Exception as exc:  # noqa: BLE001
            log(f"Hata: {seller} | {watch.url} | {exc}")
            if watch.site == SITE_AMAZON and is_amazon_protection_error(exc):
                note_amazon_protection(state, watch_key, watch.name or watch.url, exc)
            if is_amazon_search_watch:
                amazon_empty_events.append({"page": watch.name, "failed_links": 1, "full_empty": True})
            failed = dict(state_entry)
            if should_reset_product_alert_on_error(exc):
                failed = reset_product_alert_after_missing(failed, seller, watch.name or watch.url)
            if is_amazon_search_watch and should_send_search_error_notification(failed):
                try:
                    message = (
                        f"Amazon arama: {watch.name}\n"
                        f"Aranan keyword: {watch.name}\n"
                        f"Hata: {exc}\n"
                        f"Link: {watch.url}\n"
                        "Kontrol etmen gerekebilir: link geçersiz olabilir, Amazon koruması olabilir veya sayfa yapısı değişmiş olabilir."
                    )
                    send_pushover(
                        session,
                        config.pushover_user_key,
                        config.pushover_api_token,
                        "Amazon arama hatasi",
                        message[:900],
                        watch.url,
                        config.request_timeout_seconds,
                    )
                    failed = update_error_notification_state(failed)
                    log(f"Amazon arama hata bildirimi gonderildi: {watch.name}")
                except Exception as notify_exc:  # noqa: BLE001
                    log(f"Amazon arama hata bildirimi gonderilemedi: {watch.name} | {notify_exc}")
            failed["site"] = watch.site
            failed["watch_name"] = watch.name
            failed["configured_url"] = watch.url
            failed["size"] = watch.size
            failed["offer_keys"] = []
            failed["last_error"] = str(exc)
            failed["last_error_status"] = getattr(exc, "status_code", None)
            failed["last_checked_at"] = utc_now()
            state[watch_key] = failed

    for watch in config.watches:
        watch_key = normalize_item_key("watch", watch.site, watch.name, watch.url, watch.size)
        state_entry = state.get(watch_key, {})
        if not isinstance(state_entry, dict):
            state_entry = {}
        seller = site_label(watch.site)
        if not watch_check_due(watch, state_entry, config.interval_seconds):
            summary_rows.extend(cached_summary_rows_for_watch(watch, watch_key, state, seller))
            continue

        request_tasks.append(
            {
                "site": watch.site,
                "name": watch.name or watch.url,
                "run": lambda watch=watch, watch_key=watch_key, state_entry=state_entry, seller=seller: check_watch(
                    watch, watch_key, state_entry, seller
                ),
            }
        )

    for task in balanced_request_order(request_tasks):
        task["run"]()

    if config.watches:
        scan_duration_seconds = time.monotonic() - cycle_started_at
        cycle_duration_seconds = scan_duration_seconds + config.interval_seconds
        summary_rows = deduplicate_summary_rows(summary_rows)
        publish_price_summary(summary_rows, stock_rows, cycle_duration_seconds, scan_duration_seconds)
        maybe_alert_summary_drop(state, summary_rows, config, session)
        maybe_alert_amazon_empty_searches(state, amazon_empty_events, config, session)
    save_json(STATE_PATH, state)


def log_cycle_banner(config: HermesConfig) -> None:
    line = "=" * 92
    log(line)
    log(
        f">>> HERMES v{APP_VERSION} | YENI KONTROL TURU | "
        f"Kontrol araligi: {config.interval_seconds} saniye <<<"
    )
    log(line)


def run_service() -> int:
    try:
        config = load_config()
    except Exception as exc:  # noqa: BLE001
        log(f"Baslatma hatasi: {exc}")
        return 1

    run_once = os.getenv("RUN_ONCE", "").strip() == "1"
    if run_once:
        log_cycle_banner(config)
        check_once(config)
        return 0

    start_telegram_listener(config)
    log(f"Servis basladi. Hermes v{APP_VERSION} | Kontrol araligi: {config.interval_seconds} saniye")
    while True:
        log_cycle_banner(config)
        check_once(config)
        next_check = local_now() + timedelta(seconds=config.interval_seconds)
        log(f"Sonraki kontrol: {format_local_datetime(next_check)}")
        time.sleep(config.interval_seconds)
