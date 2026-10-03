"""Shared Hepsiburada pieces: site constants, the candidate record, text, URL and JSON helpers."""

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Optional
from urllib.parse import urljoin, urlsplit

from ...logging_utils import log
from ...utils import format_tl, normalize_offer_text, repair_mojibake

BASE_URL = "https://www.hepsiburada.com"


MIN_PRICE = Decimal("50")


MAX_PRICE = Decimal("1000000")


PRICE_RE = re.compile(
    r"(?<![\d.,])(?:\d{1,3}(?:\.\d{3})+|\d{1,7})(?:,\d{2})?\s*TL",
    re.IGNORECASE,
)


PRICE_LIKE_RE = re.compile(r"(?<![\d.,])(?:\d{1,3}(?:\.\d{3})+|\d{4,7})(?:,\d{2})?(?![\d.,])")


PRODUCT_URL_RE = re.compile(r"/(?:[^\s'\"<>]+)-(?:p|pm)-[A-Z0-9]+", re.IGNORECASE)


PRODUCT_ID_RE = re.compile(r"-(?:p|pm)-([A-Z0-9]+)", re.IGNORECASE)


EMBEDDED_VARIANT_RE = re.compile(
    r'"sku":"(?P<sku>[^"]+)"[\s\S]{0,1800}?'
    r'"url":"(?P<url>[^"]+)"[\s\S]{0,1800}?'
    r'"merchantName":"(?P<seller>[^"]*)"',
    re.IGNORECASE,
)


VARIANT_LABEL_JSON_KEYS = (
    "variantName",
    "variantValue",
    "variantValueName",
    "selectedValue",
    "optionValue",
    "displayName",
    "label",
    "text",
    "name",
    "value",
    "color",
    "colour",
    "renk",
    "capacity",
    "storage",
    "depolama",
)


VARIANT_VALUE_JSON_KEYS = (
    "variantName",
    "variantValue",
    "variantValueName",
    "selectedValue",
    "optionValue",
    "displayName",
    "label",
    "text",
    "name",
    "value",
    "color",
    "colour",
    "renk",
    "capacity",
    "storage",
    "depolama",
)


VARIANT_LABEL_JSON_KEYS_NORMALIZED = {normalize_offer_text(item) for item in VARIANT_LABEL_JSON_KEYS}


DETAIL_PRICE_SELECTORS = [
    "[data-test-id='price-current-price']",
    "[data-test-id='price-current-price'] span",
    "[data-test-id='price']",
    "[itemprop='price']",
    "#offering-price",
    ".product-price",
    ".price",
]


DETAIL_STOP_MARKERS = ("urun bilgileri", "urun aciklamasi", "degerlendirmeler")


INSTALLMENT_MARKERS = ("peşin fiyatına", "pesin fiyatina", "taksit", " x ")


COUPON_MARKERS = ("kupon", "hepsipara", "kazan")


CART_SPECIAL_MARKERS = ("sepete özel", "sepete ozel")


PREMIUM_PRICE_MARKERS = (
    "premium ile",
    "premium'a özel fiyat",
    "premium'a ozel fiyat",
    "premium’a özel fiyat",
    "premium’a ozel fiyat",
    "premiuma özel fiyat",
    "premiuma ozel fiyat",
    "premium özel fiyat",
    "premium ozel fiyat",
)


PREMIUM_PRICE_MARKERS_NORMALIZED = {normalize_offer_text(item) for item in PREMIUM_PRICE_MARKERS}


PREMIUM_CAMPAIGN_MARKERS_NORMALIZED = {
    "indirim",
    "kazanc",
    "kazancimi",
    "hepsipara",
    "kupon",
    "koruma paketi",
    "paketlerinde",
}


BAD_TITLE_MARKERS = (
    "teslimat bilgisi",
    "sepete ekle",
    "kampanya",
    "peşin fiyatına",
    "pesin fiyatina",
    "fiyat:",
)


PRODUCT_CARD_CLASS_MARKERS = ("productcard", "productlistcontent")


NON_PRODUCT_PATH_MARKERS = ("degerlendirme", "yorum", "review")


VARIANT_FIELD_LABELS = (
    "renk",
    "kapasite",
    "hafiza",
    "hafıza",
    "depolama",
    "dahili hafiza",
    "dahili hafıza",
    "ram",
    "beden",
    "boyut",
    "numara",
    "secenek",
    "seçenek",
)


VARIANT_FIELD_LABELS_NORMALIZED = {normalize_offer_text(item) for item in VARIANT_FIELD_LABELS}


BRAND_ANCHORS = (
    "apple",
    "samsung",
    "govee",
    "philips",
    "tapo",
    "xiaomi",
    "huawei",
    "lenovo",
    "asus",
    "acer",
    "lg",
    "sony",
    "anker",
    "roborock",
    "dyson",
    "bosch",
    "siemens",
)


VARIANT_LABEL_SKIP_KEYS = {
    "variantListing",
    "listing",
    "listings",
    "prices",
    "minimumPrices",
    "price",
    "finalPrice",
    "finalPriceOnSale",
    "minimumPrice",
    "merchantName",
    "merchantId",
    "listingId",
    "url",
    "productUrl",
}


VARIANT_LABEL_SKIP_KEYS_NORMALIZED = {normalize_offer_text(item) for item in VARIANT_LABEL_SKIP_KEYS}


VARIANT_LABEL_FORBIDDEN_VALUES = {
    "non segmented price",
    "segmented price",
    "minimum price",
    "final price",
    "merchant",
    "merchant name",
    "listing",
    "listing id",
}


COLOR_LABELS = (
    ("antrasit", "Antrasit"),
    ("lacivert", "Lacivert"),
    ("mint yesili", "Mint Yeşili"),
    ("gumus", "Gümüş"),
    ("gri", "Gri"),
    ("mavi", "Mavi"),
    ("siyah", "Siyah"),
    ("beyaz", "Beyaz"),
    ("yesil", "Yeşil"),
    ("kirmizi", "Kırmızı"),
    ("pembe", "Pembe"),
    ("mor", "Mor"),
    ("sari", "Sarı"),
    ("turuncu", "Turuncu"),
    ("bej", "Bej"),
)


CAPACITY_RE = re.compile(r"(?<!\d)(?:\d+\s*/\s*)?(\d+)\s*(GB|TB)\b", re.IGNORECASE)


@dataclass
class HepsiburadaCandidate:
    title: str
    price: Decimal
    url: str
    seller: str = "Hepsiburada"
    identity: str = ""
    has_preferred_price: bool = False


def _absolute_url(url: str) -> str:
    return urljoin(BASE_URL, repair_mojibake(url).strip())


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", repair_mojibake(value)).strip()


def _decode_escaped_fragments(value: str) -> str:
    text = str(value or "")

    def replace_unicode(match: re.Match) -> str:
        return chr(int(match.group(1), 16))

    def replace_hex(match: re.Match) -> str:
        return chr(int(match.group(1), 16))

    text = re.sub(r"\\u([0-9a-fA-F]{4})", replace_unicode, text)
    text = re.sub(r"\\x([0-9a-fA-F]{2})", replace_hex, text)
    return (
        text.replace("\\/", "/")
        .replace('\\"', '"')
        .replace("\\'", "'")
        .replace("\\n", " ")
        .replace("\\r", " ")
        .replace("\\t", " ")
    )


def _class_text(element) -> str:
    return " ".join(str(item) for item in (element.get("class") or []))


def _product_id_from_url(url: str) -> str:
    match = PRODUCT_ID_RE.search(url or "")
    return match.group(1).upper() if match else ""


def is_product_url(url: str) -> bool:
    return bool(_product_id_from_url(url))


def product_id_from_url(url: str) -> str:
    return _product_id_from_url(url)


def _canonical_product_url(candidate: str) -> str:
    cleaned = repair_mojibake(candidate).strip()
    if not cleaned:
        return ""
    if cleaned.startswith("/"):
        cleaned = _absolute_url(cleaned)
    parsed = urlsplit(cleaned)
    if parsed.scheme not in {"http", "https"} or parsed.netloc.casefold() not in {
        "www.hepsiburada.com",
        "hepsiburada.com",
    }:
        return ""
    path = parsed.path
    normalized_path = normalize_offer_text(path)
    if any(marker in normalized_path for marker in NON_PRODUCT_PATH_MARKERS):
        return ""
    match = PRODUCT_URL_RE.search(path)
    if not match:
        return ""
    return _absolute_url(match.group(0))


def _text_from_element(element) -> str:
    if not element:
        return ""
    value = element.get("title") or element.get("aria-label") or element.get_text(" ", strip=True)
    return _clean_text(value)


def _element_context_text(element) -> str:
    if not element:
        return ""
    values = [
        element.get("title") or "",
        element.get("aria-label") or "",
        element.get("data-test-id") or "",
        _class_text(element),
        element.get_text(" ", strip=True),
    ]
    return _clean_text(" ".join(str(value) for value in values if value))


def _dedupe_candidates(candidates: Iterable[HepsiburadaCandidate]) -> list[HepsiburadaCandidate]:
    deduped: dict[str, HepsiburadaCandidate] = {}
    for candidate in candidates:
        key = candidate.identity or candidate.url or normalize_offer_text(candidate.title)
        previous = deduped.get(key)
        if previous is None or candidate.price < previous.price:
            deduped[key] = candidate
    return sorted(deduped.values(), key=lambda item: item.price)


def _iter_json_values(value: Any):
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            yield current
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)


def _json_payloads(soup) -> list[Any]:
    payloads = []
    for script in soup.find_all("script"):
        raw = script.string or script.get_text("", strip=True)
        text = str(raw or "").strip()
        if not text or "{" not in text:
            continue
        if text.startswith("{") or text.startswith("["):
            try:
                payloads.append(json.loads(text))
                continue
            except json.JSONDecodeError:
                pass
        start = text.find("{")
        end = text.rfind("}") + 1
        if 0 <= start < end:
            try:
                payloads.append(json.loads(text[start:end]))
            except json.JSONDecodeError:
                continue
    return payloads


def _first_mapping_text(mapping: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return _clean_text(value)
    return ""


def _first_mapping_url(mapping: dict) -> str:
    direct = _first_mapping_text(mapping, ("url", "productUrl", "product_url", "link", "href"))
    if direct and PRODUCT_URL_RE.search(direct):
        return _absolute_url(direct)
    for value in mapping.values():
        if isinstance(value, str) and PRODUCT_URL_RE.search(value):
            return _absolute_url(PRODUCT_URL_RE.search(value).group(0))
    return ""


def _embedded_text(soup) -> str:
    return repair_mojibake(_decode_escaped_fragments(str(soup)))


def _readable_source_text(soup) -> str:
    raw = _embedded_text(soup)
    without_tags = re.sub(r"<[^>]+>", " ", raw)
    return _clean_text(without_tags)


def _json_object_at(text: str, start: int) -> Optional[dict]:
    if start < 0 or start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    value = json.loads(text[start : index + 1])
                except json.JSONDecodeError:
                    return None
                return value if isinstance(value, dict) else None
    return None


def _log_candidates(candidates: list[HepsiburadaCandidate]) -> None:
    preview = " | ".join(f"{item.seller}={format_tl(item.price, with_currency=True)}" for item in candidates[:8])
    log(f"Hepsiburada teklifleri: {preview}")
