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

TRENDYOL_SELECTORS = [
    ".product-price-container .prc-dsc",
    ".product-price-container .prc-slg",
    ".prc-dsc",
    ".prc-slg",
    "[data-testid='price-current-price']",
    "[itemprop='price']",
]


def extract_offer(html: str) -> OfferResult:
    soup = soup_from_html(html)
    jsonld_title, jsonld_price = extract_jsonld_product(soup)
    title = jsonld_title or extract_title(soup) or "Trendyol urunu"

    for price in (
        jsonld_price,
        extract_price_from_meta(soup),
        extract_price_from_selectors(soup, TRENDYOL_SELECTORS),
        extract_price_from_scripts(html),
    ):
        if price is not None:
            return OfferResult(title=title, price=price, seller=None)

    plain = soup.get_text(" ", strip=True)
    if "TL" in plain:
        import re

        match = re.search(r"(\d{1,3}(?:\.\d{3})*,\d{2})\s*TL", plain)
        if match:
            return OfferResult(title=title, price=parse_decimal(match.group(1)), seller=None)

    raise HermesError("Trendyol sayfasindan fiyat bulunamadi.")
