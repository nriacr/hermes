import json
import re
from decimal import Decimal
from typing import Any, Dict, Iterable, List
from urllib.parse import urlsplit

import requests

from ..constants import SITE_ZARA
from ..errors import HermesError, OutOfStockHermesError
from ..logging_utils import log
from ..models import OfferResult
from ..utils import normalize_offer_text, parse_decimal, referer_for_url, repair_mojibake
from .base import Provider, iter_json_objects, soup_from_html
from .http import decode_response_text, read_site_html

OUT_OF_STOCK_MARKERS = ("outofstock", "discontinued", "benzer urunler", "benzer ürünler")


def _jsonld_payloads(html: str) -> Iterable[dict]:
    soup = soup_from_html(html)
    for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw = script.string or script.get_text(" ", strip=True)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for item in iter_json_objects(payload):
            if isinstance(item, dict):
                yield item


def _product_payloads(html: str) -> List[dict]:
    products: List[dict] = []
    for payload in _jsonld_payloads(html):
        raw_type = payload.get("@type")
        types = raw_type if isinstance(raw_type, list) else [raw_type]
        if any(str(item).casefold() == "product" for item in types):
            products.append(payload)
    variant_containers = [item for item in products if isinstance(item.get("hasVariant"), (dict, list))]
    return variant_containers or products


def _offer_price(value: Any) -> Decimal | None:
    if isinstance(value, list):
        prices = [_offer_price(item) for item in value]
        prices = [item for item in prices if item is not None]
        return min(prices) if prices else None
    if not isinstance(value, dict):
        return None
    for key in ("price", "lowPrice", "highPrice", "priceSpecification"):
        raw_value = value.get(key)
        if isinstance(raw_value, dict):
            price = _offer_price(raw_value)
            if price is not None:
                return price
        elif raw_value not in (None, ""):
            try:
                return parse_decimal(str(raw_value))
            except HermesError:
                continue
    return None


def _availability(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(_availability(item) for item in value)
    if isinstance(value, dict):
        return " ".join(_availability(item) for item in value.values())
    return str(value or "")


def _is_available(variant: dict) -> bool:
    offers = variant.get("offers")
    raw_availability = _availability(offers.get("availability") if isinstance(offers, dict) else offers)
    normalized = normalize_offer_text(raw_availability)
    return not any(marker in normalized for marker in OUT_OF_STOCK_MARKERS)


def _clean_part(value: Any) -> str:
    return re.sub(r"\s+", " ", repair_mojibake(str(value or ""))).strip()


def _strip_parenthetical(value: str) -> str:
    return _clean_part(re.sub(r"\([^)]*\)", " ", value))


def _variant_size(variant: dict) -> str:
    for key in ("size", "skuSize", "productSize", "name"):
        value = _clean_part(variant.get(key))
        if value:
            if key == "name":
                match = re.search(r"\b([A-Z]{1,3}\s*\([^)]*\)|[A-Z]{1,3})\b", value)
                return _strip_parenthetical(match.group(1)) if match else ""
            return _strip_parenthetical(value)
    return ""


def _size_tokens(value: str) -> set[str]:
    normalized = normalize_offer_text(_strip_parenthetical(value))
    ignored = {"eu", "us", "uk", "yas", "yaş", "yil", "yıl", "beden"}
    return {token for token in re.findall(r"[a-z0-9]+", normalized) if token not in ignored}


def _size_matches(variant_size: str, requested_size: str) -> bool:
    variant = normalize_offer_text(_strip_parenthetical(variant_size))
    requested = normalize_offer_text(_strip_parenthetical(requested_size))
    if not requested:
        return True
    if variant == requested:
        return True
    variant_tokens = _size_tokens(variant)
    requested_tokens = _size_tokens(requested)
    return bool(requested_tokens) and requested_tokens.issubset(variant_tokens)


def _title_with_parts(product_name: str, color: str, size: str = "") -> str:
    parts = [_clean_part(product_name)]
    for value in (color, size):
        clean = _clean_part(value)
        if clean and normalize_offer_text(clean) not in {normalize_offer_text(item) for item in parts}:
            parts.append(clean)
    return " / ".join(parts)


def _base_product_name(value: str, color: str, size: str) -> str:
    title = _strip_parenthetical(_clean_part(value))
    for suffix in (size, color):
        clean_suffix = _clean_part(suffix)
        if clean_suffix and normalize_offer_text(title).endswith(normalize_offer_text(f" - {clean_suffix}")):
            title = title[: -len(f" - {clean_suffix}")].strip()
    return title


def _variants_from_product(product: dict) -> List[dict]:
    variants = product.get("hasVariant")
    if isinstance(variants, dict):
        return [variants]
    if isinstance(variants, list):
        return [variant for variant in variants if isinstance(variant, dict)]
    return [product]


def _candidate_variants(html: str) -> List[tuple[dict, dict]]:
    candidates: List[tuple[dict, dict]] = []
    for product in _product_payloads(html):
        for variant in _variants_from_product(product):
            candidates.append((product, variant))
    return candidates


def _offer_from_variant(product: dict, variant: dict, source_url: str) -> OfferResult | None:
    price = _offer_price(variant.get("offers")) or _offer_price(product.get("offers"))
    if price is None:
        return None
    color = _clean_part(variant.get("color") or product.get("color"))
    size = _variant_size(variant)
    product_name = _base_product_name(product.get("name") or variant.get("name") or "Zara ürünü", color, size)
    offers = variant.get("offers")
    offer_url = offers.get("url") if isinstance(offers, dict) else ""
    url = _clean_part(variant.get("url") or offer_url or source_url)
    return OfferResult(
        title=_title_with_parts(product_name, color, size),
        price=price,
        seller="Zara",
        url=url or source_url,
    )


def _stock_identity_from_variant(product: dict, variant: dict, source_url: str) -> tuple[str, str]:
    color = _clean_part(variant.get("color") or product.get("color"))
    size = _variant_size(variant)
    product_name = _base_product_name(product.get("name") or variant.get("name") or "Zara ürünü", color, size)
    offers = variant.get("offers")
    offer_url = offers.get("url") if isinstance(offers, dict) else ""
    url = _clean_part(variant.get("url") or offer_url or source_url)
    return _title_with_parts(product_name, color, size), url or source_url


def extract_offers(html: str, source_url: str = "", size: str = "") -> List[OfferResult]:
    requested_size = _clean_part(size)
    offers: List[OfferResult] = []
    seen_offer_keys: set[tuple[str, str, str]] = set()
    matched_size = False
    matched_available = False
    stock_title = ""
    stock_url = ""
    fallback_title = ""
    fallback_url = ""

    for product, variant in _candidate_variants(html):
        variant_size = _variant_size(variant)
        if requested_size and not fallback_title:
            color = _clean_part(variant.get("color") or product.get("color"))
            product_name = _base_product_name(
                product.get("name") or variant.get("name") or "Zara ürünü",
                color,
                requested_size,
            )
            fallback_title = _title_with_parts(product_name, color, requested_size)
            raw_offers = variant.get("offers")
            offer_url = raw_offers.get("url") if isinstance(raw_offers, dict) else ""
            fallback_url = _clean_part(variant.get("url") or offer_url or source_url) or source_url
        if requested_size and not _size_matches(variant_size, requested_size):
            continue
        if requested_size:
            matched_size = True
            if not stock_title:
                stock_title, stock_url = _stock_identity_from_variant(product, variant, source_url)
        if not _is_available(variant):
            continue
        matched_available = True
        offer = _offer_from_variant(product, variant, source_url)
        if offer:
            offer_key = (normalize_offer_text(offer.title), str(offer.price), normalize_offer_text(offer.url or ""))
            if offer_key in seen_offer_keys:
                continue
            seen_offer_keys.add(offer_key)
            offers.append(offer)

    if requested_size:
        title = stock_title or fallback_title
        url = stock_url or fallback_url
        if not matched_size:
            raise OutOfStockHermesError(f"Zara beden bulunamadı: {requested_size}", title, url)
        if not matched_available:
            raise OutOfStockHermesError(f"Zara beden stokta değil: {requested_size}", title, url)
        if not offers:
            raise OutOfStockHermesError(f"Zara beden fiyatı bulunamadı: {requested_size}", title, url)
        return offers

    if offers:
        return [min(offers, key=lambda item: item.price)]
    raise OutOfStockHermesError("Zara sayfasından stokta ürün fiyatı bulunamadı.")


def extract_offer(html: str, source_url: str = "") -> OfferResult:
    return extract_offers(html, source_url=source_url)[0]


# ---------------------------------------------------------------------------
# Fetching: Zara may answer with its own verification interstitial first.
# ---------------------------------------------------------------------------


def is_interstitial(html: str) -> bool:
    normalized = normalize_offer_text(html)
    return "bm-verify" in normalized and "_sec/verify" in normalized


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else "https://www.zara.com"


def zara_headers(url: str) -> Dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Connection": "keep-alive",
        "Referer": referer_for_url(url),
        "Upgrade-Insecure-Requests": "1",
    }


def _verification_payload(html: str) -> Dict[str, Any]:
    token_match = re.search(r'["\']bm-verify["\']\s*:\s*["\']([^"\']+)["\']', html)
    number_match = re.search(r'Number\(\s*["\'](\d+)["\']\s*\+\s*["\'](\d+)["\']\s*\)', html)
    base_match = re.search(r"var\s+i\s*=\s*(\d+)", html)
    if not token_match or not number_match or not base_match:
        raise HermesError("Zara doğrulama sayfası çözümlenemedi.")
    return {
        "bm-verify": token_match.group(1),
        "pow": int(base_match.group(1)) + int(number_match.group(1) + number_match.group(2)),
    }


def _is_usable_page(html: str) -> bool:
    normalized = normalize_offer_text(html)
    return any(
        marker in normalized
        for marker in ("application/ld+json", "product-detail-info", "product-detail-size-selector", "hasvariant", "price__amount")
    )


def _get(session: requests.Session, url: str, timeout: int) -> requests.Response:
    response = session.get(url, headers=zara_headers(url), timeout=timeout, allow_redirects=True)
    response.raise_for_status()
    return response


def fetch_zara_page(session: requests.Session, url: str, timeout: int) -> requests.Response:
    response = _get(session, url, timeout)
    html = decode_response_text(response)
    if not is_interstitial(html):
        if _is_usable_page(html):
            return response
        fresh_session = requests.Session()
        fresh_response = _get(fresh_session, url, timeout)
        fresh_html = decode_response_text(fresh_response)
        if is_interstitial(fresh_html):
            session, response, html = fresh_session, fresh_response, fresh_html
        elif _is_usable_page(fresh_html):
            return fresh_response
        else:
            raise HermesError("Zara ürün verisi eksik döndü; sayfa fiyat/beden bilgisi içermiyor.")

    headers = zara_headers(url)
    headers.update({"Content-Type": "application/json", "Origin": _origin(url), "Referer": url})
    verify_response = session.post(
        f"{_origin(url)}/_sec/verify?provider=interstitial",
        data=json.dumps(_verification_payload(html)),
        headers=headers,
        timeout=timeout,
        allow_redirects=True,
    )
    verify_response.raise_for_status()
    response = _get(session, url, timeout)
    verified_html = decode_response_text(response)
    if is_interstitial(verified_html):
        raise HermesError("Zara bot koruması nedeniyle doğrulama sayfası döndü.")
    if not _is_usable_page(verified_html):
        raise HermesError("Zara doğrulama sonrası ürün verisi eksik döndü.")
    return response


class ZaraProvider(Provider):
    site = SITE_ZARA

    def read(self, watch, ctx, outcome):
        html = read_site_html(fetch_zara_page(ctx.session, watch.url, ctx.timeout), "Zara", is_interstitial)
        offers = extract_offers(html, source_url=watch.url, size=watch.size)
        if watch.size:
            log(f"Zara beden kontrol edildi: {watch.name or watch.url} | beden={watch.size} | adet={len(offers)}")
        return offers
