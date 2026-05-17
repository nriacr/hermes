import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Optional
from urllib.parse import urljoin, urlsplit

from ..errors import HermesError
from ..logging_utils import log
from ..models import OfferResult
from ..utils import format_tl, normalize_offer_text, parse_decimal, repair_mojibake
from .base import extract_price_from_selectors, extract_title, soup_from_html

BASE_URL = "https://www.hepsiburada.com"
MIN_PRICE = Decimal("50")
MAX_PRICE = Decimal("1000000")
PRICE_RE = re.compile(r"\d{1,3}(?:\.\d{3})*(?:,\d{2})?\s*TL", re.IGNORECASE)
PRODUCT_URL_RE = re.compile(r"/(?:[^\s'\"]+)-(?:p|pm)-[A-Z0-9]+", re.IGNORECASE)
DETAIL_PRICE_SELECTORS = [
    "[data-test-id='price-current-price']",
    "[data-test-id='price-current-price'] span",
    "[data-test-id='price']",
    "[itemprop='price']",
    "#offering-price",
    ".product-price",
    ".price",
]
DETAIL_STOP_MARKERS = ("urun bilgileri", "urun aciklamasi", "degerlendirmeler")
INSTALLMENT_MARKERS = ("peşin fiyatına", "pesin fiyatina", "taksit", " x ")
DISCOUNT_MARKERS = ("kupon", "hepsipara", "kazan", "indirim")


@dataclass
class HepsiburadaCandidate:
    title: str
    price: Decimal
    url: str
    seller: str = "Hepsiburada"


def _absolute_url(url: str) -> str:
    return urljoin(BASE_URL, repair_mojibake(url).strip())


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", repair_mojibake(value)).strip()


def _valid_price(price: Decimal) -> bool:
    return MIN_PRICE <= price <= MAX_PRICE


def _parse_price(raw_price: Any) -> Optional[Decimal]:
    if raw_price in (None, ""):
        return None
    if isinstance(raw_price, (int, float)):
        price = parse_decimal(str(raw_price))
        return price if _valid_price(price) else None
    text = _clean_text(raw_price)
    if not text:
        return None
    match = PRICE_RE.search(text)
    if match:
        text = match.group(0)
    try:
        price = parse_decimal(text)
    except HermesError:
        return None
    return price if _valid_price(price) else None


def _is_noise_price(text: str, start: int, end: int) -> bool:
    before = normalize_offer_text(text[max(0, start - 80) : start])
    after = normalize_offer_text(text[end : end + 80])
    around = f"{before} {after}"
    if any(marker in around for marker in INSTALLMENT_MARKERS):
        return True
    if any(marker in around for marker in DISCOUNT_MARKERS):
        return True
    return False


def _prices_from_text(text: str) -> list[Decimal]:
    prices = []
    clean = _clean_text(text)
    for match in PRICE_RE.finditer(clean):
        if _is_noise_price(clean, match.start(), match.end()):
            continue
        price = _parse_price(match.group(0))
        if price is not None:
            prices.append(price)
    return prices


def _title_from_card(card, link) -> str:
    link_text = _clean_text(link.get_text(" ", strip=True))
    if len(link_text) >= 8 and not link_text.isdigit():
        return link_text
    for selector in ("h1", "h2", "h3", "[title]", "[aria-label]"):
        element = card.select_one(selector)
        if not element:
            continue
        value = element.get("title") or element.get("aria-label") or element.get_text(" ", strip=True)
        title = _clean_text(value)
        if len(title) >= 8 and not title.isdigit():
            return title
    return link_text or "Hepsiburada ürünü"


def _card_for_link(link):
    current = link
    best = link
    for _ in range(8):
        if current.parent is None:
            break
        current = current.parent
        text = _clean_text(current.get_text(" ", strip=True))
        if len(text) > 2500:
            break
        best = current
        if PRICE_RE.search(text) and len(text) > 40:
            return current
    return best


def _dedupe_candidates(candidates: Iterable[HepsiburadaCandidate]) -> list[HepsiburadaCandidate]:
    deduped: dict[str, HepsiburadaCandidate] = {}
    for candidate in candidates:
        key = candidate.url or normalize_offer_text(candidate.title)
        previous = deduped.get(key)
        if previous is None or candidate.price < previous.price:
            deduped[key] = candidate
    return sorted(deduped.values(), key=lambda item: item.price)


def _search_candidates_from_dom(soup) -> list[HepsiburadaCandidate]:
    candidates = []
    for link in soup.select("a[href]"):
        href = str(link.get("href") or "")
        if not PRODUCT_URL_RE.search(href):
            continue
        url = _absolute_url(href)
        card = _card_for_link(link)
        card_text = _clean_text(card.get_text(" ", strip=True))
        prices = _prices_from_text(card_text)
        if not prices:
            continue
        candidates.append(
            HepsiburadaCandidate(
                title=_title_from_card(card, link),
                price=min(prices),
                url=url,
            )
        )
    return _dedupe_candidates(candidates)


def _iter_json_values(value: Any):
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            yield current
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)


def _json_payloads(soup) -> list[Any]:
    payloads = []
    for script in soup.find_all("script"):
        raw = script.string or script.get_text("", strip=True)
        text = str(raw or "").strip()
        if not text or "{" not in text:
            continue
        if text.startswith("{") or text.startswith("["):
            try:
                payloads.append(json.loads(text))
                continue
            except json.JSONDecodeError:
                pass
        if "__" in text:
            start = text.find("{")
            end = text.rfind("}") + 1
            if 0 <= start < end:
                try:
                    payloads.append(json.loads(text[start:end]))
                except json.JSONDecodeError:
                    continue
    return payloads


def _first_mapping_text(mapping: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return _clean_text(value)
    return ""


def _first_mapping_url(mapping: dict) -> str:
    direct = _first_mapping_text(mapping, ("url", "productUrl", "product_url", "link", "href"))
    if direct and PRODUCT_URL_RE.search(direct):
        return _absolute_url(direct)
    for value in mapping.values():
        if isinstance(value, str) and PRODUCT_URL_RE.search(value):
            return _absolute_url(PRODUCT_URL_RE.search(value).group(0))
    return ""


def _first_mapping_price(mapping: dict) -> Optional[Decimal]:
    for key in (
        "price",
        "finalPrice",
        "final_price",
        "salePrice",
        "sale_price",
        "currentPrice",
        "current_price",
        "discountedPrice",
        "amount",
        "value",
        "formattedPrice",
        "formatted_price",
    ):
        price = _parse_price(mapping.get(key))
        if price is not None:
            return price
    return None


def _search_candidates_from_json(soup) -> list[HepsiburadaCandidate]:
    candidates = []
    for payload in _json_payloads(soup):
        for mapping in _iter_json_values(payload):
            title = _first_mapping_text(mapping, ("name", "title", "productName", "product_name"))
            url = _first_mapping_url(mapping)
            price = _first_mapping_price(mapping)
            if not title or not url or price is None:
                continue
            candidates.append(HepsiburadaCandidate(title=title, price=price, url=url))
    return _dedupe_candidates(candidates)


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


def _detail_seller(lines: list[str]) -> str:
    joined = "\n".join(lines)
    match = re.search(r"Satıcı\s*:?\s*([^\n]{2,80})", joined, re.IGNORECASE)
    if match:
        seller = re.split(r"(Takip et|Satıcıya sor|Değerlendirme)", match.group(1), maxsplit=1)[0]
        seller = _clean_text(seller.replace("Resmi Satıcı", ""))
        if seller:
            return seller
    return "Hepsiburada"


def _detail_candidate(soup) -> Optional[HepsiburadaCandidate]:
    title = extract_title(soup) or "Hepsiburada ürünü"
    lines = _visible_lines_until_details(soup)
    seller = _detail_seller(lines)
    price = extract_price_from_selectors(soup, DETAIL_PRICE_SELECTORS)
    if price is None:
        line_prices = _prices_from_text("\n".join(lines[:120]))
        price = min(line_prices) if line_prices else None
    if price is None:
        return None
    return HepsiburadaCandidate(title=title, price=price, url="", seller=seller)


def _log_candidates(candidates: list[HepsiburadaCandidate]) -> None:
    preview = " | ".join(f"{item.title[:42]}={format_tl(item.price)}" for item in candidates[:8])
    log(f"Hepsiburada teklifleri: {preview}")


def extract_offer(html: str) -> OfferResult:
    soup = soup_from_html(html)
    candidates = _search_candidates_from_dom(soup)
    if not candidates:
        candidates = _search_candidates_from_json(soup)
    if not candidates:
        detail = _detail_candidate(soup)
        candidates = [detail] if detail else []
    if not candidates:
        raise HermesError("Hepsiburada sayfasından fiyat bulunamadı.")

    _log_candidates(candidates)
    best = candidates[0]
    return OfferResult(
        title=repair_mojibake(best.title),
        price=best.price,
        seller=best.seller,
        url=best.url or None,
    )
