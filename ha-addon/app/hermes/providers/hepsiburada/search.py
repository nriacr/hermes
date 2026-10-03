"""Hepsiburada search and listing pages: product cards from the page and from embedded JSON."""

import re
from decimal import Decimal

from ...errors import EmptySearchResultsHermesError
from ...models import OfferResult
from ...utils import normalize_offer_text, repair_mojibake
from ..base import soup_from_html
from .common import (
    BAD_TITLE_MARKERS,
    BRAND_ANCHORS,
    EMBEDDED_VARIANT_RE,
    PRICE_RE,
    PRODUCT_CARD_CLASS_MARKERS,
    PRODUCT_URL_RE,
    HepsiburadaCandidate,
    _absolute_url,
    _class_text,
    _clean_text,
    _dedupe_candidates,
    _first_mapping_text,
    _first_mapping_url,
    _iter_json_values,
    _json_payloads,
    _log_candidates,
    _product_id_from_url,
    _text_from_element,
)
from .prices import (
    _cart_special_prices,
    _first_mapping_price,
    _premium_prices,
    _price_from_aria_label,
    _valid_prices_from_text,
)
from .variants import (
    _clean_variant_label,
    _color_label_from_value,
    _explicit_variant_values_from_text,
    _ordered_variant_label,
    _values_from_variant_label,
    _variant_label_from_mapping,
    title_with_variant_label,
)


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


def extract_search_offers(html: str, source_url: str = "", limit: int = 24) -> list[OfferResult]:
    soup = soup_from_html(html)
    candidates = _search_candidates_from_dom(soup)
    if not candidates:
        candidates = _search_candidates_from_json(soup)
    if not candidates:
        raise EmptySearchResultsHermesError("Hepsiburada arama sayfasında ürün bulunamadı.")

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
