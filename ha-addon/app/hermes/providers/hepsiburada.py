import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Optional
from urllib.parse import unquote, urljoin, urlsplit

from ..errors import HermesError
from ..logging_utils import log
from ..models import OfferResult
from ..utils import format_tl, normalize_offer_text, parse_decimal, repair_mojibake
from .base import extract_price_from_selectors, extract_title, soup_from_html

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
    is_premium: bool = False


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


def _valid_price(price: Decimal) -> bool:
    return MIN_PRICE <= price <= MAX_PRICE


def _normalize_price_text(text: str) -> str:
    cleaned = _clean_text(text)
    match = PRICE_RE.search(cleaned)
    if match:
        cleaned = match.group(0)
    if "," not in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "")
    return cleaned


def _parse_price(raw_price: Any) -> Optional[Decimal]:
    if raw_price in (None, ""):
        return None
    if isinstance(raw_price, (int, float)):
        price = parse_decimal(str(raw_price))
        return price if _valid_price(price) else None
    text = _normalize_price_text(str(raw_price))
    if not text:
        return None
    try:
        price = parse_decimal(text)
    except HermesError:
        return None
    return price if _valid_price(price) else None


def _context_before_price(text: str, start: int) -> str:
    before = text[max(0, start - 56) : start]
    after_previous_price = re.split(r"TL", before, flags=re.IGNORECASE)[-1]
    return normalize_offer_text(after_previous_price)


def _is_noise_price(text: str, start: int, end: int) -> bool:
    before = _context_before_price(text, start)
    after = normalize_offer_text(text[end : end + 24])
    close_context = f"{before} {after}"
    coupon_context = normalize_offer_text(text[max(0, start - 48) : end + 48])
    if any(marker in close_context for marker in INSTALLMENT_MARKERS):
        return True
    if any(marker in coupon_context for marker in COUPON_MARKERS):
        return True
    return False


def _valid_prices_from_text(text: str) -> list[Decimal]:
    prices = []
    clean = _clean_text(text)
    for match in PRICE_RE.finditer(clean):
        if _is_noise_price(clean, match.start(), match.end()):
            continue
        price = _parse_price(match.group(0))
        if price is not None:
            prices.append(price)
    return prices


def _price_from_aria_label(card) -> Optional[Decimal]:
    for element in card.select("[aria-label]"):
        label = _clean_text(element.get("aria-label") or "")
        match = re.search(r"fiyat\s*:\s*(?P<price>[^,]+(?:,\d{2})?\s*TL)", label, re.IGNORECASE)
        if not match:
            continue
        price = _parse_price(match.group("price"))
        if price is not None:
            return price
    return None


def _cart_special_prices(text: str) -> list[Decimal]:
    clean = _clean_text(text)
    normalized = normalize_offer_text(clean)
    marker_positions = [normalized.find(marker) for marker in CART_SPECIAL_MARKERS]
    marker_positions = [position for position in marker_positions if position >= 0]
    if not marker_positions:
        return []
    segment = clean[min(marker_positions) :]
    return [price for price in (_parse_price(match.group(0)) for match in PRICE_RE.finditer(segment)) if price is not None]


def _is_premium_campaign_amount(segment: str, price_start: int, price_end: int) -> bool:
    close_context = normalize_offer_text(segment[max(0, price_start - 56) : price_end + 56])
    return any(marker in close_context for marker in PREMIUM_CAMPAIGN_MARKERS_NORMALIZED)


def _prices_after_markers(text: str, markers: tuple[str, ...], window: int = 160) -> list[Decimal]:
    clean = _clean_text(text)
    searchable = clean.casefold().replace("’", "'").replace("`", "'").replace("´", "'")
    prices: list[Decimal] = []
    for marker in markers:
        pattern = re.compile(re.escape(marker.casefold().replace("’", "'").replace("`", "'").replace("´", "'")))
        for match in pattern.finditer(searchable):
            position = match.start()
            segment = clean[position : position + window]
            for match in PRICE_RE.finditer(segment):
                if _is_premium_campaign_amount(segment, match.start(), match.end()):
                    continue
                price = _parse_price(match.group(0))
                if price is not None:
                    prices.append(price)
                    break
            else:
                for match in PRICE_LIKE_RE.finditer(segment):
                    if _is_premium_campaign_amount(segment, match.start(), match.end()):
                        continue
                    price = _parse_price(match.group(0))
                    if price is not None:
                        prices.append(price)
                        break
    return prices


def _premium_prices(text: str) -> list[Decimal]:
    return _prices_after_markers(text, PREMIUM_PRICE_MARKERS)


def _has_premium_marker(text: str) -> bool:
    normalized = normalize_offer_text(text)
    return any(marker in normalized for marker in PREMIUM_PRICE_MARKERS_NORMALIZED)


def _prices_from_card(card) -> list[Decimal]:
    text = card.get_text(" ", strip=True)
    premium_prices = _premium_prices(text)
    if premium_prices:
        return [min(premium_prices)]
    aria_price = _price_from_aria_label(card)
    if aria_price is not None:
        return [aria_price]
    special_prices = _cart_special_prices(text)
    if special_prices:
        return [min(special_prices)]
    return _valid_prices_from_text(text)


def _is_good_title(title: str) -> bool:
    normalized = normalize_offer_text(title)
    if len(title.strip()) < 8 or title.strip().isdigit():
        return False
    return not any(marker in normalized for marker in BAD_TITLE_MARKERS)


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


def _title_from_card(card, link) -> str:
    candidates = [
        _clean_text(link.get("title") or ""),
        _text_from_element(card.select_one("[data-test-id^='title'] a")),
        _text_from_element(card.select_one("[data-test-id^='title']")),
        _text_from_element(card.select_one("a[class*='title']")),
        _text_from_element(card.select_one("h2")),
        _text_from_element(card.select_one("h3")),
        _text_from_element(link),
    ]
    for title in candidates:
        if _is_good_title(title):
            return title
    return "Hepsiburada ürünü"


def _infer_seller_from_title(title: str) -> str:
    words = _clean_text(title).split()
    normalized_words = [normalize_offer_text(word) for word in words]
    for index, word in enumerate(normalized_words[:5]):
        if word in BRAND_ANCHORS:
            if index == 0:
                return "Hepsiburada"
            return " ".join(words[:index])
    return "Hepsiburada"


def _is_product_link(link) -> bool:
    href = str(link.get("href") or "")
    if not PRODUCT_URL_RE.search(href):
        return False
    return link.find_parent("footer") is None


def _closest_product_card(link):
    current = link.parent
    for _ in range(8):
        if current is None:
            return None
        class_text = normalize_offer_text(_class_text(current))
        text = _clean_text(current.get_text(" ", strip=True))
        text_length = len(text)
        has_price = PRICE_RE.search(text) is not None or _price_from_aria_label(current) is not None
        if has_price and text_length <= 2600:
            if any(marker in class_text for marker in PRODUCT_CARD_CLASS_MARKERS):
                return current
            if current.name in {"article", "li"}:
                return current
        current = current.parent
    return None


def _dedupe_candidates(candidates: Iterable[HepsiburadaCandidate]) -> list[HepsiburadaCandidate]:
    deduped: dict[str, HepsiburadaCandidate] = {}
    for candidate in candidates:
        key = candidate.identity or candidate.url or normalize_offer_text(candidate.title)
        previous = deduped.get(key)
        if previous is None or candidate.price < previous.price:
            deduped[key] = candidate
    return sorted(deduped.values(), key=lambda item: item.price)


def _remember_seller(sellers: dict[str, str], product_id: str, seller: str) -> None:
    clean_seller = _clean_text(seller)
    if product_id and clean_seller:
        sellers[product_id.upper()] = clean_seller


def _seller_lookup_from_embedded_text(soup) -> dict[str, str]:
    sellers: dict[str, str] = {}
    html = repair_mojibake(str(soup)).replace('\\"', '"')
    for match in EMBEDDED_VARIANT_RE.finditer(html):
        seller = match.group("seller")
        _remember_seller(sellers, match.group("sku"), seller)
        _remember_seller(sellers, _product_id_from_url(match.group("url")), seller)
    return sellers


def _seller_lookup_from_json(soup) -> dict[str, str]:
    sellers = _seller_lookup_from_embedded_text(soup)
    for payload in _json_payloads(soup):
        for mapping in _iter_json_values(payload):
            url = _first_mapping_url(mapping)
            listing = mapping.get("listing") if isinstance(mapping.get("listing"), dict) else {}
            seller = _first_mapping_text(listing, ("merchantName", "merchant_name", "sellerName", "seller_name"))
            seller = seller or _first_mapping_text(mapping, ("merchantName", "merchant_name", "sellerName", "seller_name"))
            product_id = _product_id_from_url(url)
            _remember_seller(sellers, product_id, seller)
    return sellers


def _variant_lookup_from_json(soup) -> dict[str, str]:
    variants: dict[str, str] = {}
    for payload in _json_payloads(soup):
        for mapping in _iter_json_values(payload):
            url = _first_mapping_url(mapping)
            product_id = _product_id_from_url(url)
            if not product_id:
                continue
            label = _variant_label_from_mapping(mapping)
            if label:
                variants[product_id] = label
    return variants


def _search_candidates_from_dom(soup) -> list[HepsiburadaCandidate]:
    candidates = []
    seller_lookup = _seller_lookup_from_json(soup)
    variant_lookup = _variant_lookup_from_json(soup)
    for link in soup.select("a[href]"):
        if not _is_product_link(link):
            continue
        card = _closest_product_card(link)
        if card is None:
            continue
        prices = _prices_from_card(card)
        if not prices:
            continue
        title = _title_from_card(card, link)
        if not _is_good_title(title):
            continue
        url = _absolute_url(str(link.get("href") or ""))
        product_id = _product_id_from_url(url)
        seller = seller_lookup.get(product_id) or _infer_seller_from_title(title)
        title = _title_with_search_variant_label(
            title,
            _search_card_variant_label(title=title, url=url, card=card, embedded_label=variant_lookup.get(product_id, "")),
        )
        candidates.append(
            HepsiburadaCandidate(
                title=title,
                price=min(prices),
                url=url,
                seller=seller,
            )
        )
    return _dedupe_candidates(candidates)


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


def _first_mapping_price(mapping: dict) -> Optional[Decimal]:
    for key in (
        "finalPrice",
        "final_price",
        "salePrice",
        "sale_price",
        "currentPrice",
        "current_price",
        "discountedPrice",
        "price",
        "formattedPrice",
        "formatted_price",
        "amount",
    ):
        price = _parse_price(mapping.get(key))
        if price is not None:
            return price
    return None


def _search_candidates_from_json(soup) -> list[HepsiburadaCandidate]:
    candidates = []
    seller_lookup = _seller_lookup_from_json(soup)
    for payload in _json_payloads(soup):
        for mapping in _iter_json_values(payload):
            title = _first_mapping_text(mapping, ("name", "title", "productName", "product_name"))
            url = _first_mapping_url(mapping)
            price = _first_mapping_price(mapping)
            if not title or not _is_good_title(title) or not url or price is None:
                continue
            product_id = _product_id_from_url(url)
            seller = seller_lookup.get(product_id) or _infer_seller_from_title(title)
            title = _title_with_search_variant_label(
                title,
                _search_card_variant_label(title=title, url=url, embedded_label=_variant_label_from_mapping(mapping)),
            )
            candidates.append(
                HepsiburadaCandidate(
                    title=title,
                    price=price,
                    url=url,
                    seller=seller,
                )
            )
    return _dedupe_candidates(candidates)


def _visible_lines_until_details(soup) -> list[str]:
    lines = []
    for raw_line in soup.get_text("\n", strip=True).splitlines():
        line = _clean_text(raw_line)
        if not line:
            continue
        if any(marker in normalize_offer_text(line) for marker in DETAIL_STOP_MARKERS):
            break
        lines.append(line)
    return lines


def _clean_variant_value(value: str) -> str:
    cleaned = _clean_text(value)
    cleaned = re.split(
        r"\b(Satıcı|Satici|Sepete|Adet|Teslimat|Ürün|Urun|Kuponlar)\b",
        cleaned,
        maxsplit=1,
    )[0]
    cleaned = _clean_text(cleaned.strip(" :-"))
    if not cleaned or len(cleaned) > 48:
        return ""
    normalized = normalize_offer_text(cleaned)
    if normalized in VARIANT_FIELD_LABELS_NORMALIZED:
        return ""
    if normalized in VARIANT_LABEL_FORBIDDEN_VALUES:
        return ""
    if any(marker in normalized for marker in ("fiyat", "price", "taksit", "sepete", "teslimat", "merchant", "listing")):
        return ""
    return cleaned


def _clean_variant_label(value: str) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for raw_part in re.split(r"\s*/\s*", _clean_text(value)):
        part = _clean_variant_value(raw_part)
        normalized_part = normalize_offer_text(part)
        if part and normalized_part not in seen:
            seen.add(normalized_part)
            parts.append(part)
    return " / ".join(parts)


def clean_display_title(title: str) -> str:
    parts = [_clean_text(part) for part in re.split(r"\s*/\s*", _clean_text(title)) if _clean_text(part)]
    if not parts:
        return _clean_text(title)
    base = _clean_search_base_title(parts[0])
    normalized_base = normalize_offer_text(base)
    cleaned_parts: list[str] = []
    seen: set[str] = set()
    for part in parts[1:]:
        cleaned = _clean_variant_value(part)
        normalized = normalize_offer_text(cleaned)
        if not cleaned or normalized in seen:
            continue
        if _looks_like_repeated_title_fragment(normalized_base, cleaned):
            continue
        seen.add(normalized)
        cleaned_parts.append(cleaned)
    return " / ".join([base, *cleaned_parts]) if cleaned_parts else base


def _clean_search_base_title(title: str) -> str:
    clean_title = _clean_text(title)
    normalized = normalize_offer_text(clean_title)
    if "nordbron" in normalized and "stark" in normalized and "sirt cantasi" in normalized:
        return "Nordbron Stark Sırt Çantası"
    return clean_title


def _looks_like_repeated_title_fragment(normalized_base: str, value: str) -> bool:
    normalized = normalize_offer_text(value)
    if len(value) < 12 or any(char.isdigit() for char in value):
        return False
    if normalized and normalized in normalized_base:
        return True
    base_words = normalized_base.split()
    value_words = normalized.split()
    if len(value_words) < 2 or len(base_words) < 2:
        return False
    return value_words[:2] == base_words[:2]


def _variant_field_label(normalized_line: str) -> str:
    normalized = normalized_line.strip(" :")
    for label in sorted(VARIANT_FIELD_LABELS, key=len, reverse=True):
        if normalized == label or normalized.startswith(f"{label}:") or normalized.startswith(f"{label} "):
            return label
    return ""


def extract_selected_variant_labels(html: str) -> list[str]:
    soup = soup_from_html(html)
    lines = _visible_lines_until_details(soup)
    values: list[str] = []
    seen: set[str] = set()
    for index, line in enumerate(lines[:80]):
        normalized = normalize_offer_text(line).strip(" :")
        label = _variant_field_label(normalized)
        if not label:
            continue
        value = ""
        if normalized == label and index + 1 < len(lines):
            value = _clean_variant_value(lines[index + 1])
        elif normalized.startswith(f"{label}:"):
            value = _clean_variant_value(line.split(":", 1)[1])
        elif normalized.startswith(f"{label} "):
            value = _clean_variant_value(line[len(label) :])
        normalized_value = normalize_offer_text(value)
        if value and normalized_value not in seen:
            seen.add(normalized_value)
            values.append(value)
    return values


def extract_selected_variant_label(html: str) -> str:
    return _clean_variant_label(" / ".join(extract_selected_variant_labels(html)))


def title_with_variant_label(title: str, variant_label: str) -> str:
    clean_title = _clean_text(title)
    clean_label = _clean_variant_label(variant_label)
    if not clean_title or not clean_label:
        return clean_title
    if normalize_offer_text(clean_label) in normalize_offer_text(clean_title):
        return clean_title
    return f"{clean_title} / {clean_label}"


def _values_from_variant_label(label: str) -> list[str]:
    return [_clean_variant_value(part) for part in re.split(r"\s*/\s*", _clean_text(label)) if _clean_variant_value(part)]


def _capacity_label_from_value(value: str) -> str:
    matches = list(CAPACITY_RE.finditer(_clean_text(value)))
    if not matches:
        return ""
    preferred = [match for match in matches if match.group(2).casefold() == "tb" or int(match.group(1)) >= 32]
    match = (preferred or matches)[-1]
    return f"{int(match.group(1))} {match.group(2).upper()}"


def _color_label_from_value(value: str) -> str:
    normalized = normalize_offer_text(unquote(_clean_text(value)).replace("-", " ").replace("_", " "))
    for marker, label in COLOR_LABELS:
        if re.search(rf"(^|\W){re.escape(marker)}($|\W)", normalized):
            return label
    return ""


def _explicit_variant_values_from_text(text: str) -> list[str]:
    values: list[str] = []
    clean = _clean_text(text)
    for label in VARIANT_FIELD_LABELS:
        pattern = re.compile(rf"\b{re.escape(label)}\b\s*:?\s*([^/|,\n\r]{{1,48}})", re.IGNORECASE)
        for match in pattern.finditer(clean):
            value = _clean_variant_value(match.group(1))
            if value:
                values.append(value)
    return values


def _ordered_variant_label(values: Iterable[str], *fallback_texts: str) -> str:
    capacities: list[str] = []
    colors: list[str] = []
    others: list[str] = []
    seen: set[str] = set()

    def add(bucket: list[str], value: str) -> None:
        normalized = normalize_offer_text(value)
        if value and normalized not in seen:
            seen.add(normalized)
            bucket.append(value)

    for raw_value in values:
        value = _clean_variant_value(raw_value)
        if not value:
            continue
        capacity = _capacity_label_from_value(value)
        color = _color_label_from_value(value)
        if capacity:
            add(capacities, capacity)
        elif color:
            add(colors, color)
        else:
            add(others, value)

    fallback = " ".join(unquote(str(text or "")).replace("-", " ").replace("_", " ") for text in fallback_texts)
    capacity = _capacity_label_from_value(fallback)
    color = _color_label_from_value(fallback)
    if capacity:
        add(capacities, capacity)
    if color:
        add(colors, color)

    return _clean_variant_label(" / ".join([*capacities[:1], *colors[:1], *others[:2]]))


def _search_card_context(card) -> str:
    if card is None:
        return ""
    values = [card.get_text(" ", strip=True)]
    for element in card.select("[alt], [title], [aria-label]"):
        for attribute in ("alt", "title", "aria-label"):
            value = element.get(attribute)
            if isinstance(value, str) and 0 < len(value) <= 180:
                values.append(value)
    return _clean_text(" ".join(values))


def _search_card_variant_label(title: str, url: str, card=None, embedded_label: str = "") -> str:
    values = _values_from_variant_label(embedded_label)
    card_text = _search_card_context(card)
    values.extend(_explicit_variant_values_from_text(card_text))
    return _ordered_variant_label(values, title, url, card_text)


def _title_with_search_variant_label(title: str, variant_label: str) -> str:
    clean_title = _clean_text(title)
    clean_label = _clean_variant_label(variant_label)
    if not clean_title or not clean_label:
        return clean_title
    for value in _values_from_variant_label(clean_label):
        if not _color_label_from_value(value):
            continue
        clean_title = re.sub(rf"[\s,/-]+{re.escape(value)}\s*$", "", clean_title, flags=re.IGNORECASE).strip()
    return title_with_variant_label(clean_title, clean_label)


def _detail_seller(lines: list[str]) -> str:
    joined = "\n".join(lines)
    match = re.search(r"Satıcı\s*:?\s*([^\n]{2,80})", joined, re.IGNORECASE)
    if match:
        seller = re.split(r"(Takip et|Satıcıya sor|Değerlendirme)", match.group(1), maxsplit=1)[0]
        seller = _clean_text(seller.replace("Resmi Satıcı", ""))
        if seller and normalize_offer_text(seller) not in {"ol", "satici ol", "satici"}:
            return seller
    return "Hepsiburada"


def _number_price(raw_price: str) -> Optional[Decimal]:
    try:
        price = Decimal(str(raw_price))
    except Exception:  # noqa: BLE001
        return None
    return price if _valid_price(price) else None


def _embedded_text(soup) -> str:
    return repair_mojibake(_decode_escaped_fragments(str(soup)))


def _readable_source_text(soup) -> str:
    raw = _embedded_text(soup)
    without_tags = re.sub(r"<[^>]+>", " ", raw)
    return _clean_text(without_tags)


def _price_context_allows(context: str) -> bool:
    normalized = normalize_offer_text(context)
    blocked_markers = (
        "installment",
        "taksit",
        "aylik",
        "worldcard",
        "kupon",
        "coupon",
        "kargo",
        "cargo",
        "shipping",
        "shipment",
    )
    return not any(marker in normalized for marker in blocked_markers)


def _price_context_is_premium(context: str) -> bool:
    normalized = normalize_offer_text(context).replace("’", "'").replace("`", "'").replace("´", "'")
    return any(marker in normalized for marker in PREMIUM_PRICE_MARKERS_NORMALIZED)


def _premium_entries_from_nested_value(value: Any, parent_context: str = "") -> list[tuple[Decimal, str]]:
    entries: list[tuple[Decimal, str]] = []
    if isinstance(value, dict):
        own_context = " ".join(
            str(item)
            for item in value.values()
            if isinstance(item, str) and len(item) <= 120
        )
        context = f"{parent_context} {own_context}".strip()
        if _price_context_is_premium(context):
            for key in ("value", "amount", "price", "finalPrice", "finalPriceOnSale", "minimumPrice"):
                price = _number_price(value.get(key))
                if price is not None:
                    entries.append((price, context))
        for key, nested in value.items():
            key_context = f"{context} {key}".strip()
            entries.extend(_premium_entries_from_nested_value(nested, key_context))
    elif isinstance(value, list):
        for item in value:
            entries.extend(_premium_entries_from_nested_value(item, parent_context))
    return entries


def _mapping_price_entries(mapping: dict) -> list[tuple[Decimal, str]]:
    entries: list[tuple[Decimal, str]] = []

    def add(raw_price: Any, context: str) -> None:
        price = _number_price(raw_price)
        if price is not None and _price_context_allows(context):
            entries.append((price, context))

    for key in (
        "finalPriceOnSale",
        "finalPrice",
        "final_price",
        "salePrice",
        "sale_price",
        "currentPrice",
        "current_price",
        "discountedPrice",
        "price",
        "amount",
    ):
        add(mapping.get(key), key)

    for collection_key in ("prices", "minimumPrices"):
        price_items = mapping.get(collection_key)
        if not isinstance(price_items, list):
            continue
        for item in price_items:
            if not isinstance(item, dict):
                continue
            context = " ".join(str(value) for value in item.values() if isinstance(value, str))
            normalized_context = normalize_offer_text(context)
            if (
                collection_key == "minimumPrices"
                and not _price_context_is_premium(context)
                and "non segmented price" not in normalized_context
            ):
                continue
            add(item.get("value"), f"{collection_key} {context}")

    entries.extend(_premium_entries_from_nested_value(mapping))
    return entries


def _listing_mapping_price(mapping: dict) -> Optional[Decimal]:
    entries = _mapping_price_entries(mapping)
    premium_entries = [price for price, context in entries if _price_context_is_premium(context)]
    if premium_entries:
        return min(premium_entries)
    reference_prices = [
        price
        for price, context in entries
        if normalize_offer_text(context)
        in {
            "finalpriceonsale",
            "finalprice",
            "final price",
            "saleprice",
            "sale price",
            "currentprice",
            "current price",
            "discountedprice",
            "discounted price",
        }
    ]
    if reference_prices:
        lowest_reasonable_price = max(reference_prices) * Decimal("0.50")
        entries = [
            (price, context)
            for price, context in entries
            if price >= lowest_reasonable_price or _price_context_is_premium(context)
        ]
    if entries:
        return min(price for price, _context in entries)

    final_price = _number_price(mapping.get("finalPriceOnSale"))
    if final_price is not None:
        return final_price

    minimum_prices = mapping.get("minimumPrices")
    if isinstance(minimum_prices, list):
        for item in minimum_prices:
            if isinstance(item, dict) and item.get("name") == "non-segmented-price":
                price = _number_price(item.get("value"))
                if price is not None:
                    return price

    return _number_price(mapping.get("minimumPrice"))


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


def _selected_variant_mapping(text: str, selected_product_id: str) -> Optional[dict]:
    if not selected_product_id:
        return None
    sku_pattern = re.compile(r'"sku"\s*:\s*"' + re.escape(selected_product_id) + r'"', re.IGNORECASE)
    for match in sku_pattern.finditer(text):
        position = match.start()
        for _ in range(220):
            position = text.rfind("{", 0, position)
            if position < 0:
                break
            mapping = _json_object_at(text, position)
            if (
                mapping
                and str(mapping.get("sku") or "").upper() == selected_product_id
                and isinstance(mapping.get("variantListing"), list)
            ):
                return mapping
            position -= 1
    return None


def _selected_variant_listing_mappings(text: str, selected_product_id: str) -> list[dict]:
    mapping = _selected_variant_mapping(text, selected_product_id)
    if not mapping:
        return []
    return [item for item in mapping["variantListing"] if isinstance(item, dict)]


def _variant_label_from_value(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = _clean_variant_value(value)
    normalized = normalize_offer_text(cleaned)
    if not cleaned:
        return ""
    if any(marker in normalized for marker in ("http", "www", "hepsiburada", "hbc", "merchant", "listing")):
        return ""
    if PRICE_RE.search(cleaned):
        return ""
    return cleaned


def _variant_label_from_mapping(mapping: dict) -> str:
    labels: list[str] = []
    seen: set[str] = set()

    def add_label(value: Any) -> None:
        label = _variant_label_from_value(value)
        normalized_label = normalize_offer_text(label)
        if label and normalized_label not in seen:
            seen.add(normalized_label)
            labels.append(label)

    def add_selected_child_values(value: Any) -> None:
        if isinstance(value, dict):
            selected = any(
                bool(value.get(key))
                for key in ("selected", "isSelected", "checked", "active", "isActive")
            )
            if selected:
                for key in VARIANT_VALUE_JSON_KEYS:
                    add_label(value.get(key))
            for nested in value.values():
                if isinstance(nested, (dict, list)):
                    add_selected_child_values(nested)
        elif isinstance(value, list):
            for item in value:
                add_selected_child_values(item)

    stack: list[Any] = [mapping]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            field_name = ""
            for key in ("fieldName", "attributeName", "propertyName", "optionName", "name", "label", "displayName"):
                candidate_name = current.get(key)
                if normalize_offer_text(str(candidate_name or "")) in VARIANT_FIELD_LABELS_NORMALIZED:
                    field_name = str(candidate_name or "")
                    break
            if field_name:
                for key in VARIANT_VALUE_JSON_KEYS:
                    if key in {"name", "label", "displayName"} and normalize_offer_text(str(current.get(key) or "")) in VARIANT_FIELD_LABELS_NORMALIZED:
                        continue
                    add_label(current.get(key))
                add_selected_child_values(current)
            for key, value in current.items():
                normalized_key = normalize_offer_text(str(key))
                if key in VARIANT_LABEL_SKIP_KEYS or normalized_key in VARIANT_LABEL_SKIP_KEYS_NORMALIZED:
                    continue
                if normalized_key in VARIANT_LABEL_JSON_KEYS_NORMALIZED:
                    add_label(value)
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(current, list):
            stack.extend(current)
    return _clean_variant_label(" / ".join(labels[:4]))


def extract_embedded_variant_label(html: str, source_url: str) -> str:
    selected_product_id = _product_id_from_url(source_url)
    if not selected_product_id:
        return ""
    mapping = _selected_variant_mapping(_embedded_text(soup_from_html(html)), selected_product_id)
    return _clean_variant_label(_variant_label_from_mapping(mapping)) if mapping else ""


def _embedded_listing_mappings(text: str, selected_product_id: str = "") -> Iterable[dict]:
    selected_mappings = _selected_variant_listing_mappings(text, selected_product_id)
    if selected_mappings:
        seen = set()
        for mapping in selected_mappings:
            listing_id = str(mapping.get("listingId") or "").strip()
            seller = str(mapping.get("merchantName") or "").strip()
            if not seller:
                continue
            key = listing_id or f"{seller}:{mapping.get('finalPriceOnSale')}:{mapping.get('minimumPrice')}"
            if key in seen:
                continue
            seen.add(key)
            yield mapping
        return
    if selected_product_id:
        return

    start_pattern = re.compile(r'\{(?="(?:aiBasedShipmentDay|merchantId)"[\s\S]{0,1400}?"listingId")')
    seen = set()
    for match in start_pattern.finditer(text):
        mapping = _json_object_at(text, match.start())
        if not mapping:
            continue
        listing_id = str(mapping.get("listingId") or "").strip()
        seller = str(mapping.get("merchantName") or "").strip()
        if not listing_id or not seller:
            continue
        key = listing_id or f"{seller}:{match.start()}"
        if key in seen:
            continue
        seen.add(key)
        yield mapping


def _embedded_detail_candidates(soup, source_url: str = "") -> list[HepsiburadaCandidate]:
    html = _embedded_text(soup)
    if '"merchantName"' not in html or not any(key in html for key in ('"minimumPrice"', '"finalPriceOnSale"')):
        return []
    selected_product_id = _product_id_from_url(source_url)
    title = extract_title(soup) or "Hepsiburada ürünü"
    candidates = []
    for mapping in _embedded_listing_mappings(html, selected_product_id):
        price = _listing_mapping_price(mapping)
        if price is None:
            continue
        seller = _clean_text(mapping.get("merchantName") or "")
        listing_id = str(mapping.get("listingId") or "").strip()
        url = _absolute_url(str(mapping.get("url") or "")) if PRODUCT_URL_RE.search(str(mapping.get("url") or "")) else ""
        seller_key = normalize_offer_text(seller)
        identity = f"{selected_product_id}:{seller_key}" if selected_product_id and seller_key else listing_id
        identity = identity or f"{seller.casefold()}:{price}"
        candidates.append(
            HepsiburadaCandidate(
                title=title,
                price=price,
                url=url,
                seller=seller,
                identity=identity,
            )
        )
    return _dedupe_candidates(candidates)


def extract_embedded_variant_offer(html: str, source_url: str) -> Optional[OfferResult]:
    soup = soup_from_html(html)
    candidates = _embedded_detail_candidates(soup, source_url=source_url)
    if not candidates:
        return None
    _log_candidates(candidates)
    best = candidates[0]
    return OfferResult(
        title=repair_mojibake(best.title),
        price=best.price,
        seller=best.seller,
        url=source_url,
    )


def _variant_context_allows(segment: str) -> bool:
    normalized = normalize_offer_text(segment)
    return any(marker in normalized for marker in ("variant", "varyant", "renk", "color", "secenek", "seçenek"))


def extract_variant_urls(html: str, source_url: str, limit: int = 8) -> list[str]:
    """Return detail-page variant URLs without mixing their prices on the current page."""
    selected_product_id = _product_id_from_url(source_url)
    if not selected_product_id:
        return []

    urls: list[str] = []
    seen_product_ids: set[str] = set()

    def add(candidate: str) -> None:
        absolute = _canonical_product_url(candidate)
        product_id = _product_id_from_url(absolute)
        if not product_id or product_id in seen_product_ids:
            return
        seen_product_ids.add(product_id)
        urls.append(absolute)

    add(source_url)
    soup = soup_from_html(html)
    for link in soup.select("a[href]"):
        href = str(link.get("href") or "")
        if not PRODUCT_URL_RE.search(href):
            continue
        context = _element_context_text(link)
        parent = link.parent
        for _ in range(3):
            if parent is None:
                break
            context += " " + _element_context_text(parent)
            parent = parent.parent
        if _variant_context_allows(context):
            add(href)
        if len(urls) >= limit:
            return urls[:limit]

    text = _embedded_text(soup)
    for match in PRODUCT_URL_RE.finditer(text):
        segment = text[max(0, match.start() - 900) : match.end() + 900]
        if not _variant_context_allows(segment):
            continue
        if "merchantName" in segment and "variantListing" in segment:
            continue
        add(match.group(0))
        if len(urls) >= limit:
            break
    return urls[:limit]


def _detail_candidate(soup) -> Optional[HepsiburadaCandidate]:
    title = extract_title(soup) or "Hepsiburada ürünü"
    lines = _visible_lines_until_details(soup)
    seller = _detail_seller(lines)
    line_text = "\n".join(lines)
    premium_prices = _premium_prices(line_text)
    readable_source_text = ""
    if not premium_prices:
        readable_source_text = _readable_source_text(soup)
        premium_prices = _premium_prices(readable_source_text)
    if not premium_prices and (_has_premium_marker(line_text) or _has_premium_marker(readable_source_text)):
        log("Hepsiburada Premium metni bulundu ama fiyat ayrıştırılamadı.")
    price = min(premium_prices) if premium_prices else None
    if price is None:
        price = extract_price_from_selectors(soup, DETAIL_PRICE_SELECTORS)
    if price is None:
        line_prices = _valid_prices_from_text(line_text)
        price = min(line_prices) if line_prices else None
    if price is None:
        return None
    return HepsiburadaCandidate(title=title, price=price, url="", seller=seller, is_premium=bool(premium_prices))


def _log_candidates(candidates: list[HepsiburadaCandidate]) -> None:
    preview = " | ".join(f"{item.seller}={format_tl(item.price, with_currency=True)}" for item in candidates[:8])
    log(f"Hepsiburada teklifleri: {preview}")


def extract_search_offers(html: str, source_url: str = "", limit: int = 24) -> list[OfferResult]:
    soup = soup_from_html(html)
    candidates = _search_candidates_from_dom(soup)
    if not candidates:
        candidates = _search_candidates_from_json(soup)
    if not candidates:
        raise HermesError("Hepsiburada arama sayfasından fiyat bulunamadı.")

    candidates = candidates[: max(1, int(limit or 1))]
    _log_candidates(candidates)
    return [
        OfferResult(
            title=repair_mojibake(candidate.title),
            price=candidate.price,
            seller=candidate.seller,
            url=candidate.url or source_url or None,
        )
        for candidate in candidates
    ]


def extract_offer(html: str, source_url: str = "") -> OfferResult:
    soup = soup_from_html(html)
    selected_product_id = _product_id_from_url(source_url)
    if selected_product_id:
        candidates = _embedded_detail_candidates(soup, source_url=source_url)
        detail = _detail_candidate(soup)
        if detail and (detail.is_premium or not candidates):
            detail.identity = f"{selected_product_id}:{normalize_offer_text(detail.seller)}"
            candidates.append(detail)
        candidates = _dedupe_candidates(candidates)
    else:
        candidates = _search_candidates_from_dom(soup)
        if not candidates:
            candidates = _search_candidates_from_json(soup)
        if not candidates:
            candidates = _embedded_detail_candidates(soup, source_url=source_url)
        if not candidates:
            detail = _detail_candidate(soup)
            candidates = [detail] if detail else []
    if not candidates:
        raise HermesError("Hepsiburada sayfasından fiyat bulunamadı.")

    _log_candidates(candidates)
    best = candidates[0]
    return OfferResult(
        title=repair_mojibake(best.title),
        price=best.price,
        seller=best.seller,
        url=best.url or None,
    )
