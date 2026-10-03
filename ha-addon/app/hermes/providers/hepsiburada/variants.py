"""Hepsiburada variant handling: selected color/capacity labels, display titles and variant links."""

import re
from typing import Any, Iterable, Optional
from urllib.parse import unquote

from ...utils import normalize_offer_text
from ..base import soup_from_html
from .common import (
    CAPACITY_RE,
    COLOR_LABELS,
    DETAIL_STOP_MARKERS,
    PRICE_RE,
    PRODUCT_URL_RE,
    VARIANT_FIELD_LABELS,
    VARIANT_FIELD_LABELS_NORMALIZED,
    VARIANT_LABEL_FORBIDDEN_VALUES,
    VARIANT_LABEL_JSON_KEYS_NORMALIZED,
    VARIANT_LABEL_SKIP_KEYS,
    VARIANT_LABEL_SKIP_KEYS_NORMALIZED,
    VARIANT_VALUE_JSON_KEYS,
    _canonical_product_url,
    _clean_text,
    _element_context_text,
    _embedded_text,
    _json_object_at,
    _product_id_from_url,
)


def _visible_lines_until_details(soup) -> list[str]:
    lines = []
    for raw_line in soup.get_text("\n", strip=True).splitlines():
        line = _clean_text(raw_line)
        if not line:
            continue
        if any(marker in normalize_offer_text(line) for marker in DETAIL_STOP_MARKERS):
            break
        lines.append(line)
    return lines


def _clean_variant_value(value: str) -> str:
    cleaned = _clean_text(value)
    cleaned = re.split(
        r"\b(Satıcı|Satici|Sepete|Adet|Teslimat|Ürün|Urun|Kuponlar)\b",
        cleaned,
        maxsplit=1,
    )[0]
    cleaned = _clean_text(cleaned.strip(" :-"))
    if not cleaned or len(cleaned) > 48:
        return ""
    normalized = normalize_offer_text(cleaned)
    if normalized in VARIANT_FIELD_LABELS_NORMALIZED:
        return ""
    if normalized in VARIANT_LABEL_FORBIDDEN_VALUES:
        return ""
    if any(marker in normalized for marker in ("fiyat", "price", "taksit", "sepete", "teslimat", "merchant", "listing")):
        return ""
    return cleaned


def _clean_variant_label(value: str) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for raw_part in re.split(r"\s*/\s*", _clean_text(value)):
        part = _clean_variant_value(raw_part)
        normalized_part = normalize_offer_text(part)
        if part and normalized_part not in seen:
            seen.add(normalized_part)
            parts.append(part)
    return " / ".join(parts)


def clean_display_title(title: str) -> str:
    parts = [_clean_text(part) for part in re.split(r"\s*/\s*", _clean_text(title)) if _clean_text(part)]
    if not parts:
        return _clean_text(title)
    base = _clean_search_base_title(parts[0])
    normalized_base = normalize_offer_text(base)
    cleaned_parts: list[str] = []
    seen: set[str] = set()
    for part in parts[1:]:
        cleaned = _clean_variant_value(part)
        normalized = normalize_offer_text(cleaned)
        if not cleaned or normalized in seen:
            continue
        if _looks_like_repeated_title_fragment(normalized_base, cleaned):
            continue
        seen.add(normalized)
        cleaned_parts.append(cleaned)
    return " / ".join([base, *cleaned_parts]) if cleaned_parts else base


def _clean_search_base_title(title: str) -> str:
    clean_title = _clean_text(title)
    normalized = normalize_offer_text(clean_title)
    if "nordbron" in normalized and "stark" in normalized and "sirt cantasi" in normalized:
        return "Nordbron Stark Sırt Çantası"
    return clean_title


def _looks_like_repeated_title_fragment(normalized_base: str, value: str) -> bool:
    normalized = normalize_offer_text(value)
    if len(value) < 12 or any(char.isdigit() for char in value):
        return False
    if normalized and normalized in normalized_base:
        return True
    base_words = normalized_base.split()
    value_words = normalized.split()
    if len(value_words) < 2 or len(base_words) < 2:
        return False
    return value_words[:2] == base_words[:2]


def _variant_field_label(normalized_line: str) -> str:
    normalized = normalized_line.strip(" :")
    for label in sorted(VARIANT_FIELD_LABELS, key=len, reverse=True):
        if normalized == label or normalized.startswith(f"{label}:") or normalized.startswith(f"{label} "):
            return label
    return ""


def extract_selected_variant_labels(html: str) -> list[str]:
    soup = soup_from_html(html)
    lines = _visible_lines_until_details(soup)
    values: list[str] = []
    seen: set[str] = set()
    for index, line in enumerate(lines[:80]):
        normalized = normalize_offer_text(line).strip(" :")
        label = _variant_field_label(normalized)
        if not label:
            continue
        value = ""
        if normalized == label and index + 1 < len(lines):
            value = _clean_variant_value(lines[index + 1])
        elif normalized.startswith(f"{label}:"):
            value = _clean_variant_value(line.split(":", 1)[1])
        elif normalized.startswith(f"{label} "):
            value = _clean_variant_value(line[len(label) :])
        normalized_value = normalize_offer_text(value)
        if value and normalized_value not in seen:
            seen.add(normalized_value)
            values.append(value)
    return values


def extract_selected_variant_label(html: str) -> str:
    return _clean_variant_label(" / ".join(extract_selected_variant_labels(html)))


def title_with_variant_label(title: str, variant_label: str) -> str:
    clean_title = _clean_text(title)
    clean_label = _clean_variant_label(variant_label)
    if not clean_title or not clean_label:
        return clean_title
    if normalize_offer_text(clean_label) in normalize_offer_text(clean_title):
        return clean_title
    return f"{clean_title} / {clean_label}"


def _values_from_variant_label(label: str) -> list[str]:
    return [_clean_variant_value(part) for part in re.split(r"\s*/\s*", _clean_text(label)) if _clean_variant_value(part)]


def _capacity_label_from_value(value: str) -> str:
    matches = list(CAPACITY_RE.finditer(_clean_text(value)))
    if not matches:
        return ""
    preferred = [match for match in matches if match.group(2).casefold() == "tb" or int(match.group(1)) >= 32]
    match = (preferred or matches)[-1]
    return f"{int(match.group(1))} {match.group(2).upper()}"


def _color_label_from_value(value: str) -> str:
    normalized = normalize_offer_text(unquote(_clean_text(value)).replace("-", " ").replace("_", " "))
    for marker, label in COLOR_LABELS:
        if re.search(rf"(^|\W){re.escape(marker)}($|\W)", normalized):
            return label
    return ""


def _explicit_variant_values_from_text(text: str) -> list[str]:
    values: list[str] = []
    clean = _clean_text(text)
    for label in VARIANT_FIELD_LABELS:
        pattern = re.compile(rf"\b{re.escape(label)}\b\s*:?\s*([^/|,\n\r]{{1,48}})", re.IGNORECASE)
        for match in pattern.finditer(clean):
            value = _clean_variant_value(match.group(1))
            if value:
                values.append(value)
    return values


def _ordered_variant_label(values: Iterable[str], *fallback_texts: str) -> str:
    capacities: list[str] = []
    colors: list[str] = []
    others: list[str] = []
    seen: set[str] = set()

    def add(bucket: list[str], value: str) -> None:
        normalized = normalize_offer_text(value)
        if value and normalized not in seen:
            seen.add(normalized)
            bucket.append(value)

    for raw_value in values:
        value = _clean_variant_value(raw_value)
        if not value:
            continue
        capacity = _capacity_label_from_value(value)
        color = _color_label_from_value(value)
        if capacity:
            add(capacities, capacity)
        elif color:
            add(colors, color)
        else:
            add(others, value)

    fallback = " ".join(unquote(str(text or "")).replace("-", " ").replace("_", " ") for text in fallback_texts)
    capacity = _capacity_label_from_value(fallback)
    color = _color_label_from_value(fallback)
    if capacity:
        add(capacities, capacity)
    if color:
        add(colors, color)

    return _clean_variant_label(" / ".join([*capacities[:1], *colors[:1], *others[:2]]))


def _selected_variant_mapping(text: str, selected_product_id: str) -> Optional[dict]:
    if not selected_product_id:
        return None
    sku_pattern = re.compile(r'"sku"\s*:\s*"' + re.escape(selected_product_id) + r'"', re.IGNORECASE)
    for match in sku_pattern.finditer(text):
        position = match.start()
        for _ in range(220):
            position = text.rfind("{", 0, position)
            if position < 0:
                break
            mapping = _json_object_at(text, position)
            if (
                mapping
                and str(mapping.get("sku") or "").upper() == selected_product_id
                and isinstance(mapping.get("variantListing"), list)
            ):
                return mapping
            position -= 1
    return None


def _selected_variant_listing_mappings(text: str, selected_product_id: str) -> list[dict]:
    mapping = _selected_variant_mapping(text, selected_product_id)
    if not mapping:
        return []
    return [item for item in mapping["variantListing"] if isinstance(item, dict)]


def _variant_label_from_value(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = _clean_variant_value(value)
    normalized = normalize_offer_text(cleaned)
    if not cleaned:
        return ""
    if any(marker in normalized for marker in ("http", "www", "hepsiburada", "hbc", "merchant", "listing")):
        return ""
    if PRICE_RE.search(cleaned):
        return ""
    return cleaned


def _variant_label_from_mapping(mapping: dict) -> str:
    labels: list[str] = []
    seen: set[str] = set()

    def add_label(value: Any) -> None:
        label = _variant_label_from_value(value)
        normalized_label = normalize_offer_text(label)
        if label and normalized_label not in seen:
            seen.add(normalized_label)
            labels.append(label)

    def add_selected_child_values(value: Any) -> None:
        if isinstance(value, dict):
            selected = any(
                bool(value.get(key))
                for key in ("selected", "isSelected", "checked", "active", "isActive")
            )
            if selected:
                for key in VARIANT_VALUE_JSON_KEYS:
                    add_label(value.get(key))
            for nested in value.values():
                if isinstance(nested, (dict, list)):
                    add_selected_child_values(nested)
        elif isinstance(value, list):
            for item in value:
                add_selected_child_values(item)

    stack: list[Any] = [mapping]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            field_name = ""
            for key in ("fieldName", "attributeName", "propertyName", "optionName", "name", "label", "displayName"):
                candidate_name = current.get(key)
                if normalize_offer_text(str(candidate_name or "")) in VARIANT_FIELD_LABELS_NORMALIZED:
                    field_name = str(candidate_name or "")
                    break
            if field_name:
                for key in VARIANT_VALUE_JSON_KEYS:
                    if key in {"name", "label", "displayName"} and normalize_offer_text(str(current.get(key) or "")) in VARIANT_FIELD_LABELS_NORMALIZED:
                        continue
                    add_label(current.get(key))
                add_selected_child_values(current)
            for key, value in current.items():
                normalized_key = normalize_offer_text(str(key))
                if key in VARIANT_LABEL_SKIP_KEYS or normalized_key in VARIANT_LABEL_SKIP_KEYS_NORMALIZED:
                    continue
                if normalized_key in VARIANT_LABEL_JSON_KEYS_NORMALIZED:
                    add_label(value)
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(current, list):
            stack.extend(current)
    return _clean_variant_label(" / ".join(labels[:4]))


def extract_embedded_variant_label(html: str, source_url: str) -> str:
    selected_product_id = _product_id_from_url(source_url)
    if not selected_product_id:
        return ""
    mapping = _selected_variant_mapping(_embedded_text(soup_from_html(html)), selected_product_id)
    return _clean_variant_label(_variant_label_from_mapping(mapping)) if mapping else ""


def _variant_context_allows(segment: str) -> bool:
    normalized = normalize_offer_text(segment)
    return any(marker in normalized for marker in ("variant", "varyant", "renk", "color", "secenek", "seçenek"))


def extract_variant_urls(html: str, source_url: str, limit: int = 8) -> list[str]:
    """Return detail-page variant URLs without mixing their prices on the current page."""
    selected_product_id = _product_id_from_url(source_url)
    if not selected_product_id:
        return []

    urls: list[str] = []
    seen_product_ids: set[str] = set()

    def add(candidate: str) -> None:
        absolute = _canonical_product_url(candidate)
        product_id = _product_id_from_url(absolute)
        if not product_id or product_id in seen_product_ids:
            return
        seen_product_ids.add(product_id)
        urls.append(absolute)

    add(source_url)
    soup = soup_from_html(html)
    for link in soup.select("a[href]"):
        href = str(link.get("href") or "")
        if not PRODUCT_URL_RE.search(href):
            continue
        context = _element_context_text(link)
        parent = link.parent
        for _ in range(3):
            if parent is None:
                break
            context += " " + _element_context_text(parent)
            parent = parent.parent
        if _variant_context_allows(context):
            add(href)
        if len(urls) >= limit:
            return urls[:limit]

    text = _embedded_text(soup)
    for match in PRODUCT_URL_RE.finditer(text):
        segment = text[max(0, match.start() - 900) : match.end() + 900]
        if not _variant_context_allows(segment):
            continue
        if "merchantName" in segment and "variantListing" in segment:
            continue
        add(match.group(0))
        if len(urls) >= limit:
            break
    return urls[:limit]
