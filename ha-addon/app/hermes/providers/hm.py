import json
import re
from decimal import Decimal
from typing import Any, Iterable, List
from urllib.parse import urljoin

from ..errors import HermesError
from ..models import OfferResult
from ..utils import normalize_offer_text, parse_decimal, repair_mojibake
from .base import extract_jsonld_product, extract_price_from_meta, iter_json_objects, soup_from_html

OUT_OF_STOCK_MARKERS = (
    "benzer urunler",
    "benzer ürünler",
    "stokta yok",
    "tukendi",
    "tükendi",
    "out of stock",
    "sold out",
    "unavailable",
    "not available",
)


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", repair_mojibake(str(value or ""))).strip()


def _strip_parenthetical(value: str) -> str:
    return _clean(re.sub(r"\([^)]*\)", " ", value))


def _size_tokens(value: str) -> set[str]:
    normalized = normalize_offer_text(_strip_parenthetical(value))
    ignored = {"eu", "us", "uk", "yas", "yaş", "yil", "yıl", "beden", "size"}
    return {token for token in re.findall(r"[a-z0-9]+", normalized) if token not in ignored}


def _size_matches(variant_size: str, requested_size: str) -> bool:
    requested = normalize_offer_text(_strip_parenthetical(requested_size))
    variant = normalize_offer_text(_strip_parenthetical(variant_size))
    if not requested:
        return True
    if requested == variant:
        return True
    requested_tokens = _size_tokens(requested)
    variant_tokens = _size_tokens(variant)
    return bool(requested_tokens) and requested_tokens.issubset(variant_tokens)


def _is_available(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, dict):
        for key in ("available", "inStock", "isAvailable", "stock", "stockLevel", "quantity"):
            if key in value:
                return _is_available(value.get(key))
        text = " ".join(_clean(item) for item in value.values())
        normalized = normalize_offer_text(text)
        return not any(marker in normalized for marker in OUT_OF_STOCK_MARKERS)
    if isinstance(value, list):
        return any(_is_available(item) for item in value)
    normalized = normalize_offer_text(str(value))
    if normalized in {"true", "instock", "in stock", "available"}:
        return True
    if normalized in {"false", "0", "outofstock", "soldout", "unavailable"}:
        return False
    return not any(marker in normalized for marker in OUT_OF_STOCK_MARKERS)


def _json_payloads(html: str) -> Iterable[Any]:
    soup = soup_from_html(html)
    for script in soup.find_all("script"):
        raw = script.string or script.get_text(" ", strip=True)
        if not raw:
            continue
        raw = raw.strip()
        if script.get("type") and "json" in str(script.get("type")).casefold():
            try:
                yield json.loads(raw)
                continue
            except json.JSONDecodeError:
                pass
        for pattern in (
            r"window\.__HM_STATE__\s*=\s*(\{.*?\})\s*;",
            r"window\.__PRODUCT_DATA__\s*=\s*(\{.*?\})\s*;",
            r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;",
        ):
            match = re.search(pattern, raw, re.S)
            if not match:
                continue
            try:
                yield json.loads(match.group(1))
            except json.JSONDecodeError:
                continue


def _field(data: dict, names: tuple[str, ...]) -> Any:
    for name in names:
        if name in data and data.get(name) not in (None, ""):
            return data.get(name)
    return None


def _price_from_value(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return parse_decimal(str(value))
        except HermesError:
            return None
    if isinstance(value, str):
        try:
            return parse_decimal(value)
        except HermesError:
            return None
    if isinstance(value, list):
        candidates = [_price_from_value(item) for item in value]
        candidates = [item for item in candidates if item is not None]
        return min(candidates) if candidates else None
    if isinstance(value, dict):
        for key in (
            "formattedValue",
            "formattedPrice",
            "currentPrice",
            "salePrice",
            "redPrice",
            "whitePrice",
            "price",
            "value",
            "amount",
        ):
            price = _price_from_value(value.get(key))
            if price is not None:
                return price
    return None


def _price_from_product(data: dict) -> Decimal | None:
    for key in ("price", "priceInfo", "currentPrice", "salePrice", "redPrice", "whitePrice", "offers"):
        price = _price_from_value(data.get(key))
        if price is not None:
            return price
    return None


def _size_name(size_data: Any) -> str:
    if isinstance(size_data, str):
        return _strip_parenthetical(size_data)
    if not isinstance(size_data, dict):
        return ""
    value = _field(size_data, ("name", "size", "sizeName", "label", "displayName", "value"))
    return _strip_parenthetical(_clean(value))


def _size_available(size_data: Any) -> bool:
    if isinstance(size_data, str):
        return True
    if not isinstance(size_data, dict):
        return False
    for key in ("available", "inStock", "isAvailable", "stock", "stockLevel", "quantity", "availability"):
        if key in size_data:
            return _is_available(size_data.get(key))
    text = " ".join(_clean(value) for value in size_data.values())
    return _is_available(text)


def _sizes_from_product(data: dict) -> list[tuple[str, bool]]:
    raw_sizes = _field(data, ("sizes", "sizeVariants", "variantSizes", "skus", "stock", "availability"))
    sizes: list[tuple[str, bool]] = []
    if isinstance(raw_sizes, list):
        for size_data in raw_sizes:
            size_name = _size_name(size_data)
            if size_name:
                sizes.append((size_name, _size_available(size_data)))
    elif isinstance(raw_sizes, dict):
        for key, value in raw_sizes.items():
            if isinstance(value, dict):
                size_name = _size_name(value) or _strip_parenthetical(str(key))
                sizes.append((size_name, _size_available(value)))
            else:
                sizes.append((_strip_parenthetical(str(key)), _is_available(value)))
    return sizes


def _variants_from_product(data: dict) -> list[dict]:
    variants: list[dict] = []
    for key in ("colors", "colourVariants", "colorVariants", "articles", "variants", "products"):
        raw_variants = data.get(key)
        if isinstance(raw_variants, list):
            variants.extend(item for item in raw_variants if isinstance(item, dict))
        elif isinstance(raw_variants, dict):
            variants.extend(item for item in raw_variants.values() if isinstance(item, dict))
    return variants


def _product_like_objects(html: str) -> list[dict]:
    objects: list[dict] = []
    for payload in _json_payloads(html):
        for item in iter_json_objects(payload):
            if not isinstance(item, dict):
                continue
            has_name = bool(_field(item, ("name", "title", "productName")))
            has_price = _price_from_product(item) is not None
            has_sizes = bool(_sizes_from_product(item))
            has_variants = bool(_variants_from_product(item))
            if has_name and (has_price or has_sizes or has_variants):
                objects.append(item)
    return objects


def _absolute_url(raw_url: str, source_url: str) -> str:
    return urljoin(source_url or "https://www2.hm.com/", _clean(raw_url))


def _display_title(product_name: str, color: str, size: str) -> str:
    parts = [_clean(product_name)]
    for value in (color, size):
        clean = _clean(value)
        if clean and normalize_offer_text(clean) not in {normalize_offer_text(item) for item in parts}:
            parts.append(clean)
    return " / ".join(parts)


def _offers_from_data(data: dict, source_url: str, requested_size: str = "") -> list[OfferResult]:
    base_name = _clean(_field(data, ("name", "title", "productName")) or "H&M ürünü")
    base_color = _clean(_field(data, ("color", "colorName", "colour", "colourName", "swatchColorName")))
    base_price = _price_from_product(data)
    base_url = _absolute_url(_clean(_field(data, ("url", "link", "productUrl")) or source_url), source_url)
    products = [data] + _variants_from_product(data)
    offers: list[OfferResult] = []

    for product in products:
        name = _clean(_field(product, ("name", "title", "productName")) or base_name)
        color = _clean(_field(product, ("color", "colorName", "colour", "colourName", "swatchColorName")) or base_color)
        price = _price_from_product(product) or base_price
        url = _absolute_url(_clean(_field(product, ("url", "link", "productUrl")) or base_url), source_url)
        sizes = _sizes_from_product(product)
        if price is None:
            continue
        if not sizes and not requested_size:
            offers.append(OfferResult(title=_display_title(name, color, ""), price=price, seller="H&M", url=url))
            continue
        for size_name, available in sizes:
            if requested_size and not _size_matches(size_name, requested_size):
                continue
            if not available:
                continue
            offers.append(
                OfferResult(
                    title=_display_title(name, color, size_name),
                    price=price,
                    seller="H&M",
                    url=url,
                )
            )
    return offers


def _fallback_offer(html: str, source_url: str) -> OfferResult | None:
    soup = soup_from_html(html)
    title, jsonld_price = extract_jsonld_product(soup)
    price = jsonld_price or extract_price_from_meta(soup)
    if price is None:
        return None
    title = title or _clean(soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else "H&M ürünü")
    color_element = soup.find(string=re.compile(r"\b(Renk|Colour|Color)\b", re.I))
    color = ""
    if color_element:
        color = re.sub(r"^(Renk|Colour|Color)\s*:?\s*", "", _clean(color_element), flags=re.I)
    return OfferResult(title=_display_title(title, color, ""), price=price, seller="H&M", url=source_url)


def extract_offers(html: str, source_url: str = "", size: str = "") -> List[OfferResult]:
    requested_size = _clean(size)
    offers: List[OfferResult] = []
    seen: set[tuple[str, str, str]] = set()
    matched_size = False
    matched_available = False

    for product in _product_like_objects(html):
        product_offers = _offers_from_data(product, source_url, requested_size)
        if requested_size and _sizes_from_product(product):
            for size_name, available in _sizes_from_product(product):
                if _size_matches(size_name, requested_size):
                    matched_size = True
                    matched_available = matched_available or available
        for offer in product_offers:
            key = (normalize_offer_text(offer.title), str(offer.price), normalize_offer_text(offer.url or ""))
            if key in seen:
                continue
            seen.add(key)
            offers.append(offer)

    if offers:
        return offers
    if requested_size:
        if not matched_size:
            raise HermesError(f"H&M beden bulunamadı: {requested_size}")
        if not matched_available:
            raise HermesError(f"H&M beden stokta değil: {requested_size}")
        raise HermesError(f"H&M beden fiyatı bulunamadı: {requested_size}")

    fallback = _fallback_offer(html, source_url)
    if fallback:
        return [fallback]
    raise HermesError("H&M sayfasından fiyat/stok bilgisi bulunamadı.")


def extract_offer(html: str, source_url: str = "") -> OfferResult:
    return extract_offers(html, source_url=source_url)[0]
