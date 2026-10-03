"""Hepsiburada product detail pages: the lowest seller offer from the page and embedded listings."""

import re
from typing import Iterable, Optional

from ...errors import HermesError
from ...logging_utils import log
from ...models import OfferResult
from ...utils import normalize_offer_text, repair_mojibake
from ..base import extract_price_from_selectors, extract_title, soup_from_html
from .common import (
    DETAIL_PRICE_SELECTORS,
    PRODUCT_URL_RE,
    HepsiburadaCandidate,
    _absolute_url,
    _clean_text,
    _dedupe_candidates,
    _embedded_text,
    _json_object_at,
    _log_candidates,
    _product_id_from_url,
    _readable_source_text,
)
from .prices import (
    _cart_special_prices,
    _has_premium_marker,
    _listing_mapping_price,
    _premium_prices,
    _valid_prices_from_text,
)
from .search import _search_candidates_from_dom, _search_candidates_from_json
from .variants import _selected_variant_listing_mappings, _visible_lines_until_details


def _detail_seller(lines: list[str]) -> str:
    joined = "\n".join(lines)
    match = re.search(r"Satıcı\s*:?\s*([^\n]{2,80})", joined, re.IGNORECASE)
    if match:
        seller = re.split(r"(Takip et|Satıcıya sor|Değerlendirme)", match.group(1), maxsplit=1)[0]
        seller = _clean_text(seller.replace("Resmi Satıcı", ""))
        if seller and normalize_offer_text(seller) not in {"ol", "satici ol", "satici"}:
            return seller
    return "Hepsiburada"


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


def _detail_candidate(soup) -> Optional[HepsiburadaCandidate]:
    title = extract_title(soup) or "Hepsiburada ürünü"
    lines = _visible_lines_until_details(soup)
    seller = _detail_seller(lines)
    line_text = "\n".join(lines)
    cart_special_prices = _cart_special_prices(line_text)
    premium_prices = _premium_prices(line_text)
    readable_source_text = ""
    if not cart_special_prices or not premium_prices:
        readable_source_text = _readable_source_text(soup)
        if not cart_special_prices:
            cart_special_prices = _cart_special_prices(readable_source_text)
        if not premium_prices:
            premium_prices = _premium_prices(readable_source_text)
    if not premium_prices and (_has_premium_marker(line_text) or _has_premium_marker(readable_source_text)):
        log("Hepsiburada Premium metni bulundu ama fiyat ayrıştırılamadı.")
    preferred_prices = [*cart_special_prices, *premium_prices]
    price = min(preferred_prices) if preferred_prices else None
    if price is None:
        price = extract_price_from_selectors(soup, DETAIL_PRICE_SELECTORS)
    if price is None:
        line_prices = _valid_prices_from_text(line_text)
        price = min(line_prices) if line_prices else None
    if price is None:
        return None
    return HepsiburadaCandidate(
        title=title,
        price=price,
        url="",
        seller=seller,
        has_preferred_price=bool(preferred_prices),
    )


def extract_offer(html: str, source_url: str = "") -> OfferResult:
    soup = soup_from_html(html)
    selected_product_id = _product_id_from_url(source_url)
    if selected_product_id:
        candidates = _embedded_detail_candidates(soup, source_url=source_url)
        detail = _detail_candidate(soup)
        if detail and (detail.has_preferred_price or not candidates):
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
