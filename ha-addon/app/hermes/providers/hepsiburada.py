import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Optional
from urllib.parse import urljoin

from ..errors import HermesError
from ..logging_utils import log
from ..models import OfferResult
from ..utils import format_tl, normalize_offer_text, parse_decimal, repair_mojibake
from .base import extract_price_from_selectors, extract_title, soup_from_html

BASE_URL = "https://www.hepsiburada.com"
MIN_PRICE = Decimal("50")
MAX_PRICE = Decimal("1000000")
PRICE_RE = re.compile(r"(?<![\d.,])\d{1,3}(?:\.\d{3})*(?:,\d{2})?\s*TL", re.IGNORECASE)
PRODUCT_URL_RE = re.compile(r"/(?:[^\s'\"<>]+)-(?:p|pm)-[A-Z0-9]+", re.IGNORECASE)
PRODUCT_ID_RE = re.compile(r"-(?:p|pm)-([A-Z0-9]+)", re.IGNORECASE)
EMBEDDED_VARIANT_RE = re.compile(
    r'"sku":"(?P<sku>[^"]+)"[\s\S]{0,1800}?'
    r'"url":"(?P<url>[^"]+)"[\s\S]{0,1800}?'
    r'"merchantName":"(?P<seller>[^"]*)"',
    re.IGNORECASE,
)
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
BAD_TITLE_MARKERS = (
    "teslimat bilgisi",
    "sepete ekle",
    "kampanya",
    "peşin fiyatına",
    "pesin fiyatina",
    "fiyat:",
)
PRODUCT_CARD_CLASS_MARKERS = ("productcard", "productlistcontent")
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


@dataclass
class HepsiburadaCandidate:
    title: str
    price: Decimal
    url: str
    seller: str = "Hepsiburada"


def _absolute_url(url: str) -> str:
    return urljoin(BASE_URL, repair_mojibake(url).strip())


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", repair_mojibake(value)).strip()


def _class_text(element) -> str:
    return " ".join(str(item) for item in (element.get("class") or []))


def _product_id_from_url(url: str) -> str:
    match = PRODUCT_ID_RE.search(url or "")
    return match.group(1).upper() if match else ""


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


def _prices_from_card(card) -> list[Decimal]:
    aria_price = _price_from_aria_label(card)
    if aria_price is not None:
        return [aria_price]
    text = card.get_text(" ", strip=True)
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
        key = candidate.url or normalize_offer_text(candidate.title)
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


def _search_candidates_from_dom(soup) -> list[HepsiburadaCandidate]:
    candidates = []
    seller_lookup = _seller_lookup_from_json(soup)
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
        seller = seller_lookup.get(_product_id_from_url(url)) or _infer_seller_from_title(title)
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


def _detail_seller(lines: list[str]) -> str:
    joined = "\n".join(lines)
    match = re.search(r"Satıcı\s*:?\s*([^\n]{2,80})", joined, re.IGNORECASE)
    if match:
        seller = re.split(r"(Takip et|Satıcıya sor|Değerlendirme)", match.group(1), maxsplit=1)[0]
        seller = _clean_text(seller.replace("Resmi Satıcı", ""))
        if seller:
            return seller
    return "Hepsiburada"


def _detail_candidate(soup) -> Optional[HepsiburadaCandidate]:
    title = extract_title(soup) or "Hepsiburada ürünü"
    lines = _visible_lines_until_details(soup)
    seller = _detail_seller(lines)
    price = extract_price_from_selectors(soup, DETAIL_PRICE_SELECTORS)
    if price is None:
        line_prices = _valid_prices_from_text("\n".join(lines[:120]))
        price = min(line_prices) if line_prices else None
    if price is None:
        return None
    return HepsiburadaCandidate(title=title, price=price, url="", seller=seller)


def _log_candidates(candidates: list[HepsiburadaCandidate]) -> None:
    preview = " | ".join(f"{item.seller}={format_tl(item.price)}" for item in candidates[:8])
    log(f"Hepsiburada teklifleri: {preview}")


def extract_offer(html: str) -> OfferResult:
    soup = soup_from_html(html)
    candidates = _search_candidates_from_dom(soup)
    if not candidates:
        candidates = _search_candidates_from_json(soup)
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
