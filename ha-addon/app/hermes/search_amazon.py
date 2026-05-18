import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, List, Optional

from bs4 import BeautifulSoup

from .errors import HermesError
from .models import SearchResultItem
from .providers.amazon_common import extract_secondary_offer_price
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

AMAZON_SEARCH_STOP_SECTION_MARKERS = (
    "yardima mi ihtiyaciniz var",
    "baktiginiz urunlere gore belirlenen urunler",
    "tarama gecmisinizdeki urunleri goruntuleyen musteriler ayrica sunlari da goruntuledi",
)


@dataclass
class AmazonSearchCandidate:
    title: str
    url: str
    price: Optional[Decimal] = None


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


def _extract_card_price(card: BeautifulSoup):
    secondary = extract_secondary_offer_price(card)
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


def _keyword_words(keyword: str) -> List[str]:
    normalized = normalize_text(keyword)
    return [word for word in re.split(r"\s+", normalized) if word]


def title_matches_keyword(title: str, keyword: str) -> bool:
    words = _keyword_words(keyword)
    if not words:
        return False
    normalized_title = normalize_text(title)
    return all(word in normalized_title for word in words)


def title_matches_any_keyword(title: str, keywords: List[str]) -> bool:
    return any(title_matches_keyword(title, keyword) for keyword in keywords)


def extract_result_candidates(html: str, max_items_to_scan: int) -> List[AmazonSearchCandidate]:
    soup = BeautifulSoup(html, "html.parser")
    cards: List[Any] = []
    for selector in AMAZON_SEARCH_CARD_SELECTORS:
        found = _filter_cards_before_stop(soup.select(selector), soup)
        if found:
            cards = found
            break

    candidates: List[AmazonSearchCandidate] = []
    seen_urls = set()
    for card in cards:
        asin = str(card.get("data-asin", "")).strip() or ""
        if card.name == "div" and card.has_attr("data-asin") and not asin:
            continue
        if len(candidates) >= max_items_to_scan:
            break
        title = _extract_card_title(card)
        url = _extract_card_url(card, asin)
        if not title or not url or url in seen_urls:
            continue
        seen_urls.add(url)
        candidates.append(AmazonSearchCandidate(title=title, url=url, price=_extract_card_price(card)))

    if not candidates:
        raise HermesError("Amazon arama sonuç sayfasında okunabilir ürün bulunamadı.")
    return candidates


def extract_results(html: str, max_items_to_scan: int) -> List[SearchResultItem]:
    results = [
        SearchResultItem(title=item.title, url=item.url, price=item.price)
        for item in extract_result_candidates(html, max_items_to_scan)
        if item.price is not None
    ]
    if not results:
        raise HermesError("Amazon arama sonuç sayfasında okunabilir fiyat bulunamadı.")
    return results


def dedupe_results(results: List[SearchResultItem]) -> List[SearchResultItem]:
    deduped = {}
    for item in results:
        existing = deduped.get(item.url)
        if existing is None or item.price < existing.price:
            deduped[item.url] = item
    return list(deduped.values())


def filter_matching_results(results: List[SearchResultItem], product_name: str) -> List[SearchResultItem]:
    return [item for item in results if title_matches_keyword(item.title, product_name)]
