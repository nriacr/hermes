"""Hepsiburada price reading: valid ranges, cart and Premium campaign prices, prices inside JSON."""

import re
from decimal import Decimal
from typing import Any, Optional

from ...errors import HermesError
from ...utils import normalize_offer_text, parse_decimal
from .common import (
    CART_SPECIAL_MARKERS,
    COUPON_MARKERS,
    INSTALLMENT_MARKERS,
    MAX_PRICE,
    MIN_PRICE,
    PREMIUM_CAMPAIGN_MARKERS_NORMALIZED,
    PREMIUM_PRICE_MARKERS,
    PREMIUM_PRICE_MARKERS_NORMALIZED,
    PRICE_LIKE_RE,
    PRICE_RE,
    _clean_text,
)


def _valid_price(price: Decimal) -> bool:
    return MIN_PRICE <= price <= MAX_PRICE


def _normalize_price_text(text: str) -> str:
    cleaned = _clean_text(text)
    match = PRICE_RE.search(cleaned)
    if match:
        cleaned = match.group(0)
    if "," not in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "")
    return cleaned


def _parse_price(raw_price: Any) -> Optional[Decimal]:
    if raw_price in (None, ""):
        return None
    if isinstance(raw_price, (int, float)):
        price = parse_decimal(str(raw_price))
        return price if _valid_price(price) else None
    text = _normalize_price_text(str(raw_price))
    if not text:
        return None
    try:
        price = parse_decimal(text)
    except HermesError:
        return None
    return price if _valid_price(price) else None


def _context_before_price(text: str, start: int) -> str:
    before = text[max(0, start - 56) : start]
    after_previous_price = re.split(r"TL", before, flags=re.IGNORECASE)[-1]
    return normalize_offer_text(after_previous_price)


def _is_noise_price(text: str, start: int, end: int) -> bool:
    before = _context_before_price(text, start)
    after = normalize_offer_text(text[end : end + 24])
    close_context = f"{before} {after}"
    coupon_context = normalize_offer_text(text[max(0, start - 48) : end + 48])
    if any(marker in close_context for marker in INSTALLMENT_MARKERS):
        return True
    if any(marker in coupon_context for marker in COUPON_MARKERS):
        return True
    return False


def _valid_prices_from_text(text: str) -> list[Decimal]:
    prices = []
    clean = _clean_text(text)
    for match in PRICE_RE.finditer(clean):
        if _is_noise_price(clean, match.start(), match.end()):
            continue
        price = _parse_price(match.group(0))
        if price is not None:
            prices.append(price)
    return prices


def _price_from_aria_label(card) -> Optional[Decimal]:
    for element in card.select("[aria-label]"):
        label = _clean_text(element.get("aria-label") or "")
        match = re.search(r"fiyat\s*:\s*(?P<price>[^,]+(?:,\d{2})?\s*TL)", label, re.IGNORECASE)
        if not match:
            continue
        price = _parse_price(match.group("price"))
        if price is not None:
            return price
    return None


def _cart_special_prices(text: str) -> list[Decimal]:
    return _prices_after_markers(text, CART_SPECIAL_MARKERS, skip_campaign_amounts=True)


def _is_premium_campaign_amount(segment: str, price_start: int, price_end: int) -> bool:
    close_context = normalize_offer_text(segment[max(0, price_start - 56) : price_end + 56])
    return any(marker in close_context for marker in PREMIUM_CAMPAIGN_MARKERS_NORMALIZED)


def _prices_after_markers(
    text: str,
    markers: tuple[str, ...],
    window: int = 160,
    *,
    skip_campaign_amounts: bool = False,
) -> list[Decimal]:
    clean = _clean_text(text)
    searchable = clean.casefold().replace("’", "'").replace("`", "'").replace("´", "'")
    prices: list[Decimal] = []
    for marker in markers:
        pattern = re.compile(re.escape(marker.casefold().replace("’", "'").replace("`", "'").replace("´", "'")))
        for match in pattern.finditer(searchable):
            position = match.start()
            segment = clean[position : position + window]
            for match in PRICE_RE.finditer(segment):
                if skip_campaign_amounts and _is_premium_campaign_amount(segment, match.start(), match.end()):
                    continue
                price = _parse_price(match.group(0))
                if price is not None:
                    prices.append(price)
                    break
            else:
                for match in PRICE_LIKE_RE.finditer(segment):
                    if skip_campaign_amounts and _is_premium_campaign_amount(segment, match.start(), match.end()):
                        continue
                    price = _parse_price(match.group(0))
                    if price is not None:
                        prices.append(price)
                        break
    return prices


def _premium_prices(text: str) -> list[Decimal]:
    return _prices_after_markers(text, PREMIUM_PRICE_MARKERS, skip_campaign_amounts=True)


def _has_premium_marker(text: str) -> bool:
    normalized = normalize_offer_text(text)
    return any(marker in normalized for marker in PREMIUM_PRICE_MARKERS_NORMALIZED)


def _first_mapping_price(mapping: dict) -> Optional[Decimal]:
    for key in (
        "finalPrice",
        "final_price",
        "salePrice",
        "sale_price",
        "currentPrice",
        "current_price",
        "discountedPrice",
        "price",
        "formattedPrice",
        "formatted_price",
        "amount",
    ):
        price = _parse_price(mapping.get(key))
        if price is not None:
            return price
    return None


def _number_price(raw_price: str) -> Optional[Decimal]:
    try:
        price = Decimal(str(raw_price))
    except Exception:  # noqa: BLE001
        return None
    return price if _valid_price(price) else None


def _price_context_allows(context: str) -> bool:
    normalized = normalize_offer_text(context)
    blocked_markers = (
        "installment",
        "taksit",
        "aylik",
        "worldcard",
        "kupon",
        "coupon",
        "kargo",
        "cargo",
        "shipping",
        "shipment",
    )
    return not any(marker in normalized for marker in blocked_markers)


def _price_context_is_premium(context: str) -> bool:
    normalized = normalize_offer_text(context).replace("’", "'").replace("`", "'").replace("´", "'")
    return any(marker in normalized for marker in PREMIUM_PRICE_MARKERS_NORMALIZED)


def _price_context_key(context: str) -> str:
    """Normalize API price labels regardless of dash/underscore formatting."""
    normalized = normalize_offer_text(context)
    return re.sub(r"[^a-z0-9]+", " ", normalized).strip()


def _premium_entries_from_nested_value(value: Any, parent_context: str = "") -> list[tuple[Decimal, str]]:
    entries: list[tuple[Decimal, str]] = []
    if isinstance(value, dict):
        own_context = " ".join(
            str(item)
            for item in value.values()
            if isinstance(item, str) and len(item) <= 120
        )
        context = f"{parent_context} {own_context}".strip()
        if _price_context_is_premium(context):
            for key in ("value", "amount", "price", "finalPrice", "finalPriceOnSale", "minimumPrice"):
                price = _number_price(value.get(key))
                if price is not None:
                    entries.append((price, context))
        for key, nested in value.items():
            key_context = f"{context} {key}".strip()
            entries.extend(_premium_entries_from_nested_value(nested, key_context))
    elif isinstance(value, list):
        for item in value:
            entries.extend(_premium_entries_from_nested_value(item, parent_context))
    return entries


def _mapping_price_entries(mapping: dict) -> list[tuple[Decimal, str]]:
    entries: list[tuple[Decimal, str]] = []

    def add(raw_price: Any, context: str) -> None:
        price = _number_price(raw_price)
        if price is not None and _price_context_allows(context):
            entries.append((price, context))

    for key in (
        "finalPriceOnSale",
        "finalPrice",
        "final_price",
        "salePrice",
        "sale_price",
        "currentPrice",
        "current_price",
        "discountedPrice",
        "price",
        "amount",
    ):
        add(mapping.get(key), key)

    for collection_key in ("prices", "minimumPrices"):
        price_items = mapping.get(collection_key)
        if not isinstance(price_items, list):
            continue
        for item in price_items:
            if not isinstance(item, dict):
                continue
            context = " ".join(str(value) for value in item.values() if isinstance(value, str))
            normalized_context = _price_context_key(context)
            if (
                collection_key == "minimumPrices"
                and not _price_context_is_premium(context)
                and "non segmented price" not in normalized_context
            ):
                continue
            add(item.get("value"), f"{collection_key} {context}")

    entries.extend(_premium_entries_from_nested_value(mapping))
    return entries


def _listing_mapping_price(mapping: dict) -> Optional[Decimal]:
    entries = _mapping_price_entries(mapping)
    premium_entries = [price for price, context in entries if _price_context_is_premium(context)]
    if premium_entries:
        return min(premium_entries)
    reference_prices = [
        price
        for price, context in entries
        if normalize_offer_text(context)
        in {
            "finalpriceonsale",
            "finalprice",
            "final price",
            "saleprice",
            "sale price",
            "currentprice",
            "current price",
            "discountedprice",
            "discounted price",
        }
    ]
    if reference_prices:
        lowest_reasonable_price = max(reference_prices) * Decimal("0.50")
        entries = [
            (price, context)
            for price, context in entries
            if price >= lowest_reasonable_price or _price_context_is_premium(context)
        ]
    if entries:
        return min(price for price, _context in entries)

    final_price = _number_price(mapping.get("finalPriceOnSale"))
    if final_price is not None:
        return final_price

    minimum_prices = mapping.get("minimumPrices")
    if isinstance(minimum_prices, list):
        for item in minimum_prices:
            if isinstance(item, dict) and _price_context_key(str(item.get("name") or "")) == "non segmented price":
                price = _number_price(item.get("value"))
                if price is not None:
                    return price

    return _number_price(mapping.get("minimumPrice"))
