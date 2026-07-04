import os
import random
import time
from datetime import timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlparse

import requests

from .config_loader import load_config
from .constants import (
    AMAZON_SEARCH_ERROR_NOTIFICATION_HOUR,
    AMAZON_SEARCH_HTTP_COOLDOWN_SECONDS,
    NOTIFY_REPEAT_SECONDS,
    SITE_AMAZON,
    SITE_HEPSIBURADA,
    SITE_NORDBRON,
    STATE_PATH,
    SUMMARY_PATH,
    TELEGRAM_SEEN_DEALS_PATH,
)
from .errors import HermesError
from .http_client import cleaned_html, fetch_amazon_page, fetch_hepsiburada_page, fetch_with_retries
from .logging_utils import log
from .models import AmazonSearchPage, HermesConfig, OfferResult, PriceSummaryRow, ProductRule, SearchResultItem
from .notifier import send_pushover
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
AMAZON_PRODUCT_SEARCH_MAX_ITEMS = 24


def raise_if_age_verification(html: str) -> None:
    normalized = normalize_offer_text(html)
    if any(marker in normalized for marker in AGE_VERIFICATION_MARKERS):
        raise HermesError("Yaş doğrulaması gerekiyor. Bu sayfa otomatik takip edilemiyor.")


def is_bot_protection_page(site: str, html: str) -> bool:
    lowered = html.lower()
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


def product_check_due(product: ProductRule, state_entry: Dict[str, Any], global_interval_seconds: int) -> bool:
    interval_seconds = product.check_interval_minutes * 60 if product.check_interval_minutes else global_interval_seconds
    last_checked = parse_iso_datetime(state_entry.get("last_checked_at"))
    if not last_checked:
        return True
    elapsed_seconds = (local_now().astimezone(timezone.utc) - last_checked).total_seconds()
    return elapsed_seconds >= interval_seconds


def summary_row_from_state(product: ProductRule, state_entry: Dict[str, Any], seller: str):
    price = _state_decimal(state_entry.get("last_price"))
    if price is None:
        return None
    min_price, max_price = sanitized_price_bounds(state_entry, price, product.target_price)
    return PriceSummaryRow(
        seller=seller,
        product_title=str(state_entry.get("title") or product.name or product.url),
        product_url=str(state_entry.get("url") or state_entry.get("configured_url") or product.url),
        price=price,
        target_price=product.target_price,
        min_price=min_price,
        max_price=max_price,
    )


def format_minutes(seconds: float | int | None) -> str:
    if seconds is None:
        return "-"
    minutes = max(0, float(seconds)) / 60
    if minutes < 10:
        return f"{minutes:.1f} dk"
    return f"{minutes:.0f} dk"


def save_price_summary(
    rows: List[PriceSummaryRow],
    cycle_duration_seconds: float | None = None,
    scan_duration_seconds: float | None = None,
) -> None:
    sorted_rows = sorted_summary_rows(rows)
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
    }
    save_json(SUMMARY_PATH, payload)


def log_price_summary(rows: List[PriceSummaryRow]) -> None:
    if not rows:
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
    sorted_rows = sorted_summary_rows(rows)
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
    cycle_duration_seconds: float | None = None,
    scan_duration_seconds: float | None = None,
) -> None:
    save_price_summary(rows, cycle_duration_seconds, scan_duration_seconds)
    log_price_summary(rows)


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
        if isinstance(value.get("targets"), dict):
            for target_state in value["targets"].values():
                if not isinstance(target_state, dict) or not isinstance(target_state.get("items"), dict):
                    continue
                for item_state in target_state["items"].values():
                    if isinstance(item_state, dict) and _clear_alert_suppression(item_state):
                        reset_count += 1
            continue
        if _clear_alert_suppression(value, force_product_due=True):
            reset_count += 1

    seen_deals = load_json(TELEGRAM_SEEN_DEALS_PATH, {})
    if isinstance(seen_deals, dict) and seen_deals:
        save_json(TELEGRAM_SEEN_DEALS_PATH, {})
        reset_count += len(seen_deals)

    save_json(STATE_PATH, state)
    log(f"Bildirim susturma hafizasi sifirlandi: kayit={reset_count}")
    return reset_count


def cooldown_remaining_seconds(search_state: Dict[str, Any]) -> int:
    status = search_state.get("last_error_status")
    if status not in {429, 503}:
        return 0
    last_checked = parse_iso_datetime(search_state.get("last_checked_at"))
    if not last_checked:
        return 0
    elapsed = (local_now().astimezone(timezone.utc) - last_checked).total_seconds()
    remaining = AMAZON_SEARCH_HTTP_COOLDOWN_SECONDS - int(elapsed)
    return max(0, remaining)


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


def reset_missing_alerts(
    updated_items_state: Dict[str, Any], seen_item_keys: set, page_name: str, target_name: str
) -> None:
    missing_reset_count = 0
    for missing_item_key, missing_item_state in list(updated_items_state.items()):
        if missing_item_key in seen_item_keys or not isinstance(missing_item_state, dict):
            continue
        if (
            missing_item_state.get("last_alerted_price") is not None
            or missing_item_state.get("last_alerted_at") is not None
        ):
            changed = dict(missing_item_state)
            changed.pop("last_alerted_price", None)
            changed.pop("last_alerted_at", None)
            changed["last_missing_at"] = utc_now()
            updated_items_state[missing_item_key] = changed
            missing_reset_count += 1
    if missing_reset_count:
        log(
            f"Amazon aramada kaybolan urunler tekrar bildirim icin hazirlandi: "
            f"{page_name} / {target_name} | adet={missing_reset_count}"
        )


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
    product_part = ",".join(
        f"{product.site}:{product.url}:{product.target_price}:{product.active}"
        for product in config.products
    )
    search_part = ",".join(
        f"{page.name}:{len(page.search_urls)}:"
        + ",".join(f"{target.name}:{target.target_price}:{target.active}" for target in page.targets)
        for page in config.amazon_search_pages
    )
    return f"products={product_part}|search={search_part}"


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
                "Amazon arama sayfaları geçici olarak boş veya eksik dönmüş olabilir."
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


def is_amazon_search_url(url: str) -> bool:
    parsed = urlparse(str(url or ""))
    host = parsed.netloc.casefold()
    if "amazon." not in host:
        return False
    if parsed.path.rstrip("/") == "/s":
        return True
    query = parse_qs(parsed.query)
    return "k" in query and not any(part in parsed.path for part in ("/dp/", "/gp/product/"))


def best_offer_from_amazon_search_results(results: List[SearchResultItem], product_name: str) -> OfferResult:
    matches = filter_matching_results(results, product_name) if product_name else results
    if not matches:
        raise HermesError("Amazon arama sayfasında ürün adına uyan fiyatlı ürün bulunamadı.")
    best = min(matches, key=lambda item: item.price)
    return OfferResult(title=best.title, price=best.price, seller="Amazon", url=best.url)


def _fetch_amazon_search_product_offer(
    session: requests.Session,
    product: ProductRule,
    config: HermesConfig,
) -> OfferResult:
    response = fetch_amazon_page(
        session,
        product.url,
        config.request_timeout_seconds,
        expect_search=True,
    )
    html = cleaned_html(response)
    raise_if_age_verification(html)
    if "captcha" in html.lower() and "robot" in html.lower():
        raise HermesError("Amazon bot korumasi nedeniyle captcha sayfasi dondu.")

    candidates = extract_result_candidates(html, AMAZON_PRODUCT_SEARCH_MAX_ITEMS)
    target_keywords = [product.name] if product.name else []
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
    offer = best_offer_from_amazon_search_results(dedupe_results(results), product.name)
    log(
        "Amazon product arama linki okundu: "
        f"{product.name or product.url} | eslesen_fiyat={offer.price} TL"
    )
    return offer


def _fetch_product_offer(session: requests.Session, product: ProductRule, config: HermesConfig):
    site = product.site
    url = product.url
    timeout = config.request_timeout_seconds
    if site == SITE_AMAZON and is_amazon_search_url(url):
        return _fetch_amazon_search_product_offer(session, product, config)
    if site == SITE_HEPSIBURADA:
        response = fetch_hepsiburada_page(session, url, timeout)
    elif site == SITE_AMAZON:
        response = fetch_amazon_page(session, url, timeout)
    else:
        response = fetch_with_retries(session, url, timeout)
    html = cleaned_html(response)
    raise_if_age_verification(html)
    if is_bot_protection_page(site, html):
        raise HermesError(f"{site_label(site)} bot korumasi nedeniyle captcha sayfasi dondu.")
    return extract_offer(site, html, source_url=url)


def _fetch_amazon_detail_result(session: requests.Session, candidate, config: HermesConfig) -> SearchResultItem:
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
    return SearchResultItem(title=title, url=url, price=offer.price)


def _fetch_amazon_search_results(
    session: requests.Session,
    search_url: str,
    config: HermesConfig,
    max_items_to_scan: int,
    target_keywords: List[str],
    label: str = "Arama",
):
    wait_before_request(label, config)
    response = fetch_amazon_page(session, search_url, config.request_timeout_seconds, expect_search=True)
    html = cleaned_html(response)
    raise_if_age_verification(html)
    if "captcha" in html.lower() and "robot" in html.lower():
        raise HermesError("Amazon bot korumasi nedeniyle captcha sayfasi dondu.")

    candidates = extract_result_candidates(html, max_items_to_scan)
    results: List[SearchResultItem] = []
    skipped_detail_count = 0
    for candidate in candidates:
        if candidate.price is not None:
            results.append(SearchResultItem(title=candidate.title, url=candidate.url, price=candidate.price))
            continue
        if not title_matches_any_keyword(candidate.title, target_keywords):
            skipped_detail_count += 1
            continue
        try:
            results.append(_fetch_amazon_detail_result(session, candidate, config))
        except Exception as exc:  # noqa: BLE001
            log(f"Amazon arama detay fiyatı okunamadı: {log_cell(candidate.title, 60)} | {exc}")

    if skipped_detail_count:
        log(f"Amazon detay fiyatı atlandı: eslesmeyen_urun={skipped_detail_count}")
    if not results:
        raise HermesError("Amazon arama sonuçlarında veya ürün detaylarında okunabilir fiyat bulunamadı.")
    return results


def check_once(config: HermesConfig) -> None:
    cycle_started_at = time.monotonic()
    state = load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    session = requests.Session()
    summary_rows: List[PriceSummaryRow] = []
    amazon_empty_events: List[Dict[str, Any]] = []
    request_tasks: List[Dict[str, Any]] = []

    def check_product(product: ProductRule, product_key: str, state_entry: Dict[str, Any], seller: str) -> None:
        try:
            display_name = product.name or product.url
            wait_before_request(request_log_label(seller, display_name), config)
            offer = _fetch_product_offer(session, product, config)
            display_name = product.name or offer.title or product.url
            matched_url = offer.url or product.url
            min_price, max_price = sanitized_price_bounds(
                state_entry,
                offer.price,
                product.target_price,
                f"{seller} | {display_name}",
            )
            summary_rows.append(
                PriceSummaryRow(
                    seller=seller,
                    product_title=offer.title or display_name,
                    product_url=matched_url,
                    price=offer.price,
                    target_price=product.target_price,
                    min_price=min_price,
                    max_price=max_price,
                )
            )
            log(
                f"Kontrol edildi: {seller} | {display_name} | fiyat={offer.price} TL | "
                f"hedef={product.target_price} TL"
            )

            alert_sent = False
            if should_alert(state_entry, offer.price, product.target_price, product.notify_once_in_24h):
                seller_note = f" ({offer.seller})" if offer.seller and product.site == SITE_HEPSIBURADA else ""
                message = (
                    f"Site: {seller}\n"
                    f"{display_name}\n"
                    f"Guncel fiyat: {offer.price} TL{seller_note}\n"
                    f"Hedef fiyat: {product.target_price} TL"
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
                log(f"Bildirim gonderildi: {seller} | {display_name}")
                save_price_summary(summary_rows)
            elif offer.price <= product.target_price and product.notify_once_in_24h:
                log(
                    f"Bildirim atlandi, 24 saat dolmadi veya fiyat daha dusuk degil: "
                    f"{seller} | {matched_url}"
                )

            state[product_key] = update_state_entry(
                state_entry,
                offer.price,
                product.target_price,
                alert_sent,
                f"{seller} | {display_name}",
            )
            state[product_key]["title"] = offer.title
            state[product_key]["url"] = matched_url
            state[product_key]["configured_url"] = product.url
            state[product_key]["site"] = product.site
            state[product_key]["last_error"] = None
            state[product_key]["last_error_status"] = None
        except Exception as exc:  # noqa: BLE001
            log(f"Hata: {seller} | {product.url} | {exc}")
            failed = dict(state_entry)
            if should_reset_product_alert_on_error(exc):
                failed = reset_product_alert_after_missing(failed, seller, product.name or product.url)
            failed["site"] = product.site
            failed["last_error"] = str(exc)
            failed["last_error_status"] = getattr(exc, "status_code", None)
            failed["last_checked_at"] = utc_now()
            state[product_key] = failed

    def check_amazon_search_page(
        page: AmazonSearchPage, page_key: str, page_state: Dict[str, Any]
    ) -> None:
        try:
            all_results = []
            failed_urls = []
            target_keywords = [target.product_name for target in page.targets]
            for idx, search_url in enumerate(page.search_urls, start=1):
                try:
                    url_results = _fetch_amazon_search_results(
                        session,
                        search_url,
                        config,
                        page.max_items_to_scan,
                        target_keywords,
                        request_log_label("Amazon arama", page.name, f"link {idx}/{len(page.search_urls)}"),
                    )
                    all_results.extend(url_results)
                    log(
                        f"Arama linki kontrol edildi: {page.name} | "
                        f"link={idx}/{len(page.search_urls)} | okunan_urun={len(url_results)}"
                    )
                except Exception as exc:  # noqa: BLE001
                    failed_urls.append(f"{search_url} | {exc}")
                    log(f"Arama linki hatasi: {page.name} | link={idx}/{len(page.search_urls)} | {exc}")

            if not all_results:
                amazon_empty_events.append(
                    {
                        "page": page.name,
                        "failed_links": len(failed_urls) or len(page.search_urls),
                        "full_empty": True,
                    }
                )
                raise HermesError("Amazon arama sayfasindaki linklerin hicbirinde okunabilir urun bulunamadi.")

            results = dedupe_results(all_results)
            targets_state = dict(page_state.get("targets", {}))

            if not results:
                amazon_empty_events.append(
                    {
                        "page": page.name,
                        "failed_links": len(failed_urls) or len(page.search_urls),
                        "full_empty": True,
                    }
                )
                raise HermesError("Amazon arama sayfasindaki linklerin hicbirinde okunabilir urun bulunamadi.")

            if failed_urls:
                amazon_empty_events.append(
                    {
                        "page": page.name,
                        "failed_links": len(failed_urls),
                        "full_empty": False,
                    }
                )

            log(
                f"Arama sayfasi kontrol edildi: {page.name} | okunan_urun={len(results)} | "
                f"link_sayisi={len(page.search_urls)} | hedef_sayisi={len(page.targets)}"
            )

            for target in page.targets:
                target_key = normalize_key(target.name)
                target_state = targets_state.get(target_key, {})
                items_state = target_state.get("items", {})
                updated_items_state = dict(items_state)
                matches = filter_matching_results(results, target.product_name)
                seen_item_keys = set()

                log(
                    f"Arama hedefi kontrol edildi: {page.name} / {target.name} | "
                    f"eslesen_urun={len(matches)} | hedef={target.target_price} TL"
                )

                for match in matches:
                    item_key = normalize_key(match.url)
                    seen_item_keys.add(item_key)
                    item_state = dict(items_state.get(item_key, {}))
                    min_price, max_price = sanitized_price_bounds(
                        item_state,
                        match.price,
                        target.target_price,
                        f"Amazon arama | {page.name} | {match.title}",
                    )
                    summary_rows.append(
                        PriceSummaryRow(
                            seller="Amazon",
                            product_title=match.title,
                            product_url=match.url,
                            price=match.price,
                            target_price=target.target_price,
                            min_price=min_price,
                            max_price=max_price,
                        )
                    )
                    alert_sent = False

                    if should_alert(
                        item_state,
                        match.price,
                        target.target_price,
                        target.notify_once_in_24h,
                    ):
                        message = (
                            f"Amazon arama: {page.name}\n"
                            f"Hedef: {target.name}\n"
                            f"Eslesen urun: {match.title}\n"
                            f"Guncel fiyat: {match.price} TL\n"
                            f"Hedef fiyat: {target.target_price} TL"
                        )
                        send_pushover(
                            session,
                            config.pushover_user_key,
                            config.pushover_api_token,
                            "Amazon arama alarmi",
                            message,
                            match.url,
                            config.request_timeout_seconds,
                        )
                        alert_sent = True
                        log(f"Arama bildirimi gonderildi: {target.name} | {match.title}")
                        save_price_summary(summary_rows)
                    elif match.price <= target.target_price and target.notify_once_in_24h:
                        log(
                            "Arama bildirimi atlandi, 24 saat dolmadi veya fiyat daha dusuk degil: "
                            f"{match.title} | fiyat={match.price} TL"
                        )

                    updated_items_state[item_key] = update_state_entry(
                        item_state,
                        match.price,
                        target.target_price,
                        alert_sent,
                        f"Amazon arama | {page.name} | {match.title}",
                    )
                    updated_items_state[item_key]["title"] = match.title
                    updated_items_state[item_key]["url"] = match.url
                    updated_items_state[item_key]["last_error"] = None

                if not failed_urls:
                    reset_missing_alerts(updated_items_state, seen_item_keys, page.name, target.name)

                targets_state[target_key] = {
                    "items": updated_items_state,
                    "last_match_count": len(matches),
                    "last_checked_at": utc_now(),
                }

            state[page_key] = {
                "targets": targets_state,
                "last_checked_at": utc_now(),
                "last_error": None if not failed_urls else "; ".join(failed_urls)[:900],
                "last_error_status": None,
                "last_error_notified_at": page_state.get("last_error_notified_at"),
            }
        except Exception as exc:  # noqa: BLE001
            error_message = str(exc)
            error_status = getattr(exc, "status_code", None)
            log(f"Hata: {' | '.join(page.search_urls)} | {error_message}")
            updated_page_state = dict(page_state)
            if should_send_search_error_notification(updated_page_state):
                try:
                    target_names = ", ".join(target.name for target in page.targets)
                    message = (
                        f"Amazon arama: {page.name}\n"
                        f"Hedefler: {target_names}\n"
                        f"Hata: {error_message}\n"
                        "Kontrol etmen gerekebilir: link gecersiz olabilir, Amazon korumasi olabilir veya sayfa yapisi degismis olabilir."
                    )
                    send_pushover(
                        session,
                        config.pushover_user_key,
                        config.pushover_api_token,
                        "Amazon arama hatasi",
                        message[:900],
                        page.search_urls[0],
                        config.request_timeout_seconds,
                    )
                    updated_page_state = update_error_notification_state(updated_page_state)
                    log(f"Amazon arama hata bildirimi gonderildi: {page.name}")
                except Exception as notify_exc:  # noqa: BLE001
                    log(f"Amazon arama hata bildirimi gonderilemedi: {page.name} | {notify_exc}")

            updated_page_state["last_error"] = error_message
            updated_page_state["last_error_status"] = error_status
            updated_page_state["last_checked_at"] = utc_now()
            state[page_key] = updated_page_state

    for product in config.products:
        product_key = normalize_item_key("product", product.site, product.url)
        state_entry = state.get(product_key, {})
        if not isinstance(state_entry, dict):
            state_entry = {}
        seller = site_label(product.site)
        if not product_check_due(product, state_entry, config.interval_seconds):
            cached_row = summary_row_from_state(product, state_entry, seller)
            if cached_row:
                summary_rows.append(cached_row)
            continue

        request_tasks.append(
            {
                "site": product.site,
                "name": product.name or product.url,
                "run": lambda product=product, product_key=product_key, state_entry=state_entry, seller=seller: check_product(
                    product, product_key, state_entry, seller
                ),
            }
        )

    for page in config.amazon_search_pages:
        if not page.targets:
            log(f"Amazon arama atlandi: {page.name} | Bu arama sayfasina hedef urun eklenmemis.")
            continue
        page_key = normalize_item_key("amazon_search", page.name, *page.search_urls)
        page_state = state.get(page_key, {})
        if not isinstance(page_state, dict):
            page_state = {}
        remaining = cooldown_remaining_seconds(page_state)
        if remaining > 0:
            minutes = max(1, round(remaining / 60))
            log(
                f"Amazon arama gecici olarak atlandi: {page.name} | "
                f"Amazon korumasi sonrasi {minutes} dk sonra yeniden denenecek."
            )
            skipped = dict(page_state)
            skipped["last_skipped_at"] = utc_now()
            state[page_key] = skipped
            continue

        request_tasks.append(
            {
                "site": SITE_AMAZON,
                "name": page.name,
                "run": lambda page=page, page_key=page_key, page_state=page_state: check_amazon_search_page(
                    page, page_key, page_state
                ),
            }
        )

    for task in balanced_request_order(request_tasks):
        task["run"]()

    if config.products or config.amazon_search_pages:
        scan_duration_seconds = time.monotonic() - cycle_started_at
        cycle_duration_seconds = scan_duration_seconds + config.interval_seconds
        publish_price_summary(summary_rows, cycle_duration_seconds, scan_duration_seconds)
        maybe_alert_summary_drop(state, summary_rows, config, session)
        maybe_alert_amazon_empty_searches(state, amazon_empty_events, config, session)
    save_json(STATE_PATH, state)


def run_service() -> int:
    try:
        config = load_config()
    except Exception as exc:  # noqa: BLE001
        log(f"Baslatma hatasi: {exc}")
        return 1

    run_once = os.getenv("RUN_ONCE", "").strip() == "1"
    if run_once:
        check_once(config)
        return 0

    start_telegram_listener(config)
    log(f"Servis basladi. Kontrol araligi: {config.interval_seconds} saniye")
    while True:
        check_once(config)
        next_check = local_now() + timedelta(seconds=config.interval_seconds)
        log(f"Sonraki kontrol: {format_local_datetime(next_check)}")
        time.sleep(config.interval_seconds)
