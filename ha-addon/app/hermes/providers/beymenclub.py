"""Beymen Club'a ozel fiyat ve beden stok okuyucu.

Network benzeri sepet kampanyalarini onceleyerek, normal fiyatla karismasini
engeller. Bu kurallar baska saglayicilardan tamamen bagimsiz tutulur.
"""

import json
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import requests

from ..constants import CHROME_CLIENT_HINTS, CHROME_USER_AGENT, SITE_BEYMENCLUB
from ..errors import HermesError, HttpStatusHermesError, OutOfStockHermesError
from ..logging_utils import log
from ..models import OfferResult
from ..utils import parse_decimal, referer_for_url
from .base import (
    Provider,
    extract_jsonld_product,
    extract_price_from_meta,
    extract_price_from_scripts,
    extract_price_from_selectors,
    extract_title,
    soup_from_html,
)
from .http import curl_requests, decode_response_text, read_site_html
from .size_availability import requested_size_state, size_matches

BEYMENCLUB_SELECTORS = [
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

BEYMENCLUB_BASKET_PATTERNS = [
    re.compile(
        rf"(?<!\d)\d+\s*ve\s*(?:üzeri|uzeri)(?:\s+adet)?(?:\s+(?:için|icin))?\s*"
        rf"(?P<price>{PRICE_PATTERN})\s*tl",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        rf"\d+\s*ve\s*(?:üzeri|uzeri).*?sepette\s*"
        rf"(?P<price>{PRICE_PATTERN})\s*tl",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        rf"sepette\s*(?P<price>{PRICE_PATTERN})\s*tl",
        re.IGNORECASE | re.DOTALL,
    ),
]


def _extract_basket_price(soup):
    """Return the cheapest explicit basket campaign price, if present."""
    text = soup.get_text(" ", strip=True)
    candidates = []
    for pattern in BEYMENCLUB_BASKET_PATTERNS:
        for match in pattern.finditer(text):
            try:
                candidates.append(parse_decimal(match.group("price")))
            except HermesError:
                continue
    return min(candidates) if candidates else None


def extract_product_id(html: str) -> int | None:
    """Read the product id embedded by Beymen Club's server-rendered page."""
    match = re.search(r"\bBEYMEN\.productMain\s*=\s*", html)
    if not match:
        return None
    try:
        payload, _ = json.JSONDecoder().raw_decode(html[match.end() :].lstrip())
    except json.JSONDecodeError:
        return None
    product_id = payload.get("productId") if isinstance(payload, dict) else None
    try:
        return int(product_id)
    except (TypeError, ValueError):
        return None


def requested_size_state_from_summary(
    summary_payload: dict, requested_size: str
) -> tuple[bool, bool]:
    """Use Beymen Club's productsummary data as the authoritative size stock source."""
    result = summary_payload.get("result") if isinstance(summary_payload, dict) else None
    sizes = result.get("sizes") if isinstance(result, dict) else None
    if not isinstance(sizes, list):
        return False, False

    found = False
    available = False
    for size in sizes:
        if not isinstance(size, dict) or not size_matches(size.get("sizeName"), requested_size):
            continue
        found = True
        in_stock = size.get("inStock")
        stock_quantity = size.get("stockQuantity")
        available = available or bool(in_stock) and (stock_quantity is None or stock_quantity > 0)
    return found, available


def extract_offer(html: str, source_url: str = "") -> OfferResult:
    soup = soup_from_html(html)
    jsonld_title, jsonld_price = extract_jsonld_product(soup)
    title = jsonld_title or extract_title(soup) or "Beymen Club urunu"

    for price in (
        _extract_basket_price(soup),
        jsonld_price,
        extract_price_from_meta(soup),
        extract_price_from_selectors(soup, BEYMENCLUB_SELECTORS),
        extract_price_from_scripts(html),
    ):
        if price is not None:
            return OfferResult(title=title, price=price, seller=None)

    raise HermesError("Beymen Club sayfasindan fiyat bulunamadi.")


def extract_offers(
    html: str,
    source_url: str = "",
    size: str = "",
    size_summary: dict | None = None,
) -> list[OfferResult]:
    """Read Beymen Club's price only when the requested apparel size is available."""
    requested_size = str(size or "").strip()
    if not requested_size:
        return [extract_offer(html, source_url=source_url)]
    found, available = requested_size_state_from_summary(size_summary or {}, requested_size)
    if not found:
        # Retain a markup fallback only if Beymen Club removes the summary API.
        found, available = requested_size_state(html, requested_size)
    soup = soup_from_html(html)
    title = extract_jsonld_product(soup)[0] or extract_title(soup) or "Beymen Club urunu"
    if not found:
        raise OutOfStockHermesError(
            f"Beymen Club beden bulunamadı: {requested_size}", title, source_url
        )
    if not available:
        raise OutOfStockHermesError(
            f"Beymen Club beden stokta değil: {requested_size}", title, source_url
        )
    return [extract_offer(html, source_url=source_url)]


# ---------------------------------------------------------------------------
# Fetching: Beymen Club's WAF needs browser-shaped headers on page and size API.
# ---------------------------------------------------------------------------


def beymenclub_headers(url: str) -> Dict[str, str]:
    """Return the browser-shaped headers required by Beymen Club's WAF."""
    return {
        "User-Agent": CHROME_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "max-age=0",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        **CHROME_CLIENT_HINTS,
        "Referer": referer_for_url(url),
    }


def _is_usable_response(response) -> bool:
    text = decode_response_text(response)
    return "BEYMEN.productMain" in text or "m-priceWrapper" in text or "o-productDetail" in text


def _checked_page(response, url: str):
    if response.status_code == 403:
        raise HttpStatusHermesError(403, url)
    response.raise_for_status()
    if not _is_usable_response(response):
        raise HermesError("Beymen Club ürün sayfası beklenen fiyat verisini içermiyor.")
    return response


def _get_page(session, url: str, timeout: int):
    return _checked_page(session.get(url, headers=beymenclub_headers(url), timeout=timeout, allow_redirects=True), url)


def _attempt_label(method: str, exc: Exception) -> str:
    return f"{method}:{getattr(exc, 'status_code', None) or exc.__class__.__name__}"


def fetch_beymenclub_page(session: requests.Session, url: str, timeout: int):
    """Fetch Beymen Club through its WAF-friendly, site-specific request path."""
    last_error: Optional[Exception] = None
    attempts: List[str] = []
    for method, client in (("requests", session), ("requests_fresh", requests.Session())):
        try:
            return _get_page(client, url, timeout)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            attempts.append(_attempt_label(method, exc))
    if curl_requests is not None:
        try:
            response = curl_requests.Session().get(
                url, headers=beymenclub_headers(url), timeout=timeout, allow_redirects=True, impersonate="chrome124"
            )
            return _checked_page(response, url)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            attempts.append(_attempt_label("curl", exc))
    if attempts:
        log(f"Beymen Club teşhis: deneme={len(attempts)} | akis={' > '.join(attempts)} | url={url}")
    if last_error is not None:
        raise last_error
    raise HttpStatusHermesError(0, url)


def _summary_headers(product_url: str) -> Dict[str, str]:
    parsed = urlsplit(product_url)
    headers = beymenclub_headers(product_url)
    headers.update(
        {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": f"{parsed.scheme}://{parsed.netloc}",
            "Referer": product_url,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    return headers


def _get_size_summary(session, product_url: str, product_id: int, timeout: int) -> Dict[str, Any]:
    parsed = urlsplit(product_url)
    summary_url = f"{parsed.scheme}://{parsed.netloc}/sf-api/api/product/{product_id}/productsummary"
    response = session.post(summary_url, headers=_summary_headers(product_url), timeout=timeout, allow_redirects=True)
    if response.status_code == 403:
        raise HttpStatusHermesError(403, summary_url)
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise HermesError("Beymen Club beden stok verisi okunamadı.") from exc
    if not isinstance(payload, dict):
        raise HermesError("Beymen Club beden stok verisi beklenen biçimde dönmedi.")
    return payload


def fetch_beymenclub_size_summary(session: requests.Session, product_url: str, product_id: int, timeout: int) -> Dict[str, Any]:
    """Read the protected size API using the same browser identity as the product page."""
    attempts: List[str] = []
    last_error: Optional[Exception] = None
    try:
        return _get_size_summary(session, product_url, product_id, timeout)
    except Exception as exc:  # noqa: BLE001
        last_error = exc
        attempts.append(_attempt_label("requests", exc))
    fresh_session = requests.Session()
    try:
        _get_page(fresh_session, product_url, timeout)
        return _get_size_summary(fresh_session, product_url, product_id, timeout)
    except Exception as exc:  # noqa: BLE001
        last_error = exc
        attempts.append(_attempt_label("requests_fresh", exc))
    if curl_requests is not None:
        try:
            curl_session = curl_requests.Session()
            response = curl_session.get(
                product_url, headers=beymenclub_headers(product_url), timeout=timeout,
                allow_redirects=True, impersonate="chrome124",
            )
            _checked_page(response, product_url)
            return _get_size_summary(curl_session, product_url, product_id, timeout)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            attempts.append(_attempt_label("curl", exc))
    if attempts:
        log(f"Beymen Club beden teşhis: deneme={len(attempts)} | akis={' > '.join(attempts)} | url={product_url}")
    if isinstance(last_error, HttpStatusHermesError):
        raise HermesError("Beymen Club beden stok verisine erişim reddedildi; sonraki turda yeniden denenecek.")
    if last_error is not None:
        raise HermesError(f"Beymen Club beden stok verisi okunamadı: {last_error}") from last_error
    raise HermesError("Beymen Club beden stok verisi okunamadı.")


class BeymenClubProvider(Provider):
    site = SITE_BEYMENCLUB

    def read(self, watch, ctx, outcome):
        html = read_site_html(fetch_beymenclub_page(ctx.session, watch.url, ctx.timeout), "Beymen Club")
        size_summary = None
        if watch.size:
            product_id = extract_product_id(html)
            if product_id is None:
                raise HermesError("Beymen Club ürün kimliği bulunamadı; beden durumu doğrulanamadı.")
            size_summary = fetch_beymenclub_size_summary(ctx.session, watch.url, product_id, ctx.timeout)
        offers = extract_offers(html, source_url=watch.url, size=watch.size, size_summary=size_summary)
        if watch.size:
            log(f"Beymen Club beden kontrol edildi: {watch.name or watch.url} | beden={watch.size} | adet={len(offers)}")
        return offers
