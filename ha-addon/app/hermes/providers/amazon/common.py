import re
from typing import Any

from bs4 import Comment

from ...errors import HermesError
from ...utils import normalize_offer_text, parse_decimal

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


def is_hidden_element(element: Any) -> bool:
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


def visible_text_nodes(soup):
    for node in soup.find_all(string=True):
        if isinstance(node, Comment) or is_hidden_element(node.parent):
            continue
        if any(parent.name in {"script", "style", "template", "noscript"} for parent in node.parents):
            continue
        yield node


def has_secondary_offer_text(text: str) -> bool:
    normalized = normalize_offer_text(text)
    return "diger satin alma secenekleri" in normalized


def has_explicit_used_offer_evidence(text: str) -> bool:
    """Return true only for Amazon's visible second-hand offer wording."""
    normalized = normalize_offer_text(text)
    return "diger satin alma secenekleri" in normalized and "ikinci el urun" in normalized


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
    if not has_explicit_used_offer_evidence(text):
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
        # Amazon's result card markup changes frequently. Some cards expose
        # the used offer as plain card text rather than a dedicated component.
        # The wording is reliable only when both phrases are in the same card:
        # "Diğer satın alma seçenekleri" and "İkinci El ürün".
        if has_explicit_used_offer_evidence(text):
            return _price_after_verified_secondary_offer_text(text)
    return None
