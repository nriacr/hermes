"""Product-scoped helpers; unrelated sections never provide fallback prices."""

import json
import re

from ..errors import HermesError
from ..utils import canonical_tracking_url, normalize_offer_text, parse_decimal
from .base import as_type_list, iter_json_objects

UNRELATED = re.compile(r"recommend|related|suggest|cross.sell|upsell|benzer|öneri", re.I)

def is_product_scope(element):
    return not any(UNRELATED.search(" ".join([str(parent.get("id", "")),
                    " ".join(parent.get("class", []))])) for parent in [element, *element.parents]
                   if getattr(parent, "attrs", None) is not None)

def product_json(soup, title, url=""):
    products = []
    for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        if not is_product_scope(script):
            continue
        try:
            raw = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        for item in iter_json_objects(raw):
            if "product" in as_type_list(item.get("@type")):
                products.append(item)
    matching = [item for item in products if
                (url and canonical_tracking_url(str(item.get("url") or "")) == canonical_tracking_url(url))
                or (title and normalize_offer_text(str(item.get("name") or "")) == normalize_offer_text(title))]
    if len(matching) == 1:
        return matching[0]
    if not title and len(products) == 1:
        return products[0]
    return None

def explicit_price(product):
    offers = product.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if len(offers) == 1 else None
    if not isinstance(offers, dict) or offers.get("priceCurrency", "TRY") != "TRY":
        return None
    raw = offers.get("price")
    try:
        return parse_decimal(str(raw)) if raw is not None else None
    except HermesError:
        return None

def visible_price(soup, selectors):
    for selector in selectors:
        for element in soup.select(selector):
            if not is_product_scope(element):
                continue
            raw = element.get("content") or element.get("value") or element.get_text(" ", strip=True)
            if raw and not re.search(r"USD|EUR|GBP|[$€£]", str(raw), re.I):
                try:
                    return parse_decimal(str(raw))
                except HermesError:
                    continue
    return None


def unavailable(product):
    offers = product.get("offers")
    if isinstance(offers, list):
        offers = offers[0] if len(offers) == 1 else None
    return isinstance(offers, dict) and str(offers.get("availability", "")).lower().rsplit("/", 1)[-1] in {"outofstock", "soldout", "discontinued"}


def scoped_soup(soup):
    for element in list(soup.find_all(True)):
        if element.parent is not None and not is_product_scope(element):
            element.decompose()
    return soup
