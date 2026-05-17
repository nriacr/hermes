import re
from typing import Any

from ..errors import HermesError
from ..utils import normalize_offer_text, parse_decimal

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


def extract_price_after_secondary_offer_text(text: str):
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


def extract_secondary_offer_price(container: Any):
    for selector in AMAZON_SECONDARY_OFFER_SELECTORS:
        for element in container.select(selector):
            price = extract_price_after_secondary_offer_text(element.get_text(" ", strip=True))
            if price is not None:
                return price
    return extract_price_after_secondary_offer_text(container.get_text(" ", strip=True))
