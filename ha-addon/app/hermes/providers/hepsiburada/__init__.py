"""Hepsiburada: product, variant and search pages with campaign-aware prices."""

import contextlib
import threading
import time
from collections import Counter
from decimal import Decimal
from typing import Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from ...constants import CHROME_CLIENT_HINTS, SITE_HEPSIBURADA
from ...errors import HermesError, HttpStatusHermesError
from ...logging_utils import log
from ...traffic import CountedSession, count_curl
from ...models import OfferResult, ProductPreview, VariantPreview
from ...utils import build_headers, format_tl, is_hepsiburada_search_url, normalize_offer_text
from ..base import Provider, excluded_term_in_title, outside_variant_filter
from ..http import curl_requests, decode_response_text, has_generic_challenge, read_site_html
from . import parser
from .parser import clean_display_title, is_product_url

HOME_URL = "https://www.hepsiburada.com/"
SECURITY_PAGE_MARKERS = ("hepsiburada guvenlik", "hbblockandcaptcha", "static hepsiburada net security")
UNEXPECTED_PAGE_MESSAGE = "Hepsiburada linki beklenen ürün veya arama sayfası yerine farklı bir sayfaya yönlendi."


def is_challenge_page(html: str) -> bool:
    normalized = normalize_offer_text(html)
    return any(marker in normalized for marker in SECURITY_PAGE_MARKERS) or has_generic_challenge(html)


def hepsiburada_headers(url: str):
    headers = build_headers(url)
    headers.update(
        {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Referer": HOME_URL,
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            **CHROME_CLIENT_HINTS,
        }
    )
    return headers


def _is_product_path(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return "-p-" in path or "-pm-" in path


def _is_search_request(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.path.rstrip("/").lower() == "/ara" or "q=" in parsed.query.lower()


def _clean_search_url(url: str) -> str:
    parsed = urlsplit(url)
    kept_params = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key == "q"]
    if not kept_params:
        return url
    return urlunsplit((parsed.scheme, parsed.netloc, "/ara", urlencode(kept_params), ""))


def _add_query(url: str, query: str) -> str:
    parsed = urlsplit(url)
    new_query = "&".join(part for part in (parsed.query.strip("&"), query) if part)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment))


def url_variants(url: str) -> List[str]:
    """Equivalent addresses tried in order when Hepsiburada redirects or blocks one."""
    variants: List[str] = []

    def add(candidate: str) -> None:
        if candidate and candidate not in variants:
            variants.append(candidate)

    add(url)
    if _is_search_request(url):
        add(_clean_search_url(url))
        return variants
    clean_url = url.split("?", 1)[0]
    add(clean_url)
    if _is_product_path(clean_url):
        add(_add_query(clean_url, "magaza=Hepsiburada"))
    if "-pm-" in clean_url:
        add(clean_url.replace("-pm-", "-p-", 1))
    if "-p-" in clean_url:
        add(clean_url.replace("-p-", "-pm-", 1))
    return variants


def _is_usable_response(response) -> bool:
    final_url = getattr(response, "url", "") or ""
    lowered = decode_response_text(response).lower()
    if _is_search_request(final_url):
        return "hepsiburada" in lowered and ("ara" in lowered or "ürün" in lowered or "urun" in lowered)
    if not _is_product_path(final_url):
        return False
    return "sepete ekle" in lowered or "satıcı" in lowered or "satici" in lowered or "stok kodu" in lowered


def _checked(response, candidate: str):
    if response.status_code == 403:
        raise HttpStatusHermesError(403, candidate)
    response.raise_for_status()
    if not _is_usable_response(response):
        raise HermesError(UNEXPECTED_PAGE_MESSAGE)
    return response


def _attempt(method: str, candidate: str, exc: Exception) -> str:
    return f"{method}:{getattr(exc, 'status_code', None) or exc.__class__.__name__}:{candidate[:100]}"


WARMED_ATTRIBUTE = "hermes_hepsiburada_warmed"
ATTEMPT_LOG_SECONDS = 600
_attempts: Counter = Counter()
_attempts_lock = threading.Lock()
_attempts_since = time.monotonic()


def note_attempt(method: str, outcome) -> None:
    """Tally every network attempt by method and result; one summary line every ten minutes (measurement only)."""
    global _attempts_since
    line = ""
    with _attempts_lock:
        _attempts[(method, str(outcome))] += 1
        elapsed = time.monotonic() - _attempts_since
        if elapsed >= ATTEMPT_LOG_SECONDS:
            by_method: dict = {}
            for (name, result), count in sorted(_attempts.items()):
                by_method.setdefault(name, []).append(f"{result}={count}")
            line = (f"Hepsiburada deneme özeti (son {elapsed / 60:.0f} dk): "
                    + " | ".join(f"{name}: {', '.join(results)}" for name, results in by_method.items()))
            _attempts.clear()
            _attempts_since = time.monotonic()
    if line:
        log(line)


# url -> the address that curl_cffi last read successfully. A later read of the same url tries only that
# one first; any failure there forgets it and the full original order below runs unchanged.
_curl_winner: dict = {}


def _curl_first(url: str, timeout: int):
    """The page from the address curl_cffi read before, or None (nothing remembered or it failed)."""
    candidate = _curl_winner.get(url)
    if not candidate or curl_requests is None:
        return None
    try:
        page = _checked(
            count_curl(curl_requests.Session().get(
                candidate, headers=hepsiburada_headers(candidate), timeout=timeout,
                allow_redirects=True, impersonate="chrome124",
            )),
            candidate,
        )
        note_attempt("curl_first", "ok")
        return page
    except Exception as exc:  # noqa: BLE001
        note_attempt("curl_first", _outcome(exc))
        _curl_winner.pop(url, None)
        return None


def _outcome(exc: Exception):
    return getattr(exc, "status_code", None) or exc.__class__.__name__


def _warm_up(session: requests.Session, timeout: int) -> None:
    """Visit the home page once per session; the cookies it sets stay in the session.

    A session whose last page read failed (or whose warm-up failed) warms up again on its next read.
    """
    if getattr(session, WARMED_ATTRIBUTE, False):
        return
    with contextlib.suppress(Exception):  # a missing warm-up cookie is not fatal
        response = session.get(HOME_URL, headers=hepsiburada_headers(HOME_URL), timeout=timeout, allow_redirects=True)
        note_attempt("home", getattr(response, "status_code", "?"))
        if getattr(response, "status_code", 0) < 400:
            setattr(session, WARMED_ATTRIBUTE, True)


def _curl_page(url: str, variants: List[str], timeout: int, attempts: List[str]):
    """The first address curl_cffi reads (remembered for the next read), or (None, last error)."""
    last_error: Optional[Exception] = None
    try:
        curl_session = curl_requests.Session()
        for candidate in variants:
            try:
                page = _checked(
                    count_curl(curl_session.get(
                        candidate, headers=hepsiburada_headers(candidate), timeout=timeout,
                        allow_redirects=True, impersonate="chrome124",
                    )),
                    candidate,
                )
                note_attempt("curl", "ok")
                _curl_winner[url] = candidate
                return page, None
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                note_attempt("curl", _outcome(exc))
                attempts.append(_attempt("curl", candidate, exc))
    except Exception as exc:  # noqa: BLE001
        last_error = exc
        attempts.append(_attempt("curl_setup", url, exc))
    return None, last_error


def fetch_hepsiburada_page(session: requests.Session, url: str, timeout: int):
    """curl_cffi (a browser TLS fingerprint) first; plain requests only when curl is missing or fails.

    Measured 2026-10-28: Hepsiburada answered every plain-requests read and home-page warm-up with 403
    (456 in 24 hours) while curl_cffi read every page; trying requests first only added blocked requests.
    """
    remembered = _curl_first(url, timeout)
    if remembered is not None:
        return remembered
    attempts: List[str] = []
    variants = url_variants(url)
    last_error: Optional[Exception] = None
    if curl_requests is not None:
        page, last_error = _curl_page(url, variants, timeout, attempts)
        if page is not None:
            return page
    _warm_up(session, timeout)
    for method, client in (("requests", session), ("requests_fresh", CountedSession())):
        for candidate in variants:
            try:
                page = _checked(
                    client.get(candidate, headers=hepsiburada_headers(candidate), timeout=timeout, allow_redirects=True),
                    candidate,
                )
                note_attempt(method, "ok")
                return page
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                note_attempt(method, _outcome(exc))
                attempts.append(_attempt(method, candidate, exc))
                if client is session:
                    setattr(session, WARMED_ATTRIBUTE, False)  # the next read starts with a fresh warm-up
    if attempts:
        log(f"Hepsiburada teşhis: deneme={len(attempts)} | akis={' > '.join(attempts[-6:])} | url={url}")
    if last_error:
        raise last_error
    raise HttpStatusHermesError(0, url)


class HepsiburadaProvider(Provider):
    site = SITE_HEPSIBURADA
    alert_shows_seller = True
    card_options = ("name", "minimum_price", "official_seller_only", "exclude_terms", "variant_filter")

    def __init__(self) -> None:
        # SKU -> variant values ({"Renk": "Abis", "Kapasite": "256 GB"}) of the families read so far.
        self.families: Dict[str, Dict[str, str]] = {}

    def inspect(self, url, ctx):
        """The page's own variant list (one request): every SKU with its values, price and stock."""
        if self.is_search_url(url):
            return super().inspect(url, ctx)
        html = self._page(ctx, url)
        self._remember_family(html)
        family = parser.variant_family(html)
        if not family:
            return super().inspect(url, ctx)
        dimensions: Dict[str, List[str]] = {}
        variants = []
        for entry in family:
            for name, value in entry["values"].items():
                if value not in dimensions.setdefault(name, []):
                    dimensions[name].append(value)
            price = Decimal(str(entry["price"])) if entry["price"] not in (None, "") else None
            variants.append(VariantPreview(" / ".join(entry["values"].values()), price, entry["in_stock"], entry["values"]))
        try:
            title = clean_display_title(parser.extract_offer(html, source_url=url).title)
        except HermesError:
            title = ""
        return ProductPreview(self.site, title, variants, dimensions)

    def _remember_family(self, html: str) -> None:
        """SKU -> its variant values, so a chosen colour/capacity is matched by the offer's own SKU."""
        for entry in parser.variant_family(html):
            self.families[entry["sku"]] = entry["values"]

    def keeps_offer(self, watch, offer) -> bool:
        """The chosen variant values (by SKU when known), then the own-seller option (Hepsiburada itself)."""
        if watch.variant_filter:
            values = self.families.get(parser.product_id_from_url(offer.url or ""))
            if values is not None:
                missing = next((name for name, chosen in watch.variant_filter.items()
                                if name in values and values[name] not in chosen), "")
            else:
                missing = outside_variant_filter(watch, offer.title)
            if missing:
                log(f"Hepsiburada varyant seçimi nedeniyle sonuç atlandı ({missing}): {offer.title}")
                return False
        if not watch.official_seller_only or normalize_offer_text(offer.seller or "") == "hepsiburada":
            return True
        log(f"Hepsiburada satıcısı filtresi nedeniyle sonuç atlandı: {offer.seller or 'satıcı bilgisi yok'} | {offer.title}")
        return False

    def is_search_url(self, url: str) -> bool:
        return is_hepsiburada_search_url(url)

    def display_title(self, title: str) -> str:
        return clean_display_title(title)

    def _page(self, ctx, url: str) -> str:
        return read_site_html(fetch_hepsiburada_page(ctx.session, url, ctx.timeout), "Hepsiburada", is_challenge_page)

    def read(self, watch, ctx, outcome):
        html = self._page(ctx, watch.url)
        if watch.variant_filter:
            self._remember_family(html)
        if not is_product_url(watch.url):
            offers = parser.extract_search_offers(html, source_url=watch.url, limit=self._scan_limit(watch))
            offers = self._with_variant_titles(ctx, offers)
            log(f"Hepsiburada arama kartları okundu: {watch.name or watch.url} | adet={len(offers)}")
            return offers
        return self._product_offers(ctx, watch, html)

    @staticmethod
    def _scan_limit(watch) -> int:
        return max(1, min(int(watch.max_items_to_scan or 1), 100))

    def _with_variant_titles(self, ctx, offers: List[OfferResult]) -> List[OfferResult]:
        """Search cards omit the selected variant; its product page names it."""
        enriched: List[OfferResult] = []
        for offer in offers:
            if not offer.url or not is_product_url(offer.url):
                enriched.append(offer)
                continue
            try:
                html = self._page(ctx, offer.url)
                variant_label = parser.extract_selected_variant_label(html) or parser.extract_embedded_variant_label(html, offer.url)
                title = clean_display_title(parser.title_with_variant_label(offer.title, variant_label))
                enriched.append(OfferResult(title=title, price=offer.price, seller=offer.seller, url=offer.url))
            except Exception as exc:  # noqa: BLE001
                log(f"Hepsiburada arama kartı varyasyon etiketi tamamlanamadı: {offer.url} | {exc}")
                enriched.append(offer)
        return enriched

    def _product_offers(self, ctx, watch, html: str):
        """Yields each variant's offer as soon as it is read, so the table refreshes variant by variant."""
        variant_urls = parser.extract_variant_urls(html, watch.url, self._scan_limit(watch))
        if len(variant_urls) > 1:
            log(f"Hepsiburada varyasyonları bulundu: {watch.name or watch.url} | adet={len(variant_urls)}")

        found = 0
        errors: List[str] = []
        seen_offer_keys: set[tuple[str, str, str, str]] = set()
        for variant_url in variant_urls or [watch.url]:
            try:
                variant_label = parser.extract_embedded_variant_label(html, variant_url)
                if variant_label and watch.excluded_terms and variant_url != watch.url:
                    # A variant whose own label carries an exclusion is dropped anyway; it is not opened.
                    # Only the label is checked: the main page's title names the main variant, not this one.
                    term = excluded_term_in_title(watch, variant_label)
                    if term:
                        log(f"Hepsiburada varyasyonu açılmadı (hariç tut: {term}): {variant_label}")
                        continue
                offer, variant_html = self._variant_offer(ctx, watch, html, variant_url)
                if variant_html:
                    variant_label = parser.extract_selected_variant_label(variant_html) or variant_label
                offer_title = parser.title_with_variant_label(offer.title, variant_label)
                variant_identity = normalize_offer_text(variant_label) or parser.product_id_from_url(variant_url)
                dedupe_key = (
                    variant_identity,
                    normalize_offer_text(offer.seller or ""),
                    str(offer.price),
                    normalize_offer_text(offer_title),
                )
                if dedupe_key in seen_offer_keys:
                    log(
                        "Hepsiburada varyasyon kopyası atlandı: "
                        f"{variant_label or offer_title} | {offer.seller or '-'} | {format_tl(offer.price, with_currency=True)}"
                    )
                    continue
                seen_offer_keys.add(dedupe_key)
                found += 1
                yield OfferResult(title=offer_title, price=offer.price, seller=offer.seller, url=variant_url)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{variant_url} | {exc}")
                log(f"Hepsiburada varyasyon okunamadı: {variant_url} | {exc}")

        if found:
            return
        if errors:
            raise HermesError(errors[-1])
        raise HermesError("Hepsiburada sayfasından fiyat bulunamadı.")

    def _variant_offer(self, ctx, watch, html: str, variant_url: str):
        """Return the variant's payable offer and, when fetched, its own page."""
        offer, variant_html = self._page_offer(ctx, watch, html, variant_url)
        if not variant_html:
            return offer, variant_html
        shown = parser.shown_seller(variant_html, variant_url)
        if normalize_offer_text(offer.seller or "") == normalize_offer_text(shown):
            return offer, variant_html
        live_prices = self._live_seller_prices(ctx, variant_url)
        verified = parser.extract_verified_offer(variant_html, variant_url, shown, live_prices)
        if normalize_offer_text(verified.seller or "") != normalize_offer_text(offer.seller or ""):
            log(f"Hepsiburada satıcısı canlı listede doğrulanmadı, sayılmadı: {offer.seller} | {format_tl(offer.price, with_currency=True)}")
        return verified, variant_html

    def _live_seller_prices(self, ctx, url: str) -> Dict[str, Decimal]:
        """The site's live seller list; empty when it cannot be read, so only the shown seller counts."""
        api_url = f"{HOME_URL}api/v1/product/listings/{parser.product_id_from_url(url)}"
        headers = hepsiburada_headers(url)
        headers.pop("Upgrade-Insecure-Requests", None)
        headers.pop("Sec-Fetch-User", None)
        headers.update({"Accept": "application/json", "Referer": url, "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors"})
        try:
            response = count_curl(curl_requests.Session().get(api_url, headers=headers, timeout=ctx.timeout, impersonate="chrome124"))
            response.raise_for_status()
            prices = parser.live_seller_prices(response.json())
        except Exception as exc:  # noqa: BLE001
            log(f"Hepsiburada canlı satıcı listesi okunamadı: {api_url} | {exc}")
            return {}
        if not prices:
            log(f"Hepsiburada canlı satıcı listesi boş: {api_url}")
        return prices

    def _page_offer(self, ctx, watch, html: str, variant_url: str):
        embedded_offer = parser.extract_embedded_variant_offer(html, variant_url)
        if variant_url == watch.url:
            return _lower_offer(embedded_offer, parser.extract_offer(html, source_url=variant_url)), html
        if not embedded_offer:
            variant_html = self._page(ctx, variant_url)
            return parser.extract_offer(variant_html, source_url=variant_url), variant_html
        variant_html = ""
        try:
            # The variant's own page can show a lower Premium/cart price.
            variant_html = self._page(ctx, variant_url)
            return _lower_offer(embedded_offer, parser.extract_offer(variant_html, source_url=variant_url)), variant_html
        except Exception as exc:  # noqa: BLE001
            log(f"Hepsiburada varyasyon sayfası premium kontrolü atlandı: {variant_url} | {exc}")
            return embedded_offer, variant_html


def _lower_offer(first: Optional[OfferResult], second: Optional[OfferResult]) -> OfferResult:
    if first is None and second is None:
        raise HermesError("Hepsiburada sayfasından fiyat bulunamadı.")
    if first is None:
        return second
    if second is None:
        return first
    return second if second.price < first.price else first
