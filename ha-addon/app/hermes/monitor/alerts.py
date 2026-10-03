"""Warnings about Hermes itself: missing results and repeated access errors.

Opportunity notifications live in the cycle. These warnings are deliberately
slow: they need repetition, respect quiet hours and a one-hour cooldown, and
never reveal a CAPTCHA/HTTP 503 failure that is meant to stay silent.
"""

from datetime import timezone
from typing import Any, Dict, List

from ..constants import SEARCH_ERROR_NOTIFICATION_HOUR
from ..errors import HermesError, error_status
from ..logging_utils import log
from ..models import HermesConfig, PriceSummaryRow
from ..notifier import Pushover
from ..utils import local_now, normalize_offer_text, parse_iso_datetime, utc_now
from .state import guard_store, meta, watch_key

# Search pages can fluctuate by a few products between normal cycles. Alert
# only when both the absolute loss and the relative loss are meaningful.
SUMMARY_DROP_MIN_DELTA = 6
SUMMARY_DROP_RATIO_DIVISOR = 3
SUMMARY_DROP_CONSECUTIVE_CYCLES = 5
ALERT_COOLDOWN_SECONDS = 60 * 60
QUIET_START_HOUR = 22
QUIET_END_HOUR = 8
SEARCH_FAILURE_ALERT_MIN_PAGES = 4
SEARCH_FAILURE_ALERT_MIN_FAILED_LINKS = 6
PRODUCT_MISSING_PRICE_MARKERS = (
    "fiyat bulunamadi",
    "okunabilir fiyat bulunamadi",
    "fiyat yakalanamadi",
    "stokta degil",
)
SILENT_GUARD_KINDS = {"captcha", "http_503"}


def is_silent_access_error(exc: BaseException) -> bool:
    """CAPTCHA and HTTP 503 stay visible as errors but never notify the user."""
    message = normalize_offer_text(str(exc))
    return error_status(exc) == 503 or "captcha" in message or "robot check" in message


def missing_price_resets_alert(exc: BaseException) -> bool:
    """A page that lost its price lets the product notify again once it returns."""
    normalized = normalize_offer_text(str(exc))
    return any(marker in normalized for marker in PRODUCT_MISSING_PRICE_MARKERS)


def search_error_notification_due(state_entry: Dict[str, Any]) -> bool:
    """At most one notification per search page per day, at a fixed hour."""
    now = local_now()
    if now.hour != SEARCH_ERROR_NOTIFICATION_HOUR:
        return False
    last_notified = parse_iso_datetime(state_entry.get("last_error_notified_at"))
    return not last_notified or last_notified.astimezone().date() < now.date()


def has_silent_access_failure(state: Dict[str, Any], config: HermesConfig) -> bool:
    """Also cover deferred watches and partial results with blocked siblings."""
    guards = guard_store(state)
    for watch in config.watches:
        if not watch.active:
            continue
        key = watch_key(watch)
        entry = state.get(key, {})
        if isinstance(entry, dict) and entry.get("last_error"):
            if entry.get("last_error_status") == 503 or is_silent_access_error(HermesError(str(entry["last_error"]))):
                return True
        guard = guards.get(key, {})
        if isinstance(guard, dict) and guard.get("kind") in SILENT_GUARD_KINDS:
            return True
    return False


def quiet_hours(now) -> bool:
    return now.hour >= QUIET_START_HOUR or now.hour < QUIET_END_HOUR


def _cooldown_passed(meta_values: Dict[str, Any], field_name: str, now) -> bool:
    last_alerted = parse_iso_datetime(meta_values.get(field_name))
    return not last_alerted or (now.astimezone(timezone.utc) - last_alerted).total_seconds() >= ALERT_COOLDOWN_SECONDS


def summary_config_signature(config: HermesConfig) -> str:
    return "watches=" + ",".join(
        f"{watch.site}:{watch.url}:{watch.target_price}:{watch.minimum_price}:{watch.excluded_terms}:"
        f"{watch.size}:{watch.include_variations}:{watch.active}"
        for watch in config.watches
    )


def summary_drop_threshold(expected_count: int) -> int:
    ratio_threshold = (expected_count + SUMMARY_DROP_RATIO_DIVISOR - 1) // SUMMARY_DROP_RATIO_DIVISOR
    return max(SUMMARY_DROP_MIN_DELTA, ratio_threshold)


def maybe_alert_summary_drop(state: Dict[str, Any], rows: List[PriceSummaryRow], config: HermesConfig,
                             notifier: Pushover) -> None:
    values = meta(state)
    current_count = len(rows)
    signature = summary_config_signature(config)
    if values.get("summary_config_signature") != signature:
        values.update({
            "summary_config_signature": signature,
            "summary_expected_row_count": current_count,
            "summary_last_row_count": current_count,
            "summary_drop_consecutive_cycles": 0,
        })
        log(f"Özet takip referansı güncellendi: beklenen_ürün={current_count}")
        return

    expected_count = int(values.get("summary_expected_row_count") or current_count)
    drop_count = expected_count - current_count
    is_unexpected_drop = expected_count > 0 and drop_count >= summary_drop_threshold(expected_count)
    if is_unexpected_drop and has_silent_access_failure(state, config):
        # A count warning would indirectly reveal a deliberately silent access
        # failure. Keep the reference and restart the streak on recovery.
        values["summary_drop_consecutive_cycles"] = 0
        values["summary_last_row_count"] = current_count
        log("Özet ürün sayısı uyarısı atlandı: CAPTCHA/HTTP 503 nedeniyle eksik sonuç var.")
        return

    now = local_now()
    try:
        previous_streak = max(0, int(values.get("summary_drop_consecutive_cycles", 0)))
    except (TypeError, ValueError):
        previous_streak = 0
    streak = previous_streak + 1 if is_unexpected_drop else 0
    values["summary_drop_consecutive_cycles"] = streak
    counts = f"beklenen={expected_count} | bu_tur={current_count} | fark={drop_count}"
    if is_unexpected_drop:
        if streak < SUMMARY_DROP_CONSECUTIVE_CYCLES:
            log(f"Özet ürün sayısı beklenenden düşük, kalıcılık izleniyor: {counts} | tur={streak}/{SUMMARY_DROP_CONSECUTIVE_CYCLES}")
        elif quiet_hours(now):
            log(f"Özet ürün sayısı düşüşü 5 tur sürdü, sessiz saat nedeniyle bildirim atlandı: {counts}")
        elif not _cooldown_passed(values, "last_summary_drop_alert_at", now):
            log(f"Özet ürün sayısı düşüşü 5 tur sürdü, 1 saatlik sınır nedeniyle bildirim atlandı: {counts}")
        elif notifier.configured:
            message = (
                "Özet tablodaki ürün sayısı 5 ardışık tur boyunca beklenenden fazla düştü.\n"
                f"Beklenen ürün sayısı: {expected_count}\n"
                f"Bu tur bulunan ürün sayısı: {current_count}\n"
                f"Fark: -{drop_count}\n"
                "Ayarları, özellikle Amazon arama linklerini kontrol etmeni öneririm. "
                "Amazon arama linkleri geçici olarak boş veya eksik dönmüş olabilir."
            )
            try:
                notifier.send("Hermes özet uyarısı", message)
                values["last_summary_drop_alert_at"] = utc_now()
                log(f"Özet ürün sayısı uyarısı gönderildi, düşüş 5 tur sürdü: {counts}")
            except Exception as exc:  # noqa: BLE001
                log(f"Özet ürün sayısı uyarısı gönderilemedi: {exc}")
        else:
            log("Özet ürün sayısı uyarısı atlandı: Pushover ayarları eksik.")
    else:
        expected_count = current_count
    values["summary_expected_row_count"] = max(expected_count, current_count)
    values["summary_last_row_count"] = current_count
    values["summary_config_signature"] = signature


def maybe_alert_search_failures(state: Dict[str, Any], events: List[Dict[str, Any]], notifier: Pushover) -> None:
    if not events:
        return
    affected_pages = sorted({str(event.get("page") or "") for event in events if event.get("page")})
    failed_links = sum(int(event.get("failed_links") or 0) for event in events)
    counts = f"sayfa={len(affected_pages)} | link={failed_links}"
    if len(affected_pages) < SEARCH_FAILURE_ALERT_MIN_PAGES and failed_links < SEARCH_FAILURE_ALERT_MIN_FAILED_LINKS:
        log(f"Arama erişim uyarısı atlandı, eşik altında: {counts}")
        return
    values = meta(state)
    now = local_now()
    if quiet_hours(now):
        log(f"Arama erişim uyarısı sessiz saat nedeniyle atlandı: {counts}")
        return
    if not _cooldown_passed(values, "last_search_failure_alert_at", now):
        log(f"Arama erişim uyarısı 1 saatlik sınır nedeniyle atlandı: {counts}")
        return
    if not notifier.configured:
        log("Arama erişim uyarısı atlandı: Pushover ayarları eksik.")
        return
    lines = [f"- {event.get('page') or '-'}: erişim hatası, link={int(event.get('failed_links') or 0)}" for event in events[:8]]
    if len(events) > 8:
        lines.append(f"- +{len(events) - 8} ek arama kaydı")
    message = (
        "Arama sayfalarında anlamlı sayıda erişim hatası yakalandı.\n"
        f"Etkilenen arama sayfası: {len(affected_pages)}\n"
        f"Hata veren link sayısı: {failed_links}\n"
        "Boş sonuçlar, CAPTCHA ve HTTP 503 bu uyarının dışındadır. "
        "Diğer erişim veya sayfa hatası olan bağlantıları kontrol etmeni öneririm.\n"
        + "\n".join(lines)
    )
    try:
        notifier.send("Hermes arama erişim uyarısı", message[:900])
        values["last_search_failure_alert_at"] = utc_now()
        log(f"Arama erişim uyarısı gönderildi: {counts}")
    except Exception as exc:  # noqa: BLE001
        log(f"Arama erişim uyarısı gönderilemedi: {exc}")
