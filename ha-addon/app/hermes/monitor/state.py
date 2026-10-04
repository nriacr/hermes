"""Persistent per-watch and per-offer state: price history, alerts and access guards.

State keys and field names are unchanged since 2.x so either version can read
the other's `state.json`.
"""

import math
from datetime import timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict

from ..constants import NOTIFY_REPEAT_SECONDS, PROTECTION_PAUSE_LADDER_SECONDS
from ..errors import error_status
from ..logging_utils import log
from ..models import WatchRule
from ..utils import local_now, normalize_item_key, parse_iso_datetime, utc_now

PRICE_HISTORY_SPIKE_RATIO = Decimal("5")
PRICE_HISTORY_SPIKE_ABSOLUTE_TL = Decimal("50000")
PRICE_HISTORY_KEYS = ("min_price", "max_price", "min_price_at", "max_price_at")
META_KEY = "_meta"
# The guard store keeps its 2.x name; it now serves any site that backs off.
GUARDS_KEY = "amazon_protection"


def watch_key(watch: WatchRule) -> str:
    return normalize_item_key("watch", watch.site, watch.tracking_id or watch.name, watch.url, watch.size)


def offer_key(watch: WatchRule, url: str, is_warehouse: bool) -> str:
    return normalize_item_key(
        "watch_offer", watch.site, watch.tracking_id or watch.name, url, watch.size,
        "warehouse" if is_warehouse else "normal",
    )


def meta(state: Dict[str, Any]) -> Dict[str, Any]:
    value = state.get(META_KEY)
    if not isinstance(value, dict):
        value = {}
        state[META_KEY] = value
    return value


def entries(state: Dict[str, Any]):
    """(key, entry) pairs of watch and offer entries, without metadata."""
    return [(key, value) for key, value in state.items() if key != META_KEY and isinstance(value, dict)]


def state_decimal(value: Any):
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


# -- price history --------------------------------------------------------------


def _history_reference_price(state_entry: Dict[str, Any], target_price: Decimal | None = None) -> Decimal | None:
    references = []
    if target_price is not None and target_price > 0:
        references.append(target_price)
    last_price = state_decimal(state_entry.get("last_price"))
    if last_price is not None and last_price > 0:
        if target_price is None or not (
            last_price > target_price * PRICE_HISTORY_SPIKE_RATIO
            and last_price - target_price >= PRICE_HISTORY_SPIKE_ABSOLUTE_TL
        ):
            references.append(last_price)
    return max(references) if references else None


def is_absurd_price(state_entry: Dict[str, Any], current_price: Decimal, target_price: Decimal | None = None) -> bool:
    """A price far above both target and last price is a parsing accident, not history."""
    reference = _history_reference_price(state_entry, target_price)
    if reference is None:
        return False
    return current_price > reference * PRICE_HISTORY_SPIKE_RATIO and current_price - reference >= PRICE_HISTORY_SPIKE_ABSOLUTE_TL


def sanitized_price_bounds(state_entry: Dict[str, Any], current_price: Decimal, target_price: Decimal | None = None,
                           context: str = ""):
    min_price = state_decimal(state_entry.get("min_price"))
    max_price = state_decimal(state_entry.get("max_price"))
    if is_absurd_price(state_entry, current_price, target_price):
        if context:
            log(f"Şüpheli fiyat min/maks geçmişine eklenmedi: {context} | fiyat={current_price}")
        if min_price is not None and max_price is not None:
            return min_price, max_price
    if min_price is None or current_price < min_price:
        min_price = current_price
    if max_price is None or current_price > max_price:
        max_price = current_price
    return min_price, max_price


def updated_offer_entry(state_entry: Dict[str, Any], current_price: Decimal, target_price: Decimal, alert_sent: bool,
                        context: str = "") -> Dict[str, Any]:
    previous_min_price = state_decimal(state_entry.get("min_price"))
    previous_max_price = state_decimal(state_entry.get("max_price"))
    min_price, max_price = sanitized_price_bounds(state_entry, current_price, target_price, context)
    updated = dict(state_entry)
    updated["last_price"] = str(current_price)
    updated["min_price"] = str(min_price)
    updated["max_price"] = str(max_price)
    updated["last_checked_at"] = utc_now()
    if previous_min_price != min_price or not updated.get("min_price_at"):
        updated["min_price_at"] = updated["last_checked_at"]
    if previous_max_price != max_price or not updated.get("max_price_at"):
        updated["max_price_at"] = updated["last_checked_at"]
    updated["was_below_target"] = current_price <= target_price
    if alert_sent:
        updated["last_alerted_price"] = str(current_price)
        updated["last_alerted_at"] = utc_now()
    return updated


def clear_price_history(state: Dict[str, Any]) -> int:
    """Forget min/max of every offer; alert suppression is a separate reset."""

    def clear(value: Any) -> int:
        if isinstance(value, list):
            return sum(clear(item) for item in value)
        if not isinstance(value, dict):
            return 0
        count = 0
        for field_name in PRICE_HISTORY_KEYS:
            if field_name in value:
                value.pop(field_name, None)
                count += 1
        return count + sum(clear(child) for child in value.values())

    cleared = sum(clear(value) for _key, value in entries(state))
    meta(state)["price_history_reset_at"] = utc_now()
    return cleared


# -- notification suppression ---------------------------------------------------


def should_alert(state_entry: Dict[str, Any], current_price: Decimal, target_price: Decimal, repeat_after_24h: bool) -> bool:
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


def reset_alert_after_missing(state_entry: Dict[str, Any], seller: str, product_name: str) -> Dict[str, Any]:
    """A product that disappears may notify again when it returns at a good price."""
    updated = dict(state_entry)
    if updated.get("last_alerted_price") is None and updated.get("last_alerted_at") is None:
        return updated
    updated.pop("last_alerted_price", None)
    updated.pop("last_alerted_at", None)
    updated["last_missing_at"] = utc_now()
    log(f"Ürün stok/fiyat kayboldu, tekrar bildirim için hazırlandı: {seller} | {product_name}")
    return updated


def clear_notification_suppression(state: Dict[str, Any]) -> int:
    """Make qualifying opportunities notify once more and read every watch now."""
    reset_count = 0
    for _key, entry in entries(state):
        changed = False
        for field_name in ("last_alerted_price", "last_alerted_at", "last_checked_at"):
            if field_name in entry:
                entry.pop(field_name, None)
                changed = True
        reset_count += changed
    return reset_count


# -- access guards (back-off after protection pages) ----------------------------


def guard_store(state: Dict[str, Any]) -> Dict[str, Any]:
    store = meta(state).get(GUARDS_KEY)
    if not isinstance(store, dict):
        store = {}
        meta(state)[GUARDS_KEY] = store
    return store


def site_guard_key(site: str) -> str:
    """One guard per site: a protection page pauses every watch of the site."""
    return f"site:{site}"


def drop_watch_guards(state: Dict[str, Any]) -> None:
    """3.3.0 keeps one guard per site; per-watch guards of older versions are forgotten."""
    store = guard_store(state)
    for key in [key for key in store if not str(key).startswith("site:")]:
        del store[key]


def guard_cooldown_seconds(consecutive_blocks: int = 1) -> int:
    """15 → 30 → 60 minutes; each failed probe climbs one step, a success starts over."""
    steps = PROTECTION_PAUSE_LADDER_SECONDS
    return steps[min(max(consecutive_blocks - 1, 0), len(steps) - 1)]


def guard_remaining_seconds(state: Dict[str, Any], key: str) -> int:
    guard = guard_store(state).get(key)
    if not isinstance(guard, dict):
        return 0
    retry_after = parse_iso_datetime(guard.get("retry_after"))
    if not retry_after:
        return 0
    return max(0, math.ceil((retry_after - local_now().astimezone(timezone.utc)).total_seconds()))


def guard_kind(exc: BaseException) -> str:
    status = error_status(exc)
    return "http_503" if status == 503 else "http_429" if status == 429 else "captcha"


GUARD_LABELS = {
    "http_503": "servis hatası (HTTP 503)",
    "http_429": "istek sınırı (HTTP 429)",
    "captcha": "doğrulama/koruma sayfası",
}


def note_guard(state: Dict[str, Any], key: str, source: str, exc: BaseException, site_label: str = "Amazon") -> None:
    """Pause the whole site after a protection page; repeated blocks climb the ladder.

    A block reported while the pause already runs (the other lane's read that was under way)
    belongs to the same pause and neither extends it nor climbs the ladder.
    """
    store = guard_store(state)
    now = local_now().astimezone(timezone.utc)
    previous = store.get(key)
    if guard_remaining_seconds(state, key) > 0:
        return
    previous_at = parse_iso_datetime(previous.get("blocked_at")) if isinstance(previous, dict) else None
    try:
        previous_count = int(previous.get("consecutive_blocks") or 1) if isinstance(previous, dict) else 1
    except (TypeError, ValueError):
        previous_count = 1
    recent = bool(previous_at and 0 <= (now - previous_at).total_seconds() < 6 * 60 * 60)
    hold = getattr(exc, "hold_seconds", None)
    if hold:
        # Nothing was sent: the client still holds back after an earlier block. The pause covers
        # the rest of the hold without climbing the ladder; the probe comes after it.
        store[key] = {
            "blocked_at": previous.get("blocked_at") if recent else now.isoformat(),
            "retry_after": (now + timedelta(seconds=math.ceil(hold))).isoformat(),
            "consecutive_blocks": previous_count if recent else 1,
            "source": source,
            "message": str(exc)[:300],
            "kind": "captcha",
        }
        log(f"{site_label} engelden sonraki istek molası sürüyor: {math.ceil(hold / 60)} dk sonra yeniden denenecek.")
        return
    consecutive = min(len(PROTECTION_PAUSE_LADDER_SECONDS), previous_count + 1) if recent else 1
    cooldown = guard_cooldown_seconds(consecutive)
    kind = guard_kind(exc)
    store[key] = {
        "blocked_at": now.isoformat(),
        "retry_after": (now + timedelta(seconds=cooldown)).isoformat(),
        "consecutive_blocks": consecutive,
        "source": source,
        "message": str(exc)[:300],
        "kind": kind,
    }
    log(f"{site_label} {GUARD_LABELS[kind]}: {source} | {cooldown // 60} dk sonra yeniden denenecek.")


def clear_guard(state: Dict[str, Any], key: str, site_label: str = "Amazon") -> None:
    """A success after the pause ends it. A read that began before the block and answered during
    the pause (the other lane's) proves nothing about the visitor now and leaves the pause running."""
    if guard_remaining_seconds(state, key) > 0:
        return
    if guard_store(state).pop(key, None):
        log(f"{site_label} koruması sona erdi; normal tarama yeniden başladı.")
