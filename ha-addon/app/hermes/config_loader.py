from typing import Dict, List, Optional

from .constants import (
    DEFAULT_REQUEST_DELAY_MAX_SECONDS,
    DEFAULT_REQUEST_DELAY_MIN_SECONDS,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    OPTIONS_PATH,
)
from .errors import HermesError
from .models import AmazonSearchPage, AmazonSearchTarget, HermesConfig, ProductRule
from .storage import load_json
from .utils import detect_site_from_url, parse_bool, parse_decimal


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


def _legacy_interval_seconds(payload: Dict[str, object]) -> int:
    raw_minutes = payload.get("interval_minutes")
    if raw_minutes is None or str(raw_minutes).strip() == "":
        return 60
    try:
        return int(raw_minutes) * 60
    except (TypeError, ValueError):
        return 60


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


def _parse_search_urls(item: Dict[str, object]) -> List[str]:
    urls: List[str] = []
    for field_name in ("search_url", "search_url_2"):
        raw_url = str(item.get(field_name) or "").strip()
        if raw_url and raw_url not in urls:
            urls.append(raw_url)
    if not urls:
        raise HermesError("Amazon arama sayfası için en az search_url doldurulmalı.")
    return urls


def _prepare_products(raw_products: object) -> List[ProductRule]:
    products: List[ProductRule] = []
    if not isinstance(raw_products, list):
        return products
    for item in raw_products:
        if not isinstance(item, dict):
            continue
        if not parse_bool(item.get("active"), default=True):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        name = str(item.get("name") or url).strip()
        products.append(
            ProductRule(
                name=name,
                site=detect_site_from_url(url),
                url=url,
                target_price=parse_decimal(_required_value(item, "target_price", f"Ürün ({name})")),
                check_interval_minutes=_optional_bounded_integer(item, "check_interval_minutes", 1, 1440),
                notify_once_in_24h=parse_bool(item.get("notify_once_in_24H"), default=True),
                active=True,
            )
        )
    return products


def _prepare_search_pages(raw_pages: object) -> Dict[str, AmazonSearchPage]:
    pages: Dict[str, AmazonSearchPage] = {}
    if not isinstance(raw_pages, list):
        return pages
    for item in raw_pages:
        if not isinstance(item, dict):
            continue
        page_name = str(item.get("name") or "").strip()
        if not page_name:
            continue
        pages[page_name] = AmazonSearchPage(
            name=page_name,
            search_urls=_parse_search_urls(item),
            max_items_to_scan=int(item.get("max_items_to_scan", 24)),
            targets=[],
        )
    return pages


def _attach_search_targets(pages: Dict[str, AmazonSearchPage], raw_targets: object) -> None:
    if not isinstance(raw_targets, list):
        return
    for item in raw_targets:
        if not isinstance(item, dict):
            continue
        if not parse_bool(item.get("active"), default=True):
            continue
        search_name = str(item.get("search_name") or "").strip()
        if not search_name and len(pages) == 1:
            search_name = next(iter(pages))
        if not search_name:
            raise HermesError(
                "Birden fazla amazon_search_pages varsa amazon_search_targets içinde search_name doldurulmalı."
            )
        if search_name not in pages:
            raise HermesError(
                f"amazon_search_targets içinde tanımlanan arama sayfası bulunamadı: {search_name}"
            )

        product_name = str(item.get("product_name") or item.get("name") or "").strip()
        if not product_name:
            continue
        pages[search_name].targets.append(
            AmazonSearchTarget(
                name=product_name,
                search_name=search_name,
                product_name=product_name,
                target_price=parse_decimal(_required_value(item, "target_price", f"Amazon hedefi ({product_name})")),
                notify_once_in_24h=parse_bool(item.get("notify_once_in_24H"), default=True),
                active=True,
            )
        )


def load_config() -> HermesConfig:
    payload = load_json(OPTIONS_PATH, {})
    if not isinstance(payload, dict):
        payload = {}

    interval_seconds = _bounded_integer(
        payload,
        "interval_seconds",
        _legacy_interval_seconds(payload),
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

    raw_products = payload.get("products", [])
    raw_pages = payload.get("amazon_search_pages", [])
    raw_targets = payload.get("amazon_search_targets", [])

    products = _prepare_products(raw_products)
    pages = _prepare_search_pages(raw_pages)
    _attach_search_targets(pages, raw_targets)
    search_pages = list(pages.values())

    if not products and not search_pages:
        raise HermesError("En az bir products veya amazon_search_pages kaydı tanımlanmalı.")
    if not user_key or not api_token:
        raise HermesError("Pushover anahtarları zorunlu.")
    if raw_pages and not any(page.targets for page in search_pages):
        raise HermesError("En az bir amazon_search_targets kaydı tanımlanmalı.")

    return HermesConfig(
        interval_seconds=interval_seconds,
        request_timeout_seconds=request_timeout_seconds,
        request_delay_min_seconds=request_delay_min_seconds,
        request_delay_max_seconds=request_delay_max_seconds,
        pushover_user_key=user_key,
        pushover_api_token=api_token,
        products=products,
        amazon_search_pages=search_pages,
    )
