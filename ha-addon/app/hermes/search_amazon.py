import re
from typing import Any, List, Optional

from bs4 import BeautifulSoup

from .constants import AMAZON_BASE_URL
from .errors import HermesError
from .models import SearchResultItem
from .utils import canonical_amazon_product_url, normalize_offer_text, normalize_text, parse_decimal, repair_mojibake

AMAZON_SEARCH_CARD_SELECTORS = [
    "div[data-component-type='s-search-result']",
    "div[data-asin][data-component-type]",
    "div[data-asin]",
    ".s-result-item",
]

AMAZON_CARD_TITLE_SELECTORS = [
    "h2 a span",
    "h2 span",
    "a.a-link-normal h2 span",
    "[data-cy='title-recipe'] span",
    "a.a-link-normal span",
]

AMAZON_CARD_PRICE_SELECTORS = [
    ".a-price .a-offscreen",
    "span.a-price > span.a-offscreen",
    ".a-price-whole",
]

AMAZON_SECONDARY_OFFER_SELECTORS = [
    "[data-cy='secondary-offer-recipe']",
    "[data-cy='secondary-offer']",
    ".puis-secondary-offer",
    ".puis-see-details-content",
]

AMAZON_SECONDARY_OFFER_PRICE_PATTERN = re.compile(
    r"diger\s+satin\s+alma\s+secenekleri\s+"
    r"(?P<price>\d{1,3}(?:\.\d{3})*,\d{2}|\d+(?:,\d{2})?)\s*tl"
)

AMAZON_SEARCH_STOP_SECTION_MARKERS = (
    "yardima mi ihtiyaciniz var",
    "baktiginiz urunlere gore belirlenen urunler",
    "tarama gecmisinizdeki urunleri goruntuleyen musteriler ayrica sunlari da goruntuledi",
)


def _extract_card_title(card: BeautifulSoup) -> Optional[str]:
    for selector in AMAZON_CARD_TITLE_SELECTORS:
        element = card.select_one(selector)
        if element:
            text = element.get_text(" ", strip=True)
            if text:
                return repair_mojibake(text)
    for attr_name in ("aria-label", "title", "alt"):
        element = card.find(attrs={attr_name: True})
        if element:
            text = str(element.get(attr_name, "")).strip()
            if text:
                return repair_mojibake(text)
    return None


def _extract_price_after_secondary_offer_text(text: str):
    normalized = normalize_offer_text(text)
    if "diger satin alma secenekleri" not in normalized or "ikinci el" not in normalized:
        return None
    match = AMAZON_SECONDARY_OFFER_PRICE_PATTERN.search(normalized)
    if not match:
        return None
    try:
        return parse_decimal(match.group("price"))
    except HermesError:
        return None


def _extract_secondary_offer_price(card: BeautifulSoup):
    for selector in AMAZON_SECONDARY_OFFER_SELECTORS:
        for element in card.select(selector):
            price = _extract_price_after_secondary_offer_text(element.get_text(" ", strip=True))
            if price is not None:
                return price
    return _extract_price_after_secondary_offer_text(card.get_text(" ", strip=True))


def _extract_card_price(card: BeautifulSoup):
    secondary = _extract_secondary_offer_price(card)
    if secondary is not None:
        return secondary
    for selector in AMAZON_CARD_PRICE_SELECTORS:
        element = card.select_one(selector)
        if element:
            text = element.get_text(" ", strip=True)
            if text:
                try:
                    return parse_decimal(text)
                except HermesError:
                    continue
    text = card.get_text(" ", strip=True)
    match = re.search(r"(\d{1,3}(?:\.\d{3})*,\d{2})\s*TL", text)
    if match:
        return parse_decimal(match.group(1))
    match = re.search(r"(\d+(?:,\d{2})?)\s*TL", text)
    if match:
        return parse_decimal(match.group(1))
    return None


def _extract_card_url(card: BeautifulSoup, fallback_asin: str = ""):
    link = (
        card.select_one("h2 a[href]")
        or card.select_one("a[href*='/dp/']")
        or card.select_one("a[href*='/gp/product/']")
        or card.select_one("a[href]")
    )
    if not link:
        return None
    href = str(link.get("href", "")).strip()
    if not href:
        return None
    return canonical_amazon_product_url(href, fallback_asin)


def _is_stop_section_text(value: str) -> bool:
    normalized = normalize_offer_text(value)
    return any(marker in normalized for marker in AMAZON_SEARCH_STOP_SECTION_MARKERS)


def _find_stop_marker(soup: BeautifulSoup):
    for text_node in soup.find_all(string=True):
        if _is_stop_section_text(str(text_node)):
            return text_node
    return None


def _filter_cards_before_stop(cards: List[Any], soup: BeautifulSoup):
    marker = _find_stop_marker(soup)
    if marker is None:
        return cards
    after_marker_ids = {id(el) for el in marker.next_elements if getattr(el, "name", None)}
    return [card for card in cards if id(card) not in after_marker_ids]


def extract_results(html: str, max_items_to_scan: int) -> List[SearchResultItem]:
    soup = BeautifulSoup(html, "html.parser")
    cards: List[Any] = []
    for selector in AMAZON_SEARCH_CARD_SELECTORS:
        found = _filter_cards_before_stop(soup.select(selector), soup)
        if found:
            cards = found
            break

    results: List[SearchResultItem] = []
    seen_urls = set()
    for card in cards:
        asin = str(card.get("data-asin", "")).strip() or ""
        if card.name == "div" and card.has_attr("data-asin") and not asin:
            continue
        if len(results) >= max_items_to_scan:
            break
        title = _extract_card_title(card)
        price = _extract_card_price(card)
        url = _extract_card_url(card, asin)
        if not title or price is None or not url or url in seen_urls:
            continue
        seen_urls.add(url)
        results.append(SearchResultItem(title=title, url=url, price=price))

    if not results:
        raise HermesError("Amazon arama sonuc sayfasinda okunabilir urun bulunamadi.")
    return results


def dedupe_results(results: List[SearchResultItem]) -> List[SearchResultItem]:
    deduped = {}
    for item in results:
        existing = deduped.get(item.url)
        if existing is None or item.price < existing.price:
            deduped[item.url] = item
    return list(deduped.values())


def filter_matching_results(results: List[SearchResultItem], product_name: str) -> List[SearchResultItem]:
    needle = normalize_text(product_name)
    return [item for item in results if needle in normalize_text(item.title)]
