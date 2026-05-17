from typing import Dict, List

from .constants import OPTIONS_PATH
from .errors import HermesError
from .models import AmazonSearchPage, AmazonSearchTarget, HermesConfig, ProductRule
from .storage import load_json
from .utils import normalize_site, parse_bool, parse_decimal


def _parse_search_urls(item: Dict[str, object]) -> List[str]:
    urls: List[str] = []
    for field_name in ("search_url", "search_url_2"):
        raw_url = str(item.get(field_name) or "").strip()
        if raw_url and raw_url not in urls:
            urls.append(raw_url)
    if not urls:
        raise HermesError("Amazon arama sayfasi icin en az search_url doldurulmali.")
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
        site = normalize_site(item.get("site"), url)
        name = str(item.get("name") or url).strip()
        products.append(
            ProductRule(
                name=name,
                site=site,
                url=url,
                target_price=parse_decimal(str(item["target_price"])),
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
                "Birden fazla amazon_search_pages varsa amazon_search_targets icinde search_name doldurulmali."
            )
        if search_name not in pages:
            raise HermesError(
                f"amazon_search_targets icinde tanimlanan arama sayfasi bulunamadi: {search_name}"
            )

        target_name = str(item.get("name") or "").strip()
        if not target_name:
            continue
        product_name = str(item.get("product_name") or target_name).strip()
        pages[search_name].targets.append(
            AmazonSearchTarget(
                name=target_name,
                search_name=search_name,
                product_name=product_name,
                target_price=parse_decimal(str(item["target_price"])),
                notify_once_in_24h=parse_bool(item.get("notify_once_in_24H"), default=True),
                active=True,
            )
        )


def load_config() -> HermesConfig:
    payload = load_json(OPTIONS_PATH, {})
    if not isinstance(payload, dict):
        payload = {}

    interval_minutes = int(payload.get("interval_minutes", 30))
    request_timeout_seconds = int(payload.get("request_timeout_seconds", 20))
    user_key = str(payload.get("pushover_user_key", "")).strip()
    api_token = str(payload.get("pushover_api_token", "")).strip()

    raw_products = payload.get("products", [])
    raw_pages = payload.get("amazon_search_pages", payload.get("search_pages", []))
    raw_targets = payload.get("amazon_search_targets", payload.get("search_targets", []))

    products = _prepare_products(raw_products)
    pages = _prepare_search_pages(raw_pages)
    _attach_search_targets(pages, raw_targets)
    search_pages = list(pages.values())

    if not products and not search_pages:
        raise HermesError("En az bir products veya amazon_search_pages kaydi tanimlanmali.")
    if not user_key or not api_token:
        raise HermesError("Pushover anahtarlari zorunlu.")
    if raw_pages and not any(page.targets for page in search_pages):
        raise HermesError("En az bir amazon_search_targets kaydi tanimlanmali.")

    return HermesConfig(
        interval_minutes=interval_minutes,
        request_timeout_seconds=request_timeout_seconds,
        pushover_user_key=user_key,
        pushover_api_token=api_token,
        products=products,
        amazon_search_pages=search_pages,
    )
