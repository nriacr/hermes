import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .errors import HermesError
from .models import SearchResultItem
from .providers.amazon_common import (
    extract_verified_secondary_offer_price,
    has_explicit_used_offer_evidence,
)
from .utils import canonical_amazon_product_url, make_amazon_absolute_url, normalize_offer_text, parse_decimal, repair_mojibake

AMAZON_SEARCH_CARD_SELECTORS = [
    "div.s-main-slot div[data-component-type='s-search-result'][data-asin]",
    "div.s-main-slot div[data-cel-widget^='search_result_'][data-asin]",
    "div[data-component-type='s-search-result'][data-asin]",
]

AMAZON_CARD_TITLE_SELECTORS = [
    "h2 a span",
    "h2 span",
    "a.a-link-normal h2 span",
    "[data-cy='title-recipe'] span",
    "a.a-link-normal span",
]

AMAZON_SEARCH_STOP_SECTION_MARKERS = (
    "all departments icindeki sonuclar",
    "all departments icindeki sonuclar gosteriliyor",
    "yardima mi ihtiyaciniz var",
    "baktiginiz urunlere gore belirlenen urunler",
    "tarama gecmisinizdeki urunleri goruntuleyen musteriler ayrica sunlari da goruntuledi",
)

# These parameters choose a concrete Amazon variation or offer. Keep them in
# search-result links so different colors are not merged into one row.
AMAZON_SEARCH_RESULT_VARIATION_PARAMS = {"smid", "psc", "th"}


@dataclass
class AmazonSearchCandidate:
    title: str
    url: str
    price: Optional[Decimal] = None
    is_warehouse: bool = False


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


def _extract_primary_card_price(card: BeautifulSoup):
    """Return the visible main price, never a strike-through or hidden price."""
    for price_element in card.select(".a-price"):
        classes = set(price_element.get("class") or [])
        if "a-text-price" in classes or price_element.get("data-a-strike") == "true":
            continue
        if _is_hidden_element(price_element):
            continue
        offscreen = price_element.select_one(".a-offscreen")
        if offscreen and not _is_hidden_element(offscreen):
            try:
                return parse_decimal(offscreen.get_text(" ", strip=True))
            except HermesError:
                pass
        whole = price_element.select_one(".a-price-whole")
        if not whole or _is_hidden_element(whole):
            continue
        fraction = price_element.select_one(".a-price-fraction")
        whole_text = re.sub(r"[^\d.]", "", whole.get_text("", strip=True))
        fraction_text = re.sub(r"\D", "", fraction.get_text("", strip=True)) if fraction else "00"
        if not whole_text:
            continue
        try:
            return parse_decimal(f"{whole_text},{(fraction_text or '00')[:2].ljust(2, '0')}")
        except HermesError:
            continue
    return None


def _extract_card_offers(card: BeautifulSoup, source_is_warehouse_search: bool = False):
    """Return normal and explicitly-marked used offers without merging them.

    Amazon can show the main new-product price and an "other buying options"
    used price on the same result card. The primary price must win for the
    normal offer; the secondary block is a separate Warehouse offer.
    """
    offers = []
    primary = _extract_primary_card_price(card)
    # Accept Amazon's visible used-offer wording even when the site omits its
    # usual secondary-offer wrapper. Both required phrases must still appear.
    secondary = extract_verified_secondary_offer_price(card, include_container_fallback=True)
    card_text = card.get_text(" ", strip=True)

    # A Warehouse search can still include ordinary fallback cards. Mark a
    # card's primary price as used only when that very card explicitly says it
    # is a second-hand offer and there is no separate used-offer block. If a
    # separate block exists, the primary price remains a new offer.
    primary_is_warehouse = bool(
        source_is_warehouse_search
        and has_explicit_used_offer_evidence(card_text)
        # In a Depot-only search Amazon can render the same used price as both
        # the visible card price and the secondary-offer text. It is one used
        # offer, not a normal-plus-used pair.
        and (secondary is None or secondary == primary)
    )
    if primary is not None:
        offers.append((primary, primary_is_warehouse))
    # A same-price secondary label is not a distinct deal. Keeping it would
    # create a duplicate DEPO row next to the identical new offer.
    if secondary is not None and secondary != primary:
        offers.append((secondary, True))
    return offers


def _extract_card_offer(card: BeautifulSoup):
    """Compatibility helper returning the normal offer when one exists."""
    offers = _extract_card_offers(card)
    return offers[0] if offers else (None, False)


def _extract_card_price(card: BeautifulSoup):
    """Keep the historical price helper available for focused parser tests."""
    return _extract_card_offer(card)[0]


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
    absolute_url = make_amazon_absolute_url(href)
    canonical_url = canonical_amazon_product_url(absolute_url, fallback_asin)
    parsed = urlsplit(absolute_url)
    variation_params = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key in AMAZON_SEARCH_RESULT_VARIATION_PARAMS
    ]
    if not variation_params:
        return canonical_url
    return urlunsplit(("https", "www.amazon.com.tr", urlsplit(canonical_url).path, urlencode(variation_params), ""))


def _is_hidden_element(element: Any) -> bool:
    current = element
    while current is not None and getattr(current, "name", None):
        classes = set(current.get("class", []) or [])
        style = str(current.get("style", "")).casefold()
        if (
            current.get("aria-hidden") == "true"
            or "aok-hidden" in classes
            or "display:none" in style.replace(" ", "")
            or "visibility:hidden" in style.replace(" ", "")
        ):
            return True
        current = current.parent
    return False


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
    # Compare document order rather than DOM nesting: Amazon can place the
    # fallback heading inside a wrapper separate from the result cards.
    before_marker_ids = {id(el) for el in marker.previous_elements if getattr(el, "name", None)}
    return [card for card in cards if id(card) in before_marker_ids]


def _match_phrase(value: str) -> str:
    normalized = normalize_offer_text(value)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def title_matches_keyword(title: str, keyword: str) -> bool:
    phrase = _match_phrase(keyword)
    if not phrase:
        return False
    normalized_title = _match_phrase(title)
    return f" {phrase} " in f" {normalized_title} "


def title_matches_any_keyword(title: str, keywords: List[str]) -> bool:
    return any(title_matches_keyword(title, keyword) for keyword in keywords)


def extract_result_candidates(
    html: str,
    max_items_to_scan: int,
    primary_is_warehouse: bool = False,
) -> List[AmazonSearchCandidate]:
    soup = BeautifulSoup(html, "html.parser")
    cards: List[Any] = []
    for selector in AMAZON_SEARCH_CARD_SELECTORS:
        found = _filter_cards_before_stop(soup.select(selector), soup)
        if found:
            cards = found
            break

    candidates: List[AmazonSearchCandidate] = []
    seen_offers = set()
    scanned_cards = 0
    for card in cards:
        asin = str(card.get("data-asin", "")).strip() or ""
        if not asin or _is_hidden_element(card):
            continue
        if scanned_cards >= max_items_to_scan:
            break
        title = _extract_card_title(card)
        url = _extract_card_url(card, asin)
        if not title or not url:
            continue
        scanned_cards += 1
        card_offers = _extract_card_offers(card, primary_is_warehouse)
        if not card_offers:
            # A missing card price may be completed from the product page.
            # The card's source category alone must never turn that later
            # normal price into a DEPO offer.
            card_offers = [(None, False)]
        for price, is_warehouse in card_offers:
            # Normal and used prices for the same ASIN are deliberately
            # distinct; duplicate cards within the same condition are not.
            offer_key = (url, is_warehouse)
            if offer_key in seen_offers:
                continue
            seen_offers.add(offer_key)
            candidates.append(
                AmazonSearchCandidate(title=title, url=url, price=price, is_warehouse=is_warehouse)
            )

    if not candidates:
        raise HermesError("Amazon arama sonuç sayfasında okunabilir ürün bulunamadı.")
    return candidates


def dedupe_results(results: List[SearchResultItem]) -> List[SearchResultItem]:
    deduped = {}
    for item in results:
        key = (item.url, bool(item.is_warehouse))
        existing = deduped.get(key)
        if existing is None or item.price < existing.price:
            deduped[key] = item
    return list(deduped.values())


def filter_matching_results(results: List[SearchResultItem], product_name: str) -> List[SearchResultItem]:
    return [item for item in results if title_matches_keyword(item.title, product_name)]
