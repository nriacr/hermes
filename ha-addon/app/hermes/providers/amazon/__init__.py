"""Amazon: search pages, product variant families and verified Amazon Depo offers."""

import math
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Deque, Dict, Iterator, List, Optional, Tuple

from ...constants import (
    AMAZON_ACCESS_PATH,
    AMAZON_COOKIES_PATH,
    AMAZON_MAIN_GAPS_KEPT,
    SITE_AMAZON,
)
from ...errors import EmptySearchResultsHermesError, HermesError, OutOfStockHermesError, PriceUnavailableHermesError
from ...logging_utils import log, ProblemLog
from ...models import OfferResult, SearchResultItem, WatchRule
from ...utils import extract_asin_from_url, is_amazon_search_url, log_cell
from ..base import DEPO_LANE, Provider, ReadContext, WatchRead, excluded_term_in_title
from ..http import raise_if_age_verification
from . import parser
from .access import MAIN_LANE, AmazonAccess
from .client import AmazonClient, is_protection_error
from .matching import is_platform_seller, offers_from_search_results
from .pagecache import PageCacheMixin
from .rhythm import RhythmMixin, WatchRhythm
from .search import dedupe_results, extract_result_candidates, title_matches_any_keyword

VARIATION_LIMIT = 60


class AmazonProvider(RhythmMixin, PageCacheMixin, Provider):
    site = SITE_AMAZON
    progress_timeout_seconds = 3 * 3600
    alert_shows_seller = False
    backs_off_on_protection = True
    spaces_own_requests = True
    has_depo_lane = True

    def __init__(self, client: Optional[AmazonClient] = None) -> None:
        self.client = client or AmazonClient(access=AmazonAccess(AMAZON_ACCESS_PATH), cookies_path=AMAZON_COOKIES_PATH)
        self.problem_log = ProblemLog()
        self.rhythms: Dict[Tuple, WatchRhythm] = {}
        self.read_seconds: Dict[str, Deque[float]] = {"ana": deque(maxlen=20), "tarama": deque(maxlen=20)}
        # Watches being read right now (by either lane thread) and the gaps between main-page reads.
        self._lane_local = threading.local()
        self.busy: Dict[Tuple, float] = {}
        self._busy_lock = threading.Lock()
        self.main_gaps: Deque[Tuple[float, float]] = deque(maxlen=AMAZON_MAIN_GAPS_KEPT)
        self.cycle_no = 0
        self.begin_cycle()

    def begin_cycle(self) -> None:
        # Cycle-local caches: responses, parsed product pages and search detail
        # pages. Prices are never carried into the next cycle. A cycle is one search round:
        # a red watch is read once in it, by whichever lane gets to it first.
        self.cycle_no += 1
        self._cycle_caches = {"responses": {}, "pages": {}, "details": {}}
        self._log_measurements()

    # The Depo lane reads the same main pages again and again inside one (long) cycle; each of its
    # reads uses caches of its own, so it never gets a page the sweep fetched earlier.
    @property
    def responses(self) -> Dict:
        return getattr(self._lane_local, "caches", None) or self._cycle_caches["responses"]

    @property
    def pages(self) -> Dict[str, dict]:
        return (getattr(self._lane_local, "caches", None) or self._cycle_caches)["pages"]

    @property
    def details(self) -> Dict[str, List[SearchResultItem]]:
        return (getattr(self._lane_local, "caches", None) or self._cycle_caches)["details"]

    def _log_measurements(self) -> None:
        access = self.client.access
        if not access.stats_due():
            return

        def mean(name: str) -> str:
            values = self.read_seconds[name]
            return f"{sum(values) / len(values):.0f} sn" if values else "-"

        counters = access.counters
        recent = sorted(gap for at, gap in self.main_gaps if time.monotonic() - at < 30 * 60)
        gaps = (f"{recent[len(recent) // 2]:.0f}/{recent[min(len(recent), math.ceil(len(recent) * 0.9)) - 1]:.0f} sn (n={len(recent)})"
                if recent else "-")
        log(f"Amazon ölçüm: {access.stats_line()} | kart okuma aralığı (tur) medyan/p90={gaps} | "
            f"istek: depo şeridi={counters.get('istek_depo', 0)}, tarama şeridi={counters.get('istek_tarama', 0)} | "
            f"ana sayfa okuması ort={mean('ana')} | "
            f"varyant taraması ort={mean('tarama')} | depo: sayfa kontrolü={counters.get('depo_sayfa', 0)}, "
            f"ikinci el listesi={counters.get('depo_liste', 0)}, doğrulanan={counters.get('depo_dogrulanan', 0)} | "
            f"hariç nedeniyle atlanan istek={counters.get('hariç_atlanan', 0)}")

    def set_request_delay(self, minimum: float, maximum: float) -> None:
        self.client.delay_range = (float(minimum), float(maximum))

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
        key = self._rhythm_key(watch)
        rhythm = self.rhythms.setdefault(key, WatchRhythm())
        now = time.monotonic()
        if rhythm.main_at is not None and now - rhythm.main_at < 15 * 60:
            self.main_gaps.append((now, now - rhythm.main_at))
        rhythm.main_at = now
        rhythm.main_cycle = self.cycle_no
        with self._busy_lock:
            self.busy[key] = now
        if self.is_search_url(watch.url):
            try:
                found = self.read_search(watch, ctx, outcome)
            except BaseException:
                with self._busy_lock:
                    self.busy.pop(key, None)
                raise
            return self._exclusive(key, found)
        depo = ctx.lane == DEPO_LANE
        reads = self._read_product(watch, ctx, outcome, rhythm)
        return self._exclusive(key, self._in_lane(MAIN_LANE, reads) if depo else reads)

    def _in_lane(self, lane: str, reads):
        """Run a generator in a lane: the client counts its requests for that lane, and the Depo lane reads fresh pages."""
        self.client.lane = lane
        if lane == MAIN_LANE:
            self._lane_local.caches = {"responses": {}, "pages": {}, "details": {}}
        try:
            yield from reads
        finally:
            self.client.lane = ""
            self._lane_local.caches = None

    def _read_product(self, watch: WatchRule, ctx: ReadContext, outcome: WatchRead, rhythm: WatchRhythm) -> Iterator[OfferResult]:
        """The configured page with its used listing and, with variants, the whole family."""
        started = time.monotonic()
        yield from self.iter_product(watch, ctx, outcome)
        if not outcome.blocked:
            rhythm.sweep_at = started
            self.read_seconds["tarama" if watch.include_variations else "ana"].append(time.monotonic() - started)

    # -- pages ---------------------------------------------------------------

    def restore_requests(self, requests) -> None:
        self.client.access.restore(requests)

    def fetch(self, url: str, ctx: ReadContext, expect_search: bool = False) -> str:
        try:
            html = self.client.fetch(url, ctx.timeout, expect_search=expect_search, cache=self.responses, on_request=ctx.measure)
        finally:
            # Every ten minutes, also in the middle of a long variant sweep (not only at a cycle start).
            self._log_measurements()
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
        parser.raise_if_high_price(soup, url)
        counters = self.client.access
        counters.count("depo_sayfa")
        # Capture the link before extract_offers removes the used accordion from the shared tree.
        used_listing_url = parser.extract_used_offer_listing_url(html, source_url=url, soup=soup)
        primary_error = None
        try:
            offers = parser.extract_offers(html, source_url=url, soup=soup)
        except HermesError as exc:
            primary_error = exc
            offers = []
        if any(offer.is_warehouse for offer in offers):
            counters.count("depo_dogrulanan", sum(1 for offer in offers if offer.is_warehouse))
            return offers
        if not used_listing_url:
            if primary_error:
                raise primary_error
            return offers
        try:
            counters.count("depo_liste")
            listing_html = self.fetch(used_listing_url, ctx)
            warehouse_offers = parser.extract_verified_warehouse_offers_from_listing(listing_html, source_url=url)
        except Exception as exc:  # noqa: BLE001
            log(f"Amazon Depo teklif listesi okunamadı: {log_cell(url, 70)} | {exc}")
            self._remember_block(outcome, exc)
            if not offers:
                raise
            return offers
        if warehouse_offers:
            counters.count("depo_dogrulanan", len(warehouse_offers))
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

    def iter_product(self, watch: WatchRule, ctx: ReadContext, outcome: WatchRead) -> Iterator[OfferResult]:
        """Yield verified depot offers before continuing to the next variant.

        Follow actual Twister ASIN edges on every fetched page. A single dimension
        change can reveal additional combinations absent from the original page.
        """
        pending = [parser.AmazonProductVariation(label="", url=watch.url)]
        queued = {extract_asin_from_url(watch.url) or watch.url}
        follow_variations = watch.include_variations
        limit = VARIATION_LIMIT if follow_variations else 1
        absence_cache = self.client.unavailable_product_pages
        errors: List[str] = []
        found = discovery_pages = excluded_reads = skipped_excluded = 0

        def enqueue(items) -> None:
            for item in items or []:
                item_identity = extract_asin_from_url(item.url) or item.url
                if item_identity not in queued and len(pending) < limit:
                    queued.add(item_identity)
                    pending.append(item)

        for variation in pending:
            ctx.pace("Amazon varyant kontrolü")
            identity = extract_asin_from_url(variation.url) or variation.url
            cache_key = str(variation.url or "").strip()
            snapshot = self.pages.get(cache_key)
            if snapshot is None:
                snapshot = self._cached_absence(cache_key)
                if snapshot is not None:
                    self._remember_page(cache_key, snapshot)
            page_was_reused = snapshot is not None
            page_title = str(snapshot.get("product_title") or "") if snapshot else ""
            selected_label = str(snapshot.get("label") or variation.label) if snapshot else variation.label
            remembered = self._remembered_exclusion(cache_key, watch) if snapshot is None and follow_variations else None
            if remembered is not None:
                # Excluded by title and read recently: its neighbours are known, the page is not needed.
                skipped_excluded += 1
                self.client.access.count("hariç_atlanan")
                enqueue(remembered["variations"])
                continue
            try:
                needs_variation_upgrade = snapshot is not None and follow_variations and snapshot.get("variations") is None
                needs_offer_upgrade = (
                    snapshot is not None
                    and snapshot.get("offers_skipped_by_exclusion")
                    and tuple(snapshot.get("offer_skip_excluded_terms") or ()) != tuple(watch.excluded_terms)
                )
                if snapshot is None or needs_variation_upgrade or needs_offer_upgrade:
                    page_was_reused = False
                    html = self.fetch(variation.url, ctx)
                    page_soup = parser.parse_product_page(html)
                    discovered = None
                    if follow_variations:
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
                        if discovered is not None:
                            self._remember_exclusion(cache_key, watch, discovered)
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
                        "product_title": page_title,
                        "offers": page_offers,
                        "offer_error": offer_error,
                        "offers_skipped_by_exclusion": bool(exclusion_term),
                        "offer_skip_excluded_terms": tuple(watch.excluded_terms) if exclusion_term else (),
                    }
                    if snapshot is None:
                        snapshot = {"label": selected_label or variation.label, "variations": discovered, **page_state}
                        self._remember_page(cache_key, snapshot)
                    else:
                        snapshot["product_title"] = page_title
                        snapshot["variations"] = discovered
                        if snapshot.get("offers") is None or needs_offer_upgrade:
                            snapshot.update(page_state)
                    if isinstance(offer_error, (OutOfStockHermesError, PriceUnavailableHermesError)):
                        # A red watch's own page is looked at again in every search round (3.8.2), so its
                        # return to stock shows up at once; the 5-minute bound stays for everything else.
                        if not (variation.url == watch.url and self.is_red(watch)):
                            self._remember_absence(cache_key, snapshot)
                    elif not exclusion_term:
                        absence_cache.pop(cache_key, None)
                        self.client.excluded_pages.pop(cache_key, None)

                page_offers = snapshot["offers"]
                if follow_variations:
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
                self.problem_log.recovered(variation.url)
                outcome.unavailable.append({"product_title": title, "product_url": variation.url, "reason": str(exc)})
                if not page_was_reused:
                    log(f"Amazon varyantı stokta yok: {title} | {variation.url}")
                continue
            except Exception as exc:  # noqa: BLE001
                variant = selected_label or variation.label
                title = parser.title_with_variation(page_title, variant) if page_title else ""
                outcome.error_details.append({"product_title": title, "product_url": variation.url,
                                              "variant": variant, "reason": str(exc)})
                errors.append(f"{title or variant or 'Ürün adı bu okumada alınamadı'} | {variation.url} | {exc}")
                if not page_was_reused:
                    self.problem_log.failure(variation.url, f"Amazon varyasyonu okunamadı: {errors[-1]}")
                if self._remember_block(outcome, exc):
                    # Keep offers already yielded, then pause this watch.
                    break
                continue
            self.problem_log.recovered(variation.url)
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
            f"hariç_nedeniyle_fiyat_okuması_atlandı={excluded_reads} | hariç_nedeniyle_istek_atlandı={skipped_excluded}")
        outcome.errors.extend(errors)
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
