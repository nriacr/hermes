"""Ben Gurme'nin Shopify urun ve stok verisini okuyan izole saglayici.

Ben Gurme varyantlari Shopify'nin yapilandirilmis urun verisinde tutar. Bu
saglayici, gorunur dugme metinlerini fiyat yerine kullanmak yerine bu veriyi
okur; her stoktaki gramaj ayri teklif olarak doner.
"""

import json
import re
from decimal import Decimal
from typing import Any, Iterable, List

from ..errors import HermesError, OutOfStockHermesError
from ..models import OfferResult
from ..utils import normalize_offer_text, parse_decimal, repair_mojibake
from .base import (
    extract_image,
    extract_jsonld_product,
    extract_price_from_meta,
    extract_price_from_scripts,
    extract_price_from_selectors,
    extract_title,
    iter_json_objects,
    soup_from_html,
)
from .size_availability import size_matches


FALLBACK_PRICE_SELECTORS = [
    "[data-product-price]",
    ".product__price",
    ".product-price",
    ".price-item--sale",
    ".price-item",
    "[itemprop='price']",
]
PRODUCT_ASSIGNMENT_PATTERNS = (
    re.compile(r"(?:window\.)?(?:meta\.)?product\s*=\s*", re.IGNORECASE),
    re.compile(r"window\.__PRODUCT__\s*=\s*", re.IGNORECASE),
)
OUT_OF_STOCK_MARKERS = ("tukendi", "tükendi", "sold out", "outofstock", "out of stock")
DEFAULT_VARIANT_NAMES = {"default title", "varsayilan baslik", "varsayılan başlık"}


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", repair_mojibake(str(value or ""))).strip()


def _product_identity(payload: dict) -> str:
    return "|".join(str(payload.get(key) or "") for key in ("id", "handle", "title"))


def _walk_product_candidates(payload: Any) -> Iterable[dict]:
    for item in iter_json_objects(payload):
        variants = item.get("variants") if isinstance(item, dict) else None
        if isinstance(variants, list) and variants:
            yield item


def _decode_json_after(html: str, start: int) -> Any:
    try:
        payload, _ = json.JSONDecoder().raw_decode(html[start:].lstrip())
    except json.JSONDecodeError:
        return None
    return payload


def _product_payloads(html: str) -> List[dict]:
    payloads: List[dict] = []
    stripped = html.strip()
    if stripped.startswith(("{", "[")):
        try:
            raw_payload = json.loads(stripped)
        except json.JSONDecodeError:
            raw_payload = None
        if raw_payload is not None:
            payloads.extend(_walk_product_candidates(raw_payload))

    soup = soup_from_html(html)
    for script in soup.find_all("script"):
        raw = (script.string or script.get_text(" ", strip=True) or "").strip()
        if not raw:
            continue
        if "json" in str(script.get("type", "")).casefold():
            try:
                payloads.extend(_walk_product_candidates(json.loads(raw)))
            except json.JSONDecodeError:
                continue
        for pattern in PRODUCT_ASSIGNMENT_PATTERNS:
            for match in pattern.finditer(raw):
                decoded = _decode_json_after(raw, match.end())
                if decoded is not None:
                    payloads.extend(_walk_product_candidates(decoded))

    unique: List[dict] = []
    seen = set()
    for payload in payloads:
        key = _product_identity(payload) or str(payload)
        if key in seen:
            continue
        seen.add(key)
        unique.append(payload)
    return unique


def _variant_price(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float, Decimal)):
        # Shopify's numeric JSON prices are stored in the smallest currency unit.
        return Decimal(str(value)) / Decimal("100")
    text = _clean_text(value)
    if not text:
        return None
    try:
        return parse_decimal(text)
    except HermesError:
        return None


def _variant_is_available(variant: dict) -> bool:
    available = variant.get("available")
    if isinstance(available, bool):
        return available
    if available not in (None, ""):
        return normalize_offer_text(available) not in {"false", "0", "no"}
    inventory = variant.get("inventory_quantity")
    if isinstance(inventory, (int, float)):
        return inventory > 0
    return True


def _variant_label(variant: dict) -> str:
    title = _clean_text(variant.get("public_title") or variant.get("title"))
    if normalize_offer_text(title) not in DEFAULT_VARIANT_NAMES:
        return title
    parts = [_clean_text(variant.get(f"option{index}")) for index in range(1, 4)]
    parts = [part for part in parts if part and normalize_offer_text(part) not in DEFAULT_VARIANT_NAMES]
    return " / ".join(parts)


def _title_for_variant(product: dict, variant: dict) -> str:
    title = _clean_text(product.get("title") or product.get("name") or "Ben Gurme ürünü")
    label = _variant_label(variant)
    if label and normalize_offer_text(label) not in normalize_offer_text(title):
        return f"{title} / {label}"
    return title


def _offers_from_payload(product: dict, source_url: str, requested_size: str) -> List[OfferResult]:
    offers: List[OfferResult] = []
    candidates = [variant for variant in product.get("variants", []) if isinstance(variant, dict)]
    if not candidates:
        return offers

    matching_variants = [
        variant
        for variant in candidates
        if not requested_size
        or size_matches(_variant_label(variant), requested_size)
        or any(size_matches(variant.get(f"option{index}"), requested_size) for index in range(1, 4))
    ]
    title = _clean_text(product.get("title") or product.get("name") or "Ben Gurme ürünü")
    if requested_size and not matching_variants:
        raise OutOfStockHermesError(f"Ben Gurme beden bulunamadı: {requested_size}", title, source_url)
    if requested_size and not any(_variant_is_available(variant) for variant in matching_variants):
        raise OutOfStockHermesError(f"Ben Gurme beden stokta değil: {requested_size}", title, source_url)

    for variant in matching_variants:
        if not _variant_is_available(variant):
            continue
        price = _variant_price(variant.get("price"))
        if price is None:
            continue
        offers.append(
            OfferResult(
                title=_title_for_variant(product, variant),
                price=price,
                seller="Ben Gurme",
                url=source_url,
            )
        )
    return offers


def _fallback_offer(html: str, source_url: str) -> OfferResult:
    soup = soup_from_html(html)
    title, jsonld_price = extract_jsonld_product(soup)
    title = title or extract_title(soup) or "Ben Gurme ürünü"
    normalized = normalize_offer_text(soup.get_text(" ", strip=True))
    if any(marker in normalized for marker in OUT_OF_STOCK_MARKERS):
        raise OutOfStockHermesError("Ben Gurme ürünü stokta değil.", title, source_url)
    for price in (
        jsonld_price,
        extract_price_from_meta(soup),
        extract_price_from_selectors(soup, FALLBACK_PRICE_SELECTORS),
        extract_price_from_scripts(html),
    ):
        if price is not None:
            return OfferResult(title=title, price=price, seller="Ben Gurme", url=source_url, image_url=extract_image(soup))
    raise HermesError("Ben Gurme sayfasından fiyat veya varyant verisi bulunamadı.")


def extract_offers(html: str, source_url: str = "", size: str = "") -> List[OfferResult]:
    """Return each in-stock Shopify variant as a separate Hermes offer."""
    image_url = extract_image(soup_from_html(html))
    requested_size = _clean_text(size)
    payloads = _product_payloads(html)
    if not payloads:
        return [_fallback_offer(html, source_url)]

    offers: List[OfferResult] = []
    out_of_stock: OutOfStockHermesError | None = None
    seen = set()
    for payload in payloads:
        try:
            candidates = _offers_from_payload(payload, source_url, requested_size)
        except OutOfStockHermesError as exc:
            out_of_stock = exc
            continue
        for offer in candidates:
            identity = (normalize_offer_text(offer.title), str(offer.price))
            if identity not in seen:
                seen.add(identity)
                offers.append(offer)

    if offers:
        for offer in offers:
            offer.image_url = image_url
        return offers
    if out_of_stock is not None:
        raise out_of_stock

    title = _clean_text(payloads[0].get("title") or payloads[0].get("name") or "Ben Gurme ürünü")
    raise OutOfStockHermesError("Ben Gurme ürünü stokta değil.", title, source_url)


def extract_offer(html: str, source_url: str = "") -> OfferResult:
    offers = extract_offers(html, source_url=source_url)
    return min(offers, key=lambda offer: offer.price)
