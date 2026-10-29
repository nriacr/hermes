"""Supervisor options: reading, validation and the defaults used when saving."""

from copy import deepcopy
from itertools import product
from decimal import Decimal
from typing import Any, Dict, List, Optional

from .constants import (
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_REQUEST_DELAY_MAX_SECONDS,
    DEFAULT_REQUEST_DELAY_MIN_SECONDS,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    OPTIONS_PATH,
    SEARCH_RESULT_LIMIT,
    normalize_priority,
)
from .errors import HermesError
from .logging_utils import log, configure_secrets
from .models import HermesConfig, TelegramConfig, WatchRule
from .storage import load_json
from .utils import detect_site_from_url, normalize_item_key, parse_bool, parse_decimal, watch_name_required_for_url

WATCH_URL_FIELDS = ("url_1", "url_2", "url_3", "url_4", "url_5")

DEFAULT_TELEGRAM_CHANNELS = [
    "@yaniyocom",
    "@firsatz",
    "@onual_firsat",
    "@onual_ekstra",
    "@butcedostu",
    "@depoindirim",
    "@uygunfiyatdedektifi",
    "@tasarrufluharca",
    "@depourunleri",
    "@evEkonomi",
    "@firsatavi",
]

# Every key the Supervisor schema requires. A settings save writes the complete
# option set, so unrelated values (Pushover, public token) are never dropped.
OPTION_DEFAULTS: Dict[str, Any] = {
    "interval_seconds": DEFAULT_INTERVAL_SECONDS,
    "request_delay_min_seconds": DEFAULT_REQUEST_DELAY_MIN_SECONDS,
    "request_delay_max_seconds": DEFAULT_REQUEST_DELAY_MAX_SECONDS,
    "pushover_user_key": "",
    "pushover_api_token": "",
    "public_dashboard_enabled": False,
    "public_dashboard_token": "",
    "telegram_enabled": False,
    "telegram_saved_messages_enabled": True,
    "api_id": "",
    "api_hash": "",
    "phone_number": "",
    "verification_code": "",
    "session_name": "telegram_keyword_alert",
    "channels": list(DEFAULT_TELEGRAM_CHANNELS),
    "keywords": [],
    "exclude_keywords": [],
    "takip_edilenler": [],
}


def read_options() -> Dict[str, Any]:
    payload = load_json(OPTIONS_PATH, {})
    return payload if isinstance(payload, dict) else {}


REMOVED_OPTIONS = ("gruplar", "daily_summary_enabled")
REMOVED_CARD_KEYS = ("group", "auto_add_to_cart", "notify_once_in_24H", "notify_90_day_low", "notify_window")


def options_with_defaults(options: Any) -> Dict[str, Any]:
    """Preserve every existing add-on option while supplying schema defaults."""
    saved = deepcopy(options) if isinstance(options, dict) else {}
    # Removed in 4.26.0: groups (the settings list groups cards by site) and the daily summary.
    for removed in REMOVED_OPTIONS:
        saved.pop(removed, None)
    for key, value in OPTION_DEFAULTS.items():
        saved.setdefault(key, deepcopy(value))
    for item in saved.get("takip_edilenler", []):
        if isinstance(item, dict):
            # Removed card keys: groups, the cart box (Depo deals always go to the cart) and the
            # notification settings (every card is muted 24 hours after an alert, at any hour).
            for key in REMOVED_CARD_KEYS:
                item.pop(key, None)
        if isinstance(item, dict) and not item.get("id"):
            try:
                target = parse_decimal(str(item.get("target_price") or "0"))
            except HermesError:
                target = str(item.get("target_price") or "")
            supported = []
            for url in watch_urls(item):
                try:
                    detect_site_from_url(url)
                    supported.append(url)
                except HermesError:
                    pass
            item["id"] = tracking_card_id(str(item.get("name") or "").strip(), target,
                str(item.get("size") or "").strip(), supported)
    return saved


def watch_urls(item: Dict[str, object]) -> List[str]:
    urls: List[str] = []
    if not isinstance(item, dict):
        return urls
    for field_name in WATCH_URL_FIELDS:
        raw_url = str(item.get(field_name) or "").strip()
        if raw_url and raw_url not in urls:
            urls.append(raw_url)
    return urls


def night_window(item: Dict[str, object]) -> tuple:
    """("23:00", "07:00") when the card has night mode on with two valid times, else ("", "")."""
    if not parse_bool(item.get("night_mode"), default=False):
        return "", ""
    times = []
    for key, default in (("night_start", "23:00"), ("night_end", "07:00")):
        text = str(item.get(key) or default).strip()
        hours, _, minutes = text.partition(":")
        if not (hours.isdigit() and minutes.isdigit() and 0 <= int(hours) < 24 and 0 <= int(minutes) < 60):
            return "", ""
        times.append(f"{int(hours):02d}:{int(minutes):02d}")
    return (times[0], times[1]) if times[0] != times[1] else ("", "")


def card_sizes(value: object) -> List[str]:
    """"M; L" -> ["M", "L"]: a card watches each chosen size on its own (";" since shoe sizes can hold a comma)."""
    sizes = list(dict.fromkeys(part.strip() for part in str(value or "").split(";") if part.strip()))
    return sizes or [""]


def parse_variant_filter(value: object) -> Dict[str, List[str]]:
    """"Renk=Siyah, Mavi; Beden=M" -> {"Renk": ["Siyah", "Mavi"], "Beden": ["M"]}."""
    chosen: Dict[str, List[str]] = {}
    for part in str(value or "").split(";"):
        dimension, separator, values = part.partition("=")
        names = [item.strip() for item in values.split(",") if item.strip()]
        if separator and dimension.strip() and names:
            chosen[dimension.strip()] = names
    return chosen


def format_variant_filter(chosen: Dict[str, List[str]]) -> str:
    return "; ".join(f"{dimension}={', '.join(values)}" for dimension, values in chosen.items() if values)


def string_list(value: object) -> List[str]:
    raw_values = value if isinstance(value, list) else [value]
    values = []
    for raw_value in raw_values:
        values.extend(str(raw_value or "").replace(",", "\n").splitlines())
    return [item.strip() for item in values if item.strip()]


def _required_value(item: Dict[str, object], field_name: str, context: str) -> str:
    value = str(item.get(field_name) or "").strip()
    if not value:
        raise HermesError(f"{context} için {field_name} alanı zorunlu.")
    return value


def _bounded_integer(payload: Dict[str, object], field_name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(payload.get(field_name, default))
    except (TypeError, ValueError) as exc:
        raise HermesError(f"{field_name} tam sayı olmalı.") from exc
    if not minimum <= value <= maximum:
        raise HermesError(f"{field_name} {minimum} ile {maximum} arasında olmalı.")
    return value


def _optional_int(value: object, field_name: str) -> Optional[int]:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise HermesError(f"{field_name} tam sayı olmalı.") from exc


def _optional_price(item: Dict[str, object], field_name: str) -> Optional[Decimal]:
    raw_value = item.get(field_name)
    if raw_value is None or str(raw_value).strip() == "":
        return None
    return parse_decimal(str(raw_value))


def _supported_watch_urls(urls: List[str], context_name: str) -> List[tuple[str, str]]:
    """Keep an invalid link from preventing every other watch from starting."""
    supported_urls: List[tuple[str, str]] = []
    for url in urls:
        try:
            site = detect_site_from_url(url)
        except HermesError as exc:
            log(f"Desteklenmeyen takip linki atlandı: {context_name} | {url} | {exc}")
            continue
        supported_urls.append((url, site))
    return supported_urls


def tracking_card_id(name: str, target_price: Decimal, size: str, urls: List[str]) -> str:
    """Stable identity of one tracking card; it keys state and summary rows."""
    return normalize_item_key("tracking_card", name, str(target_price), size, "|".join(sorted(urls)))


def _prepare_watch(item):
    watches = []
    if not parse_bool(item.get("active"), default=True):
        return []
    urls = watch_urls(item)
    if not urls:
        return []
    name = str(item.get("name") or "").strip()
    context_name = name or "adsız ürün"
    supported_urls = _supported_watch_urls(urls, context_name)
    if not supported_urls:
        return []
    if not name and any(watch_name_required_for_url(url) for url, _ in supported_urls):
        raise HermesError("Arama linkleri için name alanı zorunlu. Ürün linklerinde boş bırakılabilir.")
    target_price = parse_decimal(_required_value(item, "target_price", f"Takip edilen ({context_name})"))
    minimum_price = _optional_price(item, "minimum_price")
    if minimum_price is not None and minimum_price > target_price:
        raise HermesError(f"Takip edilen ({context_name}) için minimum fiyat hedef fiyattan büyük olamaz.")
    # Pre-3.12 cards (high/medium/low or none) are read every 6 hours until they are saved again.
    priority = normalize_priority(item.get("priority"))
    size = str(item.get("size") or "").strip()
    # A card may contain several links, but all of them belong to the same
    # tracking rule. This keeps their results separate from another card.
    tracking_id = str(item.get("id") or tracking_card_id(name, target_price, size, [url for url, _ in supported_urls]))
    # Each link, and each chosen size of it, is read on its own.
    for (url, site), one_size in product(supported_urls, card_sizes(size)):
        watches.append(
            WatchRule(
                name=name,
                site=site,
                url=url,
                target_price=target_price,
                minimum_price=minimum_price,
                excluded_terms=string_list(item.get("exclude_terms")),
                size=one_size,
                variant_filter=parse_variant_filter(item.get("variant_filter")),
                night_start=night_window(item)[0],
                night_end=night_window(item)[1],
                include_variations=parse_bool(item.get("include_variations"), default=False),
                priority=priority,
                official_seller_only=parse_bool(item.get("official_seller_only"), default=False),
                check_now_token=str(item.get("check_now_token") or "").strip(),
                max_items_to_scan=SEARCH_RESULT_LIMIT,
                active=True,
                tracking_id=tracking_id,
            )
        )
    return watches

def prepare_watches(raw_watches: object, errors=None) -> List[WatchRule]:
    watches = []
    for item in raw_watches if isinstance(raw_watches, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            watches.extend(_prepare_watch(item))
        except HermesError as exc:
            if errors is None:
                raise
            message = f"{str(item.get('name') or 'Adsız takip')}: {exc}"
            errors.append(message)
            log(f"Hatalı takip atlandı: {message}")
    return watches


def prepare_telegram_config(payload: Dict[str, object]) -> TelegramConfig:
    return TelegramConfig(
        enabled=parse_bool(payload.get("telegram_enabled"), default=False),
        api_id=_optional_int(payload.get("api_id"), "api_id"),
        api_hash=str(payload.get("api_hash") or "").strip(),
        phone_number=str(payload.get("phone_number") or "").strip(),
        verification_code=str(payload.get("verification_code") or "").strip(),
        channels=string_list(payload.get("channels")) or list(DEFAULT_TELEGRAM_CHANNELS),
        keywords=string_list(payload.get("keywords")),
        exclude_keywords=string_list(payload.get("exclude_keywords")),
        saved_messages_enabled=parse_bool(payload.get("telegram_saved_messages_enabled"), default=True),
    )


def load_config(payload: Optional[Dict[str, Any]] = None, *, tolerant=False) -> HermesConfig:
    if payload is None:
        payload = read_options()
    configure_secrets(payload.get(key) for key in ("pushover_user_key", "pushover_api_token", "api_hash", "phone_number", "verification_code", "public_dashboard_token"))
    interval_seconds = _bounded_integer(payload, "interval_seconds", DEFAULT_INTERVAL_SECONDS, 1, 86400)
    request_delay_min_seconds = _bounded_integer(
        payload, "request_delay_min_seconds", DEFAULT_REQUEST_DELAY_MIN_SECONDS, 0, 120
    )
    request_delay_max_seconds = _bounded_integer(
        payload, "request_delay_max_seconds", DEFAULT_REQUEST_DELAY_MAX_SECONDS, 0, 120
    )
    if request_delay_min_seconds > request_delay_max_seconds:
        raise HermesError("request_delay_min_seconds, request_delay_max_seconds değerinden büyük olamaz.")

    user_key = str(payload.get("pushover_user_key", "")).strip()
    api_token = str(payload.get("pushover_api_token", "")).strip()
    config_errors = []
    watches = prepare_watches(payload.get("takip_edilenler", []), config_errors if tolerant else None)
    telegram = prepare_telegram_config(payload)

    if not watches and not telegram.enabled:
        raise HermesError("En az bir takip edilen kayıt veya Telegram dinleme kaydı tanımlanmalı.")
    if not user_key or not api_token:
        raise HermesError("Pushover anahtarları zorunlu.")
    if telegram.enabled:
        if not telegram.api_id or not telegram.api_hash or not telegram.phone_number:
            raise HermesError("Telegram aktifse api_id, api_hash ve phone_number zorunlu.")
        if not telegram.channels and not telegram.saved_messages_enabled:
            raise HermesError("Telegram aktifse en az bir channels kaydı tanımlanmalı.")
        if not telegram.keywords and not telegram.saved_messages_enabled:
            raise HermesError("Telegram aktifse en az bir keywords kaydı tanımlanmalı.")

    return HermesConfig(
        interval_seconds=interval_seconds,
        request_timeout_seconds=DEFAULT_REQUEST_TIMEOUT_SECONDS,
        request_delay_min_seconds=request_delay_min_seconds,
        request_delay_max_seconds=request_delay_max_seconds,
        pushover_user_key=user_key,
        pushover_api_token=api_token,
        watches=watches,
        telegram=telegram,
        config_errors=config_errors,
    )
