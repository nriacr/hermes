import re
from typing import Optional

from ..errors import HermesError
from ..models import OfferResult
from ..utils import normalize_offer_text, parse_decimal
from .amazon_common import extract_secondary_offer_price
from .base import (
    extract_jsonld_product,
    extract_price_from_meta,
    extract_title,
    soup_from_html,
)

AMAZON_PRODUCT_SELECTORS = [
    "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
    "#corePriceDisplay_desktop_feature_div .aok-offscreen",
    "#corePrice_feature_div .a-price .a-offscreen",
    "#corePrice_feature_div .aok-offscreen",
    "#corePriceDisplay_desktop_feature_div span[data-a-color='price'] .a-offscreen",
    "#corePrice_feature_div span[data-a-color='price'] .a-offscreen",
    "#tp_price_block_total_price_ww .a-offscreen",
    ".apexPriceToPay .a-offscreen",
    "#priceblock_ourprice",
    "#priceblock_dealprice",
]

AMAZON_PRIMARY_PRICE_CONTAINERS = [
    "#corePriceDisplay_desktop_feature_div",
    "#corePrice_feature_div",
    "#apex_desktop",
]

BUYING_OPTION_PRICE_PATTERNS = [
    re.compile(
        r"degis\s+tokus\s+olmadan\s+"
        r"(?P<whole>\d{1,3}(?:\.\d{3})+|\d+)"
        r"(?:,(?P<comma_fraction>\d{1,2})|\s*(?P<plain_fraction>\d{2}))?\s*tl"
    ),
    re.compile(
        r"takas\s+olmadan\s+"
        r"(?P<whole>\d{1,3}(?:\.\d{3})+|\d+)"
        r"(?:,(?P<comma_fraction>\d{1,2})|\s*(?P<plain_fraction>\d{2}))?\s*tl"
    ),
]


def _parse_visible_price(text: str):
    clean = str(text or "").strip()
    if not clean:
        return None
    adjacent_fraction = re.search(r"(?P<whole>\d{1,3}(?:\.\d{3})+)(?P<fraction>\d{2})\s*TL", clean, re.I)
    if adjacent_fraction:
        clean = f"{adjacent_fraction.group('whole')},{adjacent_fraction.group('fraction')} TL"
    if "TL" in clean and "," not in clean and "." in clean:
        clean = clean.replace(".", "")
    try:
        return parse_decimal(clean)
    except HermesError:
        return None


def _ancestor_has_active_row(element) -> bool:
    current = element
    while current is not None and getattr(current, "name", None):
        classes = set(current.get("class") or [])
        if (
            current.get("data-csa-c-is-in-initial-active-row") == "true"
            or "a-accordion-active" in classes
        ):
            return True
        current = current.parent
    return False


def _extract_active_customer_visible_price(soup):
    candidates = []
    for amount_input in soup.select("input[name='items[0.base][customerVisiblePrice][amount]']"):
        price = _parse_visible_price(str(amount_input.get("value") or ""))
        if price is None:
            continue
        candidates.append((_ancestor_has_active_row(amount_input), price))
    for is_active, price in candidates:
        if is_active:
            return price
    if candidates:
        return min(price for _, price in candidates)
    return None


def _price_from_split_spans(price_element):
    whole = price_element.select_one(".a-price-whole")
    if not whole:
        return None
    whole_text = re.sub(r"[^\d.]", "", whole.get_text("", strip=True))
    if not whole_text:
        return None
    fraction = price_element.select_one(".a-price-fraction")
    fraction_text = re.sub(r"\D", "", fraction.get_text("", strip=True)) if fraction else "00"
    fraction_text = (fraction_text or "00")[:2].ljust(2, "0")
    return _parse_visible_price(f"{whole_text},{fraction_text} TL")


def _extract_split_primary_price(soup):
    for container_selector in AMAZON_PRIMARY_PRICE_CONTAINERS:
        container = soup.select_one(container_selector)
        if not container:
            continue
        for price_element in container.select(".a-price"):
            classes = set(price_element.get("class") or [])
            if "a-text-price" in classes or price_element.get("data-a-strike") == "true":
                continue
            price = _price_from_split_spans(price_element)
            if price is not None:
                return price
    return None


def _extract_buying_option_price(soup):
    normalized_text = normalize_offer_text(soup.get_text(" ", strip=True))
    for pattern in BUYING_OPTION_PRICE_PATTERNS:
        match = pattern.search(normalized_text)
        if not match:
            continue
        fraction = match.group("comma_fraction") or match.group("plain_fraction") or "00"
        price = _parse_visible_price(f"{match.group('whole')},{fraction} TL")
        if price is not None:
            return price
    return None


def _extract_visible_primary_price(soup):
    active_price = _extract_active_customer_visible_price(soup)
    if active_price is not None:
        return active_price
    split_price = _extract_split_primary_price(soup)
    if split_price is not None:
        return split_price
    buying_option_price = _extract_buying_option_price(soup)
    if buying_option_price is not None:
        return buying_option_price
    for selector in AMAZON_PRODUCT_SELECTORS:
        element = soup.select_one(selector)
        if not element:
            continue
        raw_value = str(element.get("content") or element.get("value") or "").strip()
        price = _parse_visible_price(raw_value or element.get_text(" ", strip=True))
        if price is not None:
            return price
    return None


def extract_offer(html: str) -> OfferResult:
    soup = soup_from_html(html)
    jsonld_title, jsonld_price = extract_jsonld_product(soup)
    title: Optional[str] = jsonld_title or extract_title(soup) or "Amazon ürünü"

    for price in (
        _extract_visible_primary_price(soup),
        extract_secondary_offer_price(soup),
        jsonld_price,
        extract_price_from_meta(soup),
    ):
        if price is not None:
            return OfferResult(title=title, price=price, seller=None)

    raise HermesError("Amazon sayfasından fiyat bulunamadı.")
