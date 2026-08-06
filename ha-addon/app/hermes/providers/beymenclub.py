"""Beymen Club'a ozel fiyat ve beden stok okuyucu.

Network benzeri sepet kampanyalarini onceleyerek, normal fiyatla karismasini
engeller. Bu kurallar baska saglayicilardan tamamen bagimsiz tutulur.
"""

import json
import re

from ..errors import HermesError, OutOfStockHermesError
from ..models import OfferResult
from ..utils import parse_decimal
from .base import (
    extract_image,
    extract_jsonld_product,
    extract_price_from_meta,
    extract_price_from_scripts,
    extract_price_from_selectors,
    extract_title,
    soup_from_html,
)
from .size_availability import requested_size_state, size_matches

BEYMENCLUB_SELECTORS = [
    ".product-detail__price",
    ".product-price",
    ".price-current",
    ".current-price",
    ".new-price",
    ".discount-price",
    ".sales-price",
    "[data-testid='price']",
    "[itemprop='price']",
]

PRICE_PATTERN = r"(?:\d{1,3}(?:\.\d{3})+(?:,\d{2})?|\d+(?:,\d{2})?)"

BEYMENCLUB_BASKET_PATTERNS = [
    re.compile(
        rf"(?<!\d)\d+\s*ve\s*(?:üzeri|uzeri)(?:\s+adet)?(?:\s+(?:için|icin))?\s*"
        rf"(?P<price>{PRICE_PATTERN})\s*tl",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        rf"\d+\s*ve\s*(?:üzeri|uzeri).*?sepette\s*"
        rf"(?P<price>{PRICE_PATTERN})\s*tl",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        rf"sepette\s*(?P<price>{PRICE_PATTERN})\s*tl",
        re.IGNORECASE | re.DOTALL,
    ),
]


def _extract_basket_price(soup):
    """Return the cheapest explicit basket campaign price, if present."""
    text = soup.get_text(" ", strip=True)
    candidates = []
    for pattern in BEYMENCLUB_BASKET_PATTERNS:
        for match in pattern.finditer(text):
            try:
                candidates.append(parse_decimal(match.group("price")))
            except HermesError:
                continue
    return min(candidates) if candidates else None


def extract_product_id(html: str) -> int | None:
    """Read the product id embedded by Beymen Club's server-rendered page."""
    match = re.search(r"\bBEYMEN\.productMain\s*=\s*", html)
    if not match:
        return None
    try:
        payload, _ = json.JSONDecoder().raw_decode(html[match.end() :].lstrip())
    except json.JSONDecodeError:
        return None
    product_id = payload.get("productId") if isinstance(payload, dict) else None
    try:
        return int(product_id)
    except (TypeError, ValueError):
        return None


def requested_size_state_from_summary(
    summary_payload: dict, requested_size: str
) -> tuple[bool, bool]:
    """Use Beymen Club's productsummary data as the authoritative size stock source."""
    result = summary_payload.get("result") if isinstance(summary_payload, dict) else None
    sizes = result.get("sizes") if isinstance(result, dict) else None
    if not isinstance(sizes, list):
        return False, False

    found = False
    available = False
    for size in sizes:
        if not isinstance(size, dict) or not size_matches(size.get("sizeName"), requested_size):
            continue
        found = True
        in_stock = size.get("inStock")
        stock_quantity = size.get("stockQuantity")
        available = available or bool(in_stock) and (stock_quantity is None or stock_quantity > 0)
    return found, available


def extract_offer(html: str, source_url: str = "") -> OfferResult:
    soup = soup_from_html(html)
    jsonld_title, jsonld_price = extract_jsonld_product(soup)
    title = jsonld_title or extract_title(soup) or "Beymen Club urunu"
    image_url = extract_image(soup)

    for price in (
        _extract_basket_price(soup),
        jsonld_price,
        extract_price_from_meta(soup),
        extract_price_from_selectors(soup, BEYMENCLUB_SELECTORS),
        extract_price_from_scripts(html),
    ):
        if price is not None:
            return OfferResult(title=title, price=price, seller=None, image_url=image_url)

    raise HermesError("Beymen Club sayfasindan fiyat bulunamadi.")


def extract_offers(
    html: str,
    source_url: str = "",
    size: str = "",
    size_summary: dict | None = None,
) -> list[OfferResult]:
    """Read Beymen Club's price only when the requested apparel size is available."""
    requested_size = str(size or "").strip()
    if not requested_size:
        return [extract_offer(html, source_url=source_url)]
    found, available = requested_size_state_from_summary(size_summary or {}, requested_size)
    if not found:
        # Retain a markup fallback only if Beymen Club removes the summary API.
        found, available = requested_size_state(html, requested_size)
    soup = soup_from_html(html)
    title = extract_jsonld_product(soup)[0] or extract_title(soup) or "Beymen Club urunu"
    if not found:
        raise OutOfStockHermesError(
            f"Beymen Club beden bulunamadı: {requested_size}", title, source_url
        )
    if not available:
        raise OutOfStockHermesError(
            f"Beymen Club beden stokta değil: {requested_size}", title, source_url
        )
    return [extract_offer(html, source_url=source_url)]
