"""Shared, conservative size availability helpers for apparel providers.

This module deliberately only answers whether a requested size is represented as
available on a rendered product page. Each provider keeps its own price parser
and decides how to use this result.
"""

import json
import re
from typing import Any, Iterable

from bs4 import BeautifulSoup, Tag

from ..utils import normalize_offer_text, repair_mojibake
from .base import iter_json_objects


OUT_OF_STOCK_MARKERS = (
    "outofstock",
    "out of stock",
    "sold out",
    "unavailable",
    "not available",
    "disabled",
    "stokta yok",
    "tukendi",
    "tükendi",
    "benzer urunler",
    "benzer ürünler",
)
SIZE_FIELD_NAMES = (
    "size",
    "sizename",
    "size_name",
    "sizevalue",
    "beden",
    "bedenadi",
    "beden_adi",
)
AVAILABILITY_FIELD_NAMES = (
    "available",
    "instock",
    "isavailable",
    "stock",
    "stocklevel",
    "quantity",
    "availability",
)
SIZE_CONTEXT_MARKERS = ("size", "beden", "variant", "varyant", "option", "secenek", "seçenek")


def clean_size(value: Any) -> str:
    text = re.sub(r"\([^)]*\)", " ", repair_mojibake(str(value or "")))
    return re.sub(r"\s+", " ", text).strip()


def size_matches(candidate: Any, requested: Any) -> bool:
    candidate_normalized = normalize_offer_text(clean_size(candidate))
    requested_normalized = normalize_offer_text(clean_size(requested))
    if not requested_normalized:
        return True
    if candidate_normalized == requested_normalized:
        return True
    ignored = {"eu", "us", "uk", "yas", "yaş", "yil", "yıl", "beden", "size"}
    candidate_tokens = {
        token for token in re.findall(r"[a-z0-9]+", candidate_normalized) if token not in ignored
    }
    requested_tokens = {
        token for token in re.findall(r"[a-z0-9]+", requested_normalized) if token not in ignored
    }
    return bool(requested_tokens) and requested_tokens.issubset(candidate_tokens)


def _text_is_available(value: Any) -> bool:
    normalized = normalize_offer_text(str(value or ""))
    if normalized in {"false", "0", "outofstock", "soldout", "unavailable"}:
        return False
    return not any(marker in normalized for marker in OUT_OF_STOCK_MARKERS)


def _value_is_available(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, dict):
        for key, candidate in value.items():
            if normalize_offer_text(key).replace("_", "") in AVAILABILITY_FIELD_NAMES:
                return _value_is_available(candidate)
        return _text_is_available(" ".join(str(item) for item in value.values()))
    if isinstance(value, list):
        return any(_value_is_available(item) for item in value)
    return _text_is_available(value)


def _dict_size_name(data: dict) -> str:
    for key, value in data.items():
        if normalize_offer_text(key).replace("_", "") in SIZE_FIELD_NAMES:
            return clean_size(value)
    return ""


def _dict_is_available(data: dict) -> bool:
    for key, value in data.items():
        if normalize_offer_text(key).replace("_", "") in AVAILABILITY_FIELD_NAMES:
            return _value_is_available(value)
    return _text_is_available(" ".join(str(value) for value in data.values()))


def _json_payloads(soup: BeautifulSoup) -> Iterable[Any]:
    for script in soup.find_all("script"):
        raw = script.string or script.get_text(" ", strip=True)
        if not raw:
            continue
        raw = raw.strip()
        if script.get("type") and "json" in str(script.get("type")).casefold():
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                continue


def _json_size_states(soup: BeautifulSoup, requested_size: str) -> tuple[bool, bool]:
    found = False
    available = False
    for payload in _json_payloads(soup):
        for item in iter_json_objects(payload):
            candidate = _dict_size_name(item)
            if candidate and size_matches(candidate, requested_size):
                found = True
                available = available or _dict_is_available(item)
    return found, available


def _node_context(node: Tag) -> str:
    values = [node.name or "", str(node.get("id", "")), " ".join(node.get("class", []))]
    for name in ("name", "data-testid", "data-test-id", "data-size", "aria-label"):
        values.append(str(node.get(name, "")))
    parent = node.parent
    if isinstance(parent, Tag):
        values.extend(
            [
                parent.name or "",
                str(parent.get("id", "")),
                str(parent.get("name", "")),
                " ".join(parent.get("class", [])),
            ]
        )
    return normalize_offer_text(" ".join(values))


def _node_is_available(node: Tag) -> bool:
    context = _node_context(node)
    if node.has_attr("disabled") or normalize_offer_text(node.get("aria-disabled", "")) == "true":
        return False
    return _text_is_available(context + " " + node.get_text(" ", strip=True))


def _dom_size_states(soup: BeautifulSoup, requested_size: str) -> tuple[bool, bool]:
    found = False
    available = False
    for node in soup.find_all(["button", "option", "label", "li", "a", "input"]):
        context = _node_context(node)
        if not any(marker in context for marker in SIZE_CONTEXT_MARKERS):
            continue
        candidates = [node.get("data-size"), node.get("value"), node.get_text(" ", strip=True)]
        if not any(size_matches(candidate, requested_size) for candidate in candidates if candidate):
            continue
        found = True
        available = available or _node_is_available(node)
    return found, available


def requested_size_state(html: str, requested_size: str) -> tuple[bool, bool]:
    """Return ``(found, available)`` for a size, ignoring letter case safely."""
    requested = clean_size(requested_size)
    if not requested:
        return True, True
    soup = BeautifulSoup(html, "html.parser")
    json_found, json_available = _json_size_states(soup, requested)
    dom_found, dom_available = _dom_size_states(soup, requested)
    return json_found or dom_found, json_available or dom_available
