"""Amazon: search pages, product variant families and verified Amazon Depo offers."""

import math
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterator, List, Optional

from ...constants import SITE_AMAZON
from ...errors import EmptySearchResultsHermesError, HermesError, OutOfStockHermesError, PriceUnavailableHermesError
from ...logging_utils import log
from ...models import OfferResult, SearchResultItem, WatchRule
from ...utils import extract_asin_from_url, is_amazon_search_url, log_cell, normalize_offer_text
from ..base import Provider, ReadContext, WatchRead, excluded_term_in_title
from ..http import raise_if_age_verification
from . import parser
from .client import AmazonClient, is_protection_error
from .search import dedupe_results, extract_result_candidates, filter_matching_results, title_matches_any_keyword, title_matches_keyword

# Bound repeated reads of an absent/unreadable offer independently of card priority.
# Successful offers and access failures never enter this cache.
NO_OFFER_RECHECK_SECONDS = 5 * 60
NO_OFFER_CACHE_LIMIT = 512
PAGE_CACHE_LIMIT = 128
VARIATION_LIMIT = 60


def is_platform_seller(seller: Optional[str]) -> bool:
    return re.sub(r"[^a-z0-9]", "", normalize_offer_text(str(seller or ""))) == "amazoncomtr"


def _name_specificity(name: str) -> int:
    """Use model-name token count to prefer a more specific configured card."""
    return len(normalize_offer_text(name).split())


def most_specific_matches(results: List[SearchResultItem], product_name: str,
                          configured_names: Optional[List[str]] = None) -> List[SearchResultItem]:
    """Keep search results assigned to the most specific matching watch name.

    This is intentionally model-agnostic: when e.g. "Ultra" and "Ultra Max"
    cards both match a title, the longer configured model name owns that result.
    """
    matches = filter_matching_results(results, product_name) if product_name else results
    if not product_name or not configured_names:
        return matches
    names = {str(name).strip() for name in configured_names if str(name or "").strip()}
    if normalize_offer_text(product_name) not in {normalize_offer_text(name) for name in names}:
        names.add(product_name)
    current_specificity = _name_specificity(product_name)
    filtered: List[SearchResultItem] = []
    for item in matches:
        specificities = [_name_specificity(name) for name in names if title_matches_keyword(item.title, name)]
        if not specificities or current_specificity >= max(specificities):
            filtered.append(item)
    return filtered


def offers_from_search_results(results: List[SearchResultItem], product_name: str,
                               configured_names: Optional[List[str]] = None) -> List[OfferResult]:
    matches = most_specific_matches(results, product_name, configured_names)
    if not matches:
        raise EmptySearchResultsHermesError("Amazon arama sayfasında ürün adına uyan ürün bulunamadı.")
    # A search result does not prove the offer is sold by Amazon. Leave unknown
    # sellers unknown so the own-seller filter never trusts a marketplace card.
    return [
        OfferResult(title=item.title, price=item.price, seller=item.seller, url=item.url,
                    is_warehouse=item.is_warehouse, stock_quantity=item.stock_quantity)
        for item in matches
    ]


class AmazonProvider(Provider):
    site = SITE_AMAZON
    alert_shows_seller = False
    backs_off_on_protection = True
    spaces_own_requests = True

    def __init__(self, client: Optional[AmazonClient] = None) -> None:
        self.client = client or AmazonClient()
        self.begin_cycle()

    def begin_cycle(self) -> None:
        # Cycle-local caches: responses, parsed product pages and search detail
        # pages. Prices are never carried into the next cycle.
        self.responses: Dict = {}
        self.pages: Dict[str, dict] = {}
        self.details: Dict[str, List[SearchResultItem]] = {}

    def close(self) -> None:
        self.client.close()

    def is_search_url(self, url: str) -> bool:
        return is_amazon_search_url(url)

    def is_protection_error(self, exc: BaseException) -> bool:
        return is_protection_error(exc)

    def keeps_offer(self, watch: WatchRule, offer: OfferResult) -> bool:
        """With the own-seller option, keep Amazon.com.tr new offers and every verified Depo offer."""
        if not watch.official_seller_only or offer.is_warehouse or is_platform_seller(offer.seller):
            return True
        log("Platformun kendi satıcısı filtresi nedeniyle sonuç atlandı: "
            f"{log_cell(offer.seller or 'satıcı bilgisi yok', 70)} | {log_cell(offer.title, 70)}")
        return False

    def read(self, watch: WatchRule, ctx: ReadContext, outcome: WatchRead):
        if self.is_search_url(watch.url):
            return self.read_search(watch, ctx, outcome)
        return self.iter_product(watch, ctx, outcome)

    # -- pages ---------------------------------------------------------------

    def fetch(self, url: str, ctx: ReadContext, expect_search: bool = False) -> str:
        html = self.client.fetch(url, ctx.timeout, expect_search=expect_search, cache=self.responses, on_request=ctx.measure)
        raise_if_age_verification(html)
        return html

    @staticmethod
    def _remember_block(outcome: WatchRead, exc: BaseException) -> bool:
        if not is_protection_error(exc):
            return False
        outcome.blocked = exc
        return True

    def page_offers(self, url: str, html: str, ctx: ReadContext, outcome: WatchRead, soup=None) -> List[OfferResult]:
        """Keep the selected new offer separate from a verified Amazon Depo offer.

        Read an explicit product-page used accordion first. Fetch the separate
        listing only when needed; both paths verify condition and seller together.
        """
        soup = soup or parser.parse_product_page(html)
        # Capture the link before extract_offers removes the used accordion from the shared tree.
        used_listing_url = parser.extract_used_offer_listing_url(html, source_url=url, soup=soup)
        primary_error = None
        try:
            offers = parser.extract_offers(html, source_url=url, soup=soup)
        except HermesError as exc:
            primary_error = exc
            offers = []
        if any(offer.is_warehouse for offer in offers):
            return offers
        if not used_listing_url:
            if primary_error:
                raise primary_error
            return offers
        try:
            listing_html = self.fetch(used_listing_url, ctx)
            warehouse_offers = parser.extract_verified_warehouse_offers_from_listing(listing_html, source_url=url)
        except Exception as exc:  # noqa: BLE001
            log(f"Amazon Depo teklif listesi okunamadı: {log_cell(url, 70)} | {exc}")
            self._remember_block(outcome, exc)
            if not offers:
                raise
            return offers
        if warehouse_offers:
            log(f"Amazon Depo teklifi doğrulandı: adet={len(warehouse_offers)} | url={log_cell(url, 70)}")
        if not offers and not warehouse_offers and primary_error:
            raise primary_error
        return offers + warehouse_offers

    # -- search pages --------------------------------------------------------

    def _detail_offers(self, candidate, ctx: ReadContext, outcome: WatchRead) -> List[SearchResultItem]:
        """Read the normal and any verified Amazon Depo offer from one product page."""
        cache_key = str(candidate.url or "").strip()
        if cache_key in self.details:
            return self.details[cache_key]
        ctx.pace(f"Amazon detay | {candidate.title}"[:120])
        html = self.fetch(candidate.url, ctx)
        results = [
            # The search-card title is the match context; Amazon's lightweight
            # detail HTML may omit a product title altogether.
            SearchResultItem(title=candidate.title or offer.title, url=offer.url or candidate.url, price=offer.price,
                             is_warehouse=bool(offer.is_warehouse), stock_quantity=offer.stock_quantity, seller=offer.seller)
            for offer in self.page_offers(candidate.url, html, ctx, outcome)
        ]
        if cache_key:
            self.details[cache_key] = results
        return results

    def read_search(self, watch: WatchRule, ctx: ReadContext, outcome: WatchRead) -> List[OfferResult]:
        html = self.fetch(watch.url, ctx, expect_search=True)
        warehouse_search = parser.is_warehouse_search_url(watch.url)
        candidates = extract_result_candidates(html, watch.max_items_to_scan, primary_is_warehouse=warehouse_search)
        target_keywords = [watch.name] if watch.name else []
        results: List[SearchResultItem] = []
        skipped_details = excluded = warehouse_scans = warehouse_hits = 0
        for candidate in candidates:
            # Amazon may append ordinary fallback cards to a Depot-only search.
            # They cannot become used stock through a later product-page lookup.
            if warehouse_search and not candidate.is_warehouse:
                continue
            if excluded_term_in_title(watch, candidate.title):
                excluded += 1
                continue
            matches_watch = not target_keywords or title_matches_any_keyword(candidate.title, target_keywords)
            if candidate.price is not None:
                # A normal search card can mention a second-hand price without
                # the seller. Never trust that card-level signal as DEPO; the
                # product page below verifies it instead.
                if (warehouse_search or not candidate.is_warehouse) and (not watch.official_seller_only or candidate.is_warehouse):
                    results.append(SearchResultItem(title=candidate.title, url=candidate.url, price=candidate.price,
                                                    is_warehouse=candidate.is_warehouse))
                # Standard cards often omit used prices: inspect each matching
                # product page so a verified Amazon Depo offer accompanies it.
                if not warehouse_search and matches_watch:
                    warehouse_scans += 1
                    try:
                        detailed = self._detail_offers(candidate, ctx, outcome)
                        normal = next((item for item in detailed if not item.is_warehouse), None)
                        if normal is not None and (
                            is_platform_seller(normal.seller) if watch.official_seller_only else normal.stock_quantity is not None
                        ):
                            # The card stays the authoritative normal price; the
                            # page only adds its seller and explicit low-stock count.
                            results.append(SearchResultItem(title=candidate.title, url=candidate.url, price=candidate.price,
                                                            is_warehouse=False, stock_quantity=normal.stock_quantity,
                                                            seller=normal.seller))
                        used = [item for item in detailed if item.is_warehouse]
                        warehouse_hits += len(used)
                        results.extend(used)
                        if outcome.blocked:
                            break
                    except Exception as exc:  # noqa: BLE001
                        log(f"Amazon Depo detay fiyatı okunamadı: {log_cell(candidate.title, 60)} | {exc}")
                        if self._remember_block(outcome, exc):
                            break
                continue
            if not matches_watch:
                skipped_details += 1
                continue
            try:
                results.extend(self._detail_offers(candidate, ctx, outcome))
                if outcome.blocked:
                    break
            except Exception as exc:  # noqa: BLE001
                log(f"Amazon arama detay fiyatı okunamadı: {log_cell(candidate.title, 60)} | {exc}")
                if self._remember_block(outcome, exc):
                    break

        if skipped_details:
            log(f"Amazon arama detay fiyatı atlandı: eşleşmeyen_ürün={skipped_details}")
        if excluded:
            log(f"Amazon arama sonucu istekten önce hariç tutuldu: adet={excluded}")
        if warehouse_scans:
            log(f"Amazon Depo derin taraması tamamlandı: ürün={warehouse_scans} | bulunan_depo={warehouse_hits}")
        if not results:
            if outcome.blocked:
                raise outcome.blocked
            raise EmptySearchResultsHermesError("Amazon arama sayfasında fiyatı okunabilir ürün bulunamadı.")
        offers = offers_from_search_results(dedupe_results(results), watch.name, ctx.watch_names.get(SITE_AMAZON, []))
        if warehouse_search:
            offers = [offer for offer in offers if offer.is_warehouse]
            if not offers:
                raise EmptySearchResultsHermesError("Amazon Depo aramasında ikinci el ürün bulunamadı.")
        normal_count = sum(1 for offer in offers if not offer.is_warehouse)
        log(f"Amazon arama linki okundu: {watch.name or watch.url} | eşleşen_ürün={len(offers)} | "
            f"yeni={normal_count} | depo={len(offers) - normal_count}")
        return offers

    # -- product pages and variant families ----------------------------------

    def _remember_page(self, url: str, snapshot: dict) -> None:
        # A cycle can contain several large families; keep enough parsed pages
        # to dedupe repeated cards without growing without bound.
        if url not in self.pages and len(self.pages) >= PAGE_CACHE_LIMIT:
            self.pages.pop(next(iter(self.pages)))
        self.pages[url] = snapshot

    def _cached_absence(self, cache_key: str) -> Optional[dict]:
        absence = self.client.unavailable_product_pages.get(cache_key)
        if not absence:
            return None
        now = time.monotonic()
        if now >= absence["retry_at"]:
            self.client.unavailable_product_pages.pop(cache_key, None)
            return None
        if now - absence.get("last_skip_logged_at", 0) >= 60:
            log(f"Amazon aynı varyant için gereksiz istek atlandı: {cache_key} | "
                f"yeniden kontrol={math.ceil(absence['retry_at'] - now)} sn")
            absence["last_skip_logged_at"] = now
        return absence["snapshot"]

    def _remember_absence(self, cache_key: str, snapshot: dict) -> None:
        cache = self.client.unavailable_product_pages
        if cache_key not in cache and len(cache) >= NO_OFFER_CACHE_LIMIT:
            cache.pop(next(iter(cache)))
        now = time.monotonic()
        cache[cache_key] = {"snapshot": snapshot, "retry_at": now + NO_OFFER_RECHECK_SECONDS, "last_skip_logged_at": now}
        log(f"Amazon fiyat/teklif bulunmayan varyant: {cache_key} | yeniden kontrol={NO_OFFER_RECHECK_SECONDS} sn")

    def iter_product(self, watch: WatchRule, ctx: ReadContext, outcome: WatchRead) -> Iterator[OfferResult]:
        """Yield verified depot offers before continuing to the next variant.

        Follow actual Twister ASIN edges on every fetched page. A single dimension
        change can reveal additional combinations absent from the original page.
        """
        pending = [parser.AmazonProductVariation(label="", url=watch.url)]
        queued = {extract_asin_from_url(watch.url) or watch.url}
        limit = VARIATION_LIMIT if watch.include_variations else 1
        absence_cache = self.client.unavailable_product_pages
        errors: List[str] = []
        found = discovery_pages = excluded_reads = 0

        def enqueue(items) -> None:
            for item in items or []:
                item_identity = extract_asin_from_url(item.url) or item.url
                if item_identity not in queued and len(pending) < limit:
                    queued.add(item_identity)
                    pending.append(item)

        for variation in pending:
            identity = extract_asin_from_url(variation.url) or variation.url
            cache_key = str(variation.url or "").strip()
            snapshot = self.pages.get(cache_key)
            if snapshot is None:
                snapshot = self._cached_absence(cache_key)
                if snapshot is not None:
                    self._remember_page(cache_key, snapshot)
            page_was_reused = snapshot is not None
            try:
                needs_variation_upgrade = snapshot is not None and watch.include_variations and snapshot.get("variations") is None
                needs_offer_upgrade = (
                    snapshot is not None
                    and snapshot.get("offers_skipped_by_exclusion")
                    and tuple(snapshot.get("offer_skip_excluded_terms") or ()) != tuple(watch.excluded_terms)
                )
                if snapshot is None or needs_variation_upgrade or needs_offer_upgrade:
                    page_was_reused = False
                    if variation.url != watch.url:
                        ctx.pace(f"Amazon varyasyon | {variation.label or variation.url}"[:120])
                    html = self.fetch(variation.url, ctx)
                    page_soup = parser.parse_product_page(html)
                    discovered = None
                    if watch.include_variations:
                        discovery_pages += 1
                        discovered = parser.extract_product_variations(html, variation.url, limit, soup=page_soup)
                    enqueue(discovered)
                    selected_label = parser.selected_variation_label(html, soup=page_soup)
                    page_title = parser.extract_title(page_soup) or ""
                    # Exclusions are decided from the title and selected variant
                    # before any price is read; variant discovery still continues.
                    exclusion_term = excluded_term_in_title(watch, " ".join((page_title, selected_label, variation.label)))
                    offer_error = None
                    if exclusion_term:
                        excluded_reads += 1
                        page_offers: List[OfferResult] = []
                        log("Amazon varyant fiyat okuması hariç tutuldu: "
                            f"{log_cell(page_title or selected_label or variation.label, 90)} | hariç tut filtresi: {exclusion_term}")
                    else:
                        try:
                            page_offers = self.page_offers(variation.url, html, ctx, outcome, soup=page_soup)
                        except HermesError as exc:
                            offer_error = exc
                            page_offers = []
                    page_state = {
                        "offers": page_offers,
                        "offer_error": offer_error,
                        "offers_skipped_by_exclusion": bool(exclusion_term),
                        "offer_skip_excluded_terms": tuple(watch.excluded_terms) if exclusion_term else (),
                    }
                    if snapshot is None:
                        snapshot = {"label": selected_label or variation.label, "variations": discovered, **page_state}
                        self._remember_page(cache_key, snapshot)
                    else:
                        snapshot["variations"] = discovered
                        if snapshot.get("offers") is None or needs_offer_upgrade:
                            snapshot.update(page_state)
                    if isinstance(offer_error, (OutOfStockHermesError, PriceUnavailableHermesError)):
                        self._remember_absence(cache_key, snapshot)
                    elif not exclusion_term:
                        absence_cache.pop(cache_key, None)

                page_offers = snapshot["offers"]
                if watch.include_variations:
                    for item in snapshot.get("variations") or []:
                        if (extract_asin_from_url(item.url) or item.url) == identity and item.label:
                            variation = parser.AmazonProductVariation(label=item.label, url=item.url)
                    enqueue(snapshot.get("variations"))
                label = snapshot.get("label") or variation.label
                if snapshot.get("offer_error"):
                    raise snapshot["offer_error"]
            except OutOfStockHermesError as exc:
                title = parser.title_with_variation(
                    exc.product_title or watch.name or variation.url,
                    snapshot.get("label", variation.label) if snapshot else variation.label,
                )
                if excluded_term_in_title(watch, title):
                    continue
                outcome.unavailable.append({"product_title": title, "product_url": variation.url, "reason": str(exc)})
                if not page_was_reused:
                    log(f"Amazon varyantı stokta yok: {title} | {variation.url}")
                continue
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{variation.label or variation.url} | {exc}")
                if not page_was_reused:
                    log(f"Amazon varyasyonu okunamadı: {errors[-1]}")
                if self._remember_block(outcome, exc):
                    # Keep offers already yielded, then pause this watch.
                    break
                continue
            # Yield outside the fetch handler: notification failures belong to
            # the caller, not to the provider's parsing/error handling.
            for offer in sorted(page_offers, key=lambda item: (not item.is_warehouse, item.price)):
                found += 1
                yield OfferResult(
                    title=parser.title_with_variation(offer.title, label),
                    price=offer.price,
                    seller=offer.seller,
                    url=variation.url,
                    is_warehouse=offer.is_warehouse,
                    stock_quantity=offer.stock_quantity,
                )
            if outcome.blocked:
                break
        log(f"Amazon varyasyon taraması: {watch.name or watch.url} | varyant={len(pending)} | teklif={found} | "
            f"hatalı={len(errors)} | varyant_keşif_sayfası={discovery_pages} | "
            f"hariç_nedeniyle_fiyat_okuması_atlandı={excluded_reads}")
        if found:
            return
        if outcome.blocked:
            raise outcome.blocked
        absences = [absence_cache.get(str(item.url or "").strip()) for item in pending]
        if absences and all(entry is not None for entry in absences):
            remaining = min(entry["retry_at"] for entry in absences) - time.monotonic()
            if remaining > 0:
                outcome.retry_after = (datetime.now(timezone.utc) + timedelta(seconds=remaining)).isoformat()
        if errors:
            raise HermesError(errors[-1])
        if outcome.unavailable:
            first = outcome.unavailable[0]
            raise OutOfStockHermesError(first["reason"], first["product_title"], first["product_url"])
        raise EmptySearchResultsHermesError("Amazon bağlantısında seçilen filtrelere uygun teklif yok.")
