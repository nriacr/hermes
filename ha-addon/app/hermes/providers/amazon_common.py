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


def has_secondary_offer_text(text: str) -> bool:
    normalized = normalize_offer_text(text)
    return "diger satin alma secenekleri" in normalized


def has_verified_warehouse_evidence(text: str) -> bool:
    """Require both Amazon Depo and second-hand evidence before tagging DEPO."""
    normalized = normalize_offer_text(text)
    return "ikinci el" in normalized and "amazon depo" in normalized


def _price_after_verified_secondary_offer_text(text: str):
    """Read Amazon's dedicated second-hand offer component.

    Amazon's search cards do not always render the seller name in text. The
    dedicated secondary-offer component is Amazon's own used-offer surface, so
    its explicit "İkinci El" wording is the verified condition there. A plain
    page-wide text fallback stays stricter and still requires "Amazon Depo".
    """
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


def extract_verified_secondary_offer_price(container: Any, include_container_fallback: bool = True):
    """Return a used price only from Amazon's dedicated offer component."""
    for selector in AMAZON_SECONDARY_OFFER_SELECTORS:
        for element in container.select(selector):
            price = _price_after_verified_secondary_offer_text(element.get_text(" ", strip=True))
            if price is not None:
                return price
    if include_container_fallback:
        text = container.get_text(" ", strip=True)
        if has_verified_warehouse_evidence(text):
            return _price_after_verified_secondary_offer_text(text)
    return None
