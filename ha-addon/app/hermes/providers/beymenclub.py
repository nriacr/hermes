"""Beymen Club'a ozel fiyat okuyucu.

Network benzeri sepet kampanyalarini onceleyerek, normal fiyatla karismasini
engeller. Bu kurallar baska saglayicilardan tamamen bagimsiz tutulur.
"""

import re

from ..errors import HermesError
from ..models import OfferResult
from ..utils import parse_decimal
from .base import (
    extract_jsonld_product,
    extract_price_from_meta,
    extract_price_from_scripts,
    extract_price_from_selectors,
    extract_title,
    soup_from_html,
)

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


def extract_offer(html: str) -> OfferResult:
    soup = soup_from_html(html)
    jsonld_title, jsonld_price = extract_jsonld_product(soup)
    title = jsonld_title or extract_title(soup) or "Beymen Club urunu"

    for price in (
        _extract_basket_price(soup),
        jsonld_price,
        extract_price_from_meta(soup),
        extract_price_from_selectors(soup, BEYMENCLUB_SELECTORS),
        extract_price_from_scripts(html),
    ):
        if price is not None:
            return OfferResult(title=title, price=price, seller=None)

    raise HermesError("Beymen Club sayfasindan fiyat bulunamadi.")
