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

NETWORK_SELECTORS = [
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

NETWORK_BASKET_PATTERNS = [
    re.compile(
        r"\d+\s*ve\s*(?:üzeri|uzeri|ve\s*üzeri|ve\s*uzeri).*?sepette\s*"
        r"(?P<price>\d{1,3}(?:\.\d{3})*,\d{2}|\d+(?:,\d{2})?)\s*tl",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"sepette\s*(?P<price>\d{1,3}(?:\.\d{3})*,\d{2}|\d+(?:,\d{2})?)\s*tl",
        re.IGNORECASE | re.DOTALL,
    ),
]


def _extract_basket_price(soup):
    text = soup.get_text(" ", strip=True)
    candidates = []
    for pattern in NETWORK_BASKET_PATTERNS:
        for match in pattern.finditer(text):
            try:
                candidates.append(parse_decimal(match.group("price")))
            except HermesError:
                continue
    return min(candidates) if candidates else None


def extract_offer(html: str) -> OfferResult:
    soup = soup_from_html(html)
    jsonld_title, jsonld_price = extract_jsonld_product(soup)
    title = jsonld_title or extract_title(soup) or "Network urunu"

    for price in (
        _extract_basket_price(soup),
        jsonld_price,
        extract_price_from_meta(soup),
        extract_price_from_selectors(soup, NETWORK_SELECTORS),
        extract_price_from_scripts(html),
    ):
        if price is not None:
            return OfferResult(title=title, price=price, seller=None)

    raise HermesError("Network sayfasindan fiyat bulunamadi.")
