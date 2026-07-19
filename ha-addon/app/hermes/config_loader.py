from decimal import Decimal
from typing import Dict, List, Optional

from .constants import (
    DEFAULT_REQUEST_DELAY_MAX_SECONDS,
    DEFAULT_REQUEST_DELAY_MIN_SECONDS,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_SEARCH_MAX_ITEMS_TO_SCAN,
    OPTIONS_PATH,
    SITE_HM,
    SITE_ZARA,
)
from .errors import HermesError
from .logging_utils import log
from .models import HermesConfig, TelegramConfig, WatchRule
from .storage import load_json
from .utils import detect_site_from_url, parse_bool, parse_decimal, watch_name_required_for_url

WATCH_URL_FIELDS = ("url_1", "url_2", "url_3", "url_4", "url_5")


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


def _optional_bounded_integer(
    item: Dict[str, object], field_name: str, minimum: int, maximum: int
) -> Optional[int]:
    raw_value = item.get(field_name)
    if raw_value is None or str(raw_value).strip() == "":
        return None
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise HermesError(f"{field_name} tam sayı olmalı.") from exc
    if not minimum <= value <= maximum:
        raise HermesError(f"{field_name} {minimum} ile {maximum} arasında olmalı.")
    return value


def _watch_urls(item: Dict[str, object]) -> List[str]:
    urls: List[str] = []
    for field_name in WATCH_URL_FIELDS:
        raw_url = str(item.get(field_name) or "").strip()
        if raw_url and raw_url not in urls:
            urls.append(raw_url)
    return urls


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


def _string_list(value: object) -> List[str]:
    raw_values = value if isinstance(value, list) else [value]
    values = []
    for raw_value in raw_values:
        values.extend(str(raw_value or "").replace(",", "\n").splitlines())
    return [item.strip() for item in values if item.strip()]


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


def _prepare_telegram_config(payload: Dict[str, object]) -> TelegramConfig:
    channels = _string_list(payload.get("channels")) or DEFAULT_TELEGRAM_CHANNELS
    keywords = _string_list(payload.get("keywords"))
    exclude_keywords = _string_list(payload.get("exclude_keywords"))
    enabled = parse_bool(payload.get("telegram_enabled"), default=False)
    return TelegramConfig(
        enabled=enabled,
        api_id=_optional_int(payload.get("api_id"), "api_id"),
        api_hash=str(payload.get("api_hash") or "").strip(),
        phone_number=str(payload.get("phone_number") or "").strip(),
        verification_code=str(payload.get("verification_code") or "").strip(),
        session_name=str(payload.get("session_name") or "telegram_keyword_alert").strip()
        or "telegram_keyword_alert",
        channels=channels,
        keywords=keywords,
        exclude_keywords=exclude_keywords,
        saved_messages_enabled=parse_bool(payload.get("telegram_saved_messages_enabled"), default=True),
    )


def _prepare_watches(raw_watches: object) -> List[WatchRule]:
    watches: List[WatchRule] = []
    if not isinstance(raw_watches, list):
        return watches
    for item in raw_watches:
        if not isinstance(item, dict):
            continue
        if not parse_bool(item.get("active"), default=True):
            continue
        urls = _watch_urls(item)
        if not urls:
            continue
        name = str(item.get("name") or "").strip()
        context_name = name or "adsız ürün"
        supported_urls = _supported_watch_urls(urls, context_name)
        if not supported_urls:
            continue
        if not name and any(watch_name_required_for_url(url) for url, _ in supported_urls):
            raise HermesError("Arama linkleri için name alanı zorunlu. Ürün linklerinde boş bırakılabilir.")
        target_price = parse_decimal(_required_value(item, "target_price", f"Takip edilen ({context_name})"))
        minimum_price = _optional_price(item, "minimum_price")
        if minimum_price is not None and minimum_price > target_price:
            raise HermesError(f"Takip edilen ({context_name}) için minimum fiyat hedef fiyattan büyük olamaz.")
        excluded_terms = _string_list(item.get("exclude_terms"))
        group = str(item.get("group") or "").strip()
        if not group and any(site in {SITE_ZARA, SITE_HM} for _, site in supported_urls):
            group = "Moda"
        size = str(item.get("size") or "").strip()
        check_interval_minutes = _optional_bounded_integer(item, "check_interval_minutes", 1, 1440)
        notify_once_in_24h = parse_bool(item.get("notify_once_in_24H"), default=True)
        for url, site in supported_urls:
            watches.append(
                WatchRule(
                    name=name,
                    site=site,
                    url=url,
                    target_price=target_price,
                    minimum_price=minimum_price,
                    excluded_terms=excluded_terms,
                    group=group,
                    size=size,
                    max_items_to_scan=DEFAULT_SEARCH_MAX_ITEMS_TO_SCAN,
                    check_interval_minutes=check_interval_minutes,
                    notify_once_in_24h=notify_once_in_24h,
                    active=True,
                )
            )
    return watches


def load_config() -> HermesConfig:
    payload = load_json(OPTIONS_PATH, {})
    if not isinstance(payload, dict):
        payload = {}

    interval_seconds = _bounded_integer(
        payload,
        "interval_seconds",
        60,
        10,
        86400,
    )
    request_timeout_seconds = DEFAULT_REQUEST_TIMEOUT_SECONDS
    request_delay_min_seconds = _bounded_integer(
        payload,
        "request_delay_min_seconds",
        DEFAULT_REQUEST_DELAY_MIN_SECONDS,
        0,
        120,
    )
    request_delay_max_seconds = _bounded_integer(
        payload,
        "request_delay_max_seconds",
        DEFAULT_REQUEST_DELAY_MAX_SECONDS,
        0,
        120,
    )
    if request_delay_min_seconds > request_delay_max_seconds:
        raise HermesError("request_delay_min_seconds, request_delay_max_seconds değerinden büyük olamaz.")

    user_key = str(payload.get("pushover_user_key", "")).strip()
    api_token = str(payload.get("pushover_api_token", "")).strip()

    watches = _prepare_watches(payload.get("takip_edilenler", []))
    telegram = _prepare_telegram_config(payload)

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
        request_timeout_seconds=request_timeout_seconds,
        request_delay_min_seconds=request_delay_min_seconds,
        request_delay_max_seconds=request_delay_max_seconds,
        pushover_user_key=user_key,
        pushover_api_token=api_token,
        watches=watches,
        telegram=telegram,
    )
