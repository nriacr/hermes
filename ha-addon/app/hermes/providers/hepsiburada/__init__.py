"""Hepsiburada: product, variant and search pages with campaign-aware prices."""

from typing import List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from ...constants import CHROME_CLIENT_HINTS, SITE_HEPSIBURADA
from ...errors import HermesError, HttpStatusHermesError
from ...logging_utils import log
from ...models import OfferResult
from ...utils import build_headers, format_tl, is_hepsiburada_search_url, normalize_offer_text
from ..base import Provider
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


def fetch_hepsiburada_page(session: requests.Session, url: str, timeout: int):
    try:
        session.get(HOME_URL, headers=hepsiburada_headers(HOME_URL), timeout=timeout, allow_redirects=True)
    except Exception:  # noqa: BLE001 - a missing warm-up cookie is not fatal
        pass

    last_error: Optional[Exception] = None
    attempts: List[str] = []
    variants = url_variants(url)
    for method, client in (("requests", session), ("requests_fresh", requests.Session())):
        for candidate in variants:
            try:
                return _checked(
                    client.get(candidate, headers=hepsiburada_headers(candidate), timeout=timeout, allow_redirects=True),
                    candidate,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                attempts.append(_attempt(method, candidate, exc))
    if curl_requests is not None:
        try:
            curl_session = curl_requests.Session()
            for candidate in variants:
                try:
                    return _checked(
                        curl_session.get(
                            candidate, headers=hepsiburada_headers(candidate), timeout=timeout,
                            allow_redirects=True, impersonate="chrome124",
                        ),
                        candidate,
                    )
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    attempts.append(_attempt("curl", candidate, exc))
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            attempts.append(_attempt("curl_setup", url, exc))
    if attempts:
        log(f"Hepsiburada teşhis: deneme={len(attempts)} | akis={' > '.join(attempts[-6:])} | url={url}")
    if last_error:
        raise last_error
    raise HttpStatusHermesError(0, url)


class HepsiburadaProvider(Provider):
    site = SITE_HEPSIBURADA
    alert_shows_seller = True

    def is_search_url(self, url: str) -> bool:
        return is_hepsiburada_search_url(url)

    def display_title(self, title: str) -> str:
        return clean_display_title(title)

    def _page(self, ctx, url: str) -> str:
        return read_site_html(fetch_hepsiburada_page(ctx.session, url, ctx.timeout), "Hepsiburada", is_challenge_page)

    def read(self, watch, ctx, outcome):
        html = self._page(ctx, watch.url)
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

    def _product_offers(self, ctx, watch, html: str) -> List[OfferResult]:
        variant_urls = parser.extract_variant_urls(html, watch.url, self._scan_limit(watch))
        if len(variant_urls) > 1:
            log(f"Hepsiburada varyasyonları bulundu: {watch.name or watch.url} | adet={len(variant_urls)}")

        offers: List[OfferResult] = []
        errors: List[str] = []
        seen_offer_keys: set[tuple[str, str, str, str]] = set()
        for variant_url in variant_urls or [watch.url]:
            try:
                variant_label = parser.extract_embedded_variant_label(html, variant_url)
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
                offers.append(OfferResult(title=offer_title, price=offer.price, seller=offer.seller, url=variant_url))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{variant_url} | {exc}")
                log(f"Hepsiburada varyasyon okunamadı: {variant_url} | {exc}")

        if offers:
            return offers
        if errors:
            raise HermesError(errors[-1])
        raise HermesError("Hepsiburada sayfasından fiyat bulunamadı.")

    def _variant_offer(self, ctx, watch, html: str, variant_url: str):
        """Return the variant's payable offer and, when fetched, its own page."""
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
