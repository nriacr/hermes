import json
import re
from typing import Any

from ..errors import HermesError
from ..logging_utils import log
from ..models import OfferResult
from ..utils import format_tl, normalize_offer_text, parse_decimal, repair_mojibake
from .base import extract_price_from_selectors, extract_title, soup_from_html

PRICE_RE = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}\s*TL", re.IGNORECASE)
STOP_MARKERS = ("urun bilgileri", "urun aciklamasi")
SELLER_NOISE = (
    "diger saticilar",
    "tumunu gor",
    "tahmini",
    "urune git",
    "sepete ekle",
    "premium",
    "kampanya",
    "degerlendirme",
    "saticiya sor",
    "takip et",
    "kapinda",
    "fiyat",
    "renk",
    "secenek",
    "ozellik",
    "urun ozelligi",
    "urun ozelligi secimi",
    "ozelligi",
    "puan",
)
SELLER_EXACT_NOISE = {
    "gri",
    "uzay gri",
    "siyah",
    "uzay siyahi",
    "beyaz",
    "mavi",
    "lacivert",
    "yesil",
    "kirmizi",
    "turuncu",
    "mor",
    "sari",
    "pembe",
    "gumus",
    "silver",
    "gold",
    "gray",
    "grey",
    "black",
    "white",
    "blue",
    "green",
    "red",
    "purple",
    "pink",
    "urun ozelligi",
    "urun ozelligi secimi",
    "secenek",
    "secenek secimi",
    "renk",
    "kapasite",
    "hafiza",
}
SELLER_PATTERN_NOISE = (
    re.compile(r"^\d+\s*(gb|tb|mb|inch|inc|in|hz|w|mah|cm|mm)$", re.IGNORECASE),
    re.compile(r"^\d+\s*-\s*\d+$", re.IGNORECASE),
)
HEPSIBURADA_SELECTORS = [
    "[data-test-id='price-current-price']",
    "[data-test-id='price-current-price'] span",
    "[data-test-id='price']",
    "[itemprop='price']",
    "#offering-price",
    ".product-price",
    ".price",
]


def _is_noise_seller(normalized: str) -> bool:
    if not normalized:
        return True
    if normalized in SELLER_EXACT_NOISE:
        return True
    return any(pattern.search(normalized) for pattern in SELLER_PATTERN_NOISE)


def _clean_seller(raw: Any) -> str:
    seller = repair_mojibake(raw).strip(" :-")
    seller = re.sub(r"\s+\d+(?:[,.]\d+)?$", "", seller).strip(" :-")
    normalized = normalize_offer_text(seller)
    letters_only = re.sub(r"[^A-Za-zÇĞİÖŞÜçğıöşü]", "", seller)
    if not seller or len(seller) > 64 or len(letters_only) < 3:
        return ""
    if PRICE_RE.search(seller):
        return ""
    if any(token in normalized for token in SELLER_NOISE):
        return ""
    if _is_noise_seller(normalized):
        return ""
    return seller


def _visible_lines(soup):
    lines = []
    for raw in soup.get_text("\n", strip=True).splitlines():
        line = repair_mojibake(raw).strip()
        if not line:
            continue
        if any(marker in normalize_offer_text(line) for marker in STOP_MARKERS):
            break
        lines.append(line)
    return lines


def _main_seller(lines):
    joined = "\n".join(lines)
    match = re.search(r"Satıcı\s*:?\s*([^\n]{2,80})", joined, re.IGNORECASE)
    if match:
        value = match.group(1)
        value = re.split(r"(Takip et|Saticiya sor|Degerlendirme)", value, maxsplit=1, flags=re.IGNORECASE)[0]
        seller = _clean_seller(value.replace("Resmi Satıcı", ""))
        if seller:
            return seller
    for i, line in enumerate(lines):
        if normalize_offer_text(line) == "satici" and i + 1 < len(lines):
            seller = _clean_seller(lines[i + 1].replace("Resmi Satıcı", ""))
            if seller:
                return seller
    return "Hepsiburada"


def _nearest_seller_before(lines, price_index: int):
    ignore = ("tahmini", "kapinda", "urune git", "sepete ekle", "diger saticilar", "tumunu gor")
    for back in range(1, 15):
        idx = price_index - back
        if idx < 0:
            break
        prev = lines[idx]
        if any(token in normalize_offer_text(prev) for token in ignore):
            continue
        candidate = _clean_seller(prev)
        if candidate:
            return candidate
    return ""


def _collect_line_candidates(lines):
    candidates = []
    for idx, line in enumerate(lines):
        for match in PRICE_RE.finditer(line):
            seller = _nearest_seller_before(lines, idx)
            if seller:
                candidates.append((seller, match.group(0)))
    return candidates


def _iter_dicts(root):
    stack = [root]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            yield current
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)


def _collect_json_payloads(soup):
    payloads = []
    for tag in soup.find_all("script"):
        raw = tag.string or tag.get_text("", strip=True)
        if not raw or len(raw) < 3:
            continue
        text = str(raw).strip()
        if text.startswith("{") or text.startswith("["):
            try:
                payloads.append(json.loads(text))
                continue
            except Exception:
                pass
        if "__NEXT_DATA__" in text and "{" in text and "}" in text:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    payloads.append(json.loads(text[start:end]))
                except Exception:
                    continue
    return payloads


def _mapping_seller(mapping):
    for key in (
        "merchantName",
        "merchant_name",
        "sellerName",
        "seller_name",
        "storeName",
        "store_name",
        "vendorName",
        "vendor_name",
    ):
        value = mapping.get(key)
        if isinstance(value, str):
            seller = _clean_seller(value)
            if seller:
                return seller
    merchant = mapping.get("merchant")
    if isinstance(merchant, dict):
        return _mapping_seller(merchant)
    seller_obj = mapping.get("seller")
    if isinstance(seller_obj, dict):
        return _mapping_seller(seller_obj)
    return ""


def _mapping_prices(mapping):
    prices = []
    for key in (
        "price",
        "finalPrice",
        "final_price",
        "salePrice",
        "sale_price",
        "currentPrice",
        "current_price",
        "amount",
        "value",
        "formattedPrice",
        "formatted_price",
    ):
        value = mapping.get(key)
        if isinstance(value, (int, float)):
            if 100 <= float(value) <= 1000000:
                prices.append(parse_decimal(str(value)))
            continue
        if not isinstance(value, str):
            continue
        for match in PRICE_RE.finditer(value):
            try:
                prices.append(parse_decimal(match.group(0)))
            except HermesError:
                continue
    return prices


def _collect_json_candidates(soup):
    candidates = []
    for payload in _collect_json_payloads(soup):
        for mapping in _iter_dicts(payload):
            seller = _mapping_seller(mapping)
            if not seller:
                continue
            for price in _mapping_prices(mapping):
                if 100 <= price <= 1000000:
                    candidates.append((seller, price))
    return candidates


def extract_offer(html: str) -> OfferResult:
    soup = soup_from_html(html)
    title = extract_title(soup) or "Hepsiburada urunu"
    lines = _visible_lines(soup)
    fallback_seller = _main_seller(lines)
    offers = []

    main_price = extract_price_from_selectors(soup, HEPSIBURADA_SELECTORS)
    if main_price is not None:
        offers.append((fallback_seller, main_price))

    for seller, raw_price in _collect_line_candidates(lines[:380]):
        try:
            price = parse_decimal(raw_price)
        except HermesError:
            continue
        if 100 <= price <= 1000000:
            offers.append((seller, price))

    offers.extend(_collect_json_candidates(soup))

    deduped = {}
    for seller, price in offers:
        cleaned = _clean_seller(seller) or fallback_seller
        prev = deduped.get(cleaned)
        if prev is None or price < prev:
            deduped[cleaned] = price

    if not deduped:
        raise HermesError("Hepsiburada sayfasindan fiyat bulunamadi.")

    sorted_offers = sorted(deduped.items(), key=lambda item: item[1])
    preview = " | ".join(f"{seller}={format_tl(price)}" for seller, price in sorted_offers)
    log(f"Hepsiburada teklifleri: {preview}")

    best_seller, best_price = sorted_offers[0]
    return OfferResult(title=f"{repair_mojibake(title)} ({best_seller})", price=best_price, seller=best_seller)
