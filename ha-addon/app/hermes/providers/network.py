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

PRICE_PATTERN = r"(?:\d{1,3}(?:\.\d{3})+(?:,\d{2})?|\d+(?:,\d{2})?)"

NETWORK_BASKET_PATTERNS = [
    re.compile(
        r"(?<!\d)\d+\s*ve\s*(?:üzeri|uzeri)(?:\s+adet)?(?:\s+(?:için|icin))?\s*"
        rf"(?P<price>{PRICE_PATTERN})\s*tl",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r"\d+\s*ve\s*(?:üzeri|uzeri|ve\s*üzeri|ve\s*uzeri).*?sepette\s*"
        rf"(?P<price>{PRICE_PATTERN})\s*tl",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        rf"sepette\s*(?P<price>{PRICE_PATTERN})\s*tl",
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


def _network_product_payload(html: str) -> dict:
    """Return Network's server-rendered product payload without touching other providers."""
    match = re.search(r"\bvar\s+product\s*=\s*", html)
    if not match:
        return {}
    try:
        # The payload contains nested objects, so a balanced JSON decoder is
        # safer than a regular expression that has to guess where it ends.
        payload, _ = json.JSONDecoder().raw_decode(html[match.end() :].lstrip())
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _network_requested_size_state(html: str, requested_size: str) -> tuple[bool, bool]:
    """Read Network's authoritative ``Sizes`` / ``NoStock`` values first."""
    product = _network_product_payload(html)
    sizes = product.get("Sizes") if isinstance(product, dict) else None
    if isinstance(sizes, list):
        found = False
        available = False
        for size in sizes:
            if not isinstance(size, dict) or not size_matches(size.get("ValueText"), requested_size):
                continue
            found = True
            available = available or not bool(size.get("NoStock"))
        if found:
            return found, available

    # Preserve a narrow fallback for future Network markup changes. This only
    # decides stock state; Network's price parser remains fully isolated here.
    return requested_size_state(html, requested_size)


def extract_offer(html: str, source_url: str = "") -> OfferResult:
    soup = soup_from_html(html)
    jsonld_title, jsonld_price = extract_jsonld_product(soup)
    title = jsonld_title or extract_title(soup) or "Network urunu"
    image_url = extract_image(soup)

    for price in (
        _extract_basket_price(soup),
        jsonld_price,
        extract_price_from_meta(soup),
        extract_price_from_selectors(soup, NETWORK_SELECTORS),
        extract_price_from_scripts(html),
    ):
        if price is not None:
            return OfferResult(title=title, price=price, seller=None, image_url=image_url)

    raise HermesError("Network sayfasindan fiyat bulunamadi.")


def extract_offers(html: str, source_url: str = "", size: str = "") -> list[OfferResult]:
    """Read Network's price only when the requested apparel size is available."""
    requested_size = str(size or "").strip()
    if not requested_size:
        return [extract_offer(html, source_url=source_url)]
    found, available = _network_requested_size_state(html, requested_size)
    soup = soup_from_html(html)
    title = extract_jsonld_product(soup)[0] or extract_title(soup) or "Network urunu"
    if not found:
        raise OutOfStockHermesError(
            f"Network beden bulunamadı: {requested_size}", title, source_url
        )
    if not available:
        raise OutOfStockHermesError(
            f"Network beden stokta değil: {requested_size}", title, source_url
        )
    return [extract_offer(html, source_url=source_url)]
