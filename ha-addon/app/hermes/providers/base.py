import json
import re
from typing import Any, Iterable, Optional

from bs4 import BeautifulSoup

from ..errors import HermesError
from ..utils import parse_decimal, repair_mojibake

PRODUCT_TITLE_SELECTORS = [
    "#productTitle",
    "#title",
    "h1[data-test-id='title']",
    "h1.pr-new-br",
    "h1.product-name",
    "h1",
    "meta[property='og:title']",
]

PRICE_META_SELECTORS = [
    ("meta", {"property": "product:price:amount"}, "content"),
    ("meta", {"property": "og:price:amount"}, "content"),
    ("meta", {"itemprop": "price"}, "content"),
    ("meta", {"name": "twitter:data1"}, "content"),
]

SCRIPT_PRICE_PATTERNS = [
    re.compile(
        r'"(?:price|sellingPrice|discountedPrice|currentPrice|amount)"\s*:\s*"?(?P<price>\d+(?:[.,]\d{1,2})?)"?',
        re.IGNORECASE,
    ),
    re.compile(r"(?P<price>\d{1,3}(?:\.\d{3})*,\d{2})\s*TL", re.IGNORECASE),
]


def soup_from_html(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def extract_title(soup: BeautifulSoup) -> Optional[str]:
    for selector in PRODUCT_TITLE_SELECTORS:
        element = soup.select_one(selector)
        if not element:
            continue
        if element.name == "meta":
            content = str(element.get("content", "")).strip()
            if content:
                return repair_mojibake(content)
        text = element.get_text(" ", strip=True)
        if text:
            return repair_mojibake(text)
    return None


def extract_price_from_meta(soup: BeautifulSoup):
    for tag_name, attrs, attr_name in PRICE_META_SELECTORS:
        element = soup.find(tag_name, attrs=attrs)
        if element and element.get(attr_name):
            try:
                return parse_decimal(str(element[attr_name]))
            except HermesError:
                continue
    return None


def extract_price_from_selectors(soup: BeautifulSoup, selectors: list[str]):
    for selector in selectors:
        element = soup.select_one(selector)
        if not element:
            continue
        raw_value = str(element.get("content") or element.get("value") or "").strip()
        text = raw_value or element.get_text(" ", strip=True)
        if text:
            try:
                return parse_decimal(text)
            except HermesError:
                continue
    return None


def extract_price_from_scripts(html: str):
    for pattern in SCRIPT_PRICE_PATTERNS:
        candidates = []
        for match in pattern.finditer(html):
            try:
                candidates.append(parse_decimal(match.group("price")))
            except HermesError:
                continue
        if candidates:
            return min(candidates)
    return None


def iter_json_objects(value: Any) -> Iterable[dict]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_json_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_json_objects(child)


def as_type_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).casefold() for item in value]
    if value is None:
        return []
    return [str(value).casefold()]


def price_from_offer(offer: Any):
    if isinstance(offer, list):
        candidates = [price_from_offer(item) for item in offer]
        candidates = [item for item in candidates if item is not None]
        return min(candidates) if candidates else None
    if not isinstance(offer, dict):
        return None
    for key in ("price", "lowPrice", "highPrice", "priceAmount"):
        raw = offer.get(key)
        if raw not in (None, ""):
            try:
                return parse_decimal(str(raw))
            except HermesError:
                continue
    nested_offer = offer.get("offers")
    if nested_offer is not None:
        return price_from_offer(nested_offer)
    return None


def extract_jsonld_product(soup: BeautifulSoup):
    found_title = None
    found_price = None
    for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw = script.string or script.get_text(" ", strip=True)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for item in iter_json_objects(payload):
            if "product" not in as_type_list(item.get("@type")):
                continue
            title = str(item.get("name") or "").strip()
            if title and not found_title:
                found_title = repair_mojibake(title)
            price = price_from_offer(item.get("offers"))
            if price is not None:
                found_price = price if found_price is None else min(found_price, price)
    return found_title, found_price
