import re
from typing import Optional

from ..errors import HermesError
from ..models import OfferResult
from ..utils import parse_decimal, repair_mojibake
from .base import (
    extract_jsonld_product,
    extract_price_from_meta,
    extract_price_from_scripts,
    extract_price_from_selectors,
    extract_title,
    soup_from_html,
)

AMAZON_PRODUCT_SELECTORS = [
    "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
    "#corePrice_feature_div .a-price .a-offscreen",
    "#tp_price_block_total_price_ww .a-offscreen",
    ".apexPriceToPay .a-offscreen",
    ".a-price.aok-align-center .a-offscreen",
    ".a-price .a-offscreen",
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


def _extract_price_after_secondary_offer_text(text: str):
    normalized = repair_mojibake(text).casefold().replace("ı", "i")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if "diger satin alma secenekleri" not in normalized or "ikinci el" not in normalized:
        return None
    match = AMAZON_SECONDARY_OFFER_PRICE_PATTERN.search(normalized)
    if not match:
        return None
    try:
        return parse_decimal(match.group("price"))
    except HermesError:
        return None


def _extract_secondary_offer_price(soup):
    for selector in AMAZON_SECONDARY_OFFER_SELECTORS:
        for element in soup.select(selector):
            price = _extract_price_after_secondary_offer_text(element.get_text(" ", strip=True))
            if price is not None:
                return price
    return _extract_price_after_secondary_offer_text(soup.get_text(" ", strip=True))


def extract_offer(html: str) -> OfferResult:
    soup = soup_from_html(html)
    jsonld_title, jsonld_price = extract_jsonld_product(soup)
    title: Optional[str] = jsonld_title or extract_title(soup) or "Amazon urunu"

    for price in (
        _extract_secondary_offer_price(soup),
        jsonld_price,
        extract_price_from_meta(soup),
        extract_price_from_selectors(soup, AMAZON_PRODUCT_SELECTORS),
        extract_price_from_scripts(html),
    ):
        if price is not None:
            return OfferResult(title=title, price=price, seller=None)

    raise HermesError("Amazon sayfasindan fiyat bulunamadi.")
