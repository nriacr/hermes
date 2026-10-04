"""One monitoring cycle: read due watches, record prices, notify, publish the table."""

import math
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import chain
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

from ..constants import (
    APP_VERSION,
    CYCLE_HISTORY_PATH,
    DATABASE_PATH,
    PRIORITIES,
    SITE_MIN_REQUEST_GAP_SECONDS,
    STATE_PATH,
    SUMMARY_PATH,
)
from ..errors import BotProtectionHermesError, EmptySearchResultsHermesError, HermesError, OutOfStockHermesError, error_status
from ..history import History
from ..homeassistant import HomeAssistantBridge
from ..logging_utils import log
from ..models import HermesConfig, OfferResult, PriceSummaryRow, StockSummaryRow, WatchRule
from ..notifier import Pushover
from ..providers.base import Provider, ReadContext, RequestSpacing, WatchRead, excluded_term_in_title
from ..providers.registry import ProviderSet
from ..storage import load_json, save_json
from ..utils import canonical_tracking_url, format_tl, local_now, parse_iso_datetime, site_label, utc_now
from . import alerts, scheduling, state as state_ops, summary
from .state import offer_key as make_offer_key, watch_key as make_watch_key

# An explicit "no results" notice is a normal answer; read it again at most every five minutes.
NO_RESULTS_RECHECK_SECONDS = 5 * 60


@dataclass
class DataFiles:
    state: Path = STATE_PATH
    summary: Path = SUMMARY_PATH
    # Read only once, to move the 3.0 cycle history into the database.
    cycle_history: Path = CYCLE_HISTORY_PATH
    database: Path = DATABASE_PATH


def log_cycle_banner(config: HermesConfig) -> None:
    line = "=" * 92
    log(line)
    log(f">>> HERMES v{APP_VERSION} | YENİ KONTROL TURU | Kontrol aralığı: {config.interval_seconds} saniye <<<")
    log(line)


def skipped_offer_reason(watch: WatchRule, offer: OfferResult, display_name: str) -> str:
    if watch.minimum_price is not None and offer.price < watch.minimum_price:
        return (f"minimum fiyat filtresi: {format_tl(offer.price, with_currency=True)} < "
                f"{format_tl(watch.minimum_price, with_currency=True)}")
    excluded_term = excluded_term_in_title(watch, display_name)
    return f"hariç tut filtresi: {excluded_term}" if excluded_term else ""


def read_outcome(provider: Provider, exc: BaseException) -> str:
    """Measurement label of a failed read: captcha, http_<status>, timeout, connection, unreadable or error."""
    status = error_status(exc)
    if status:
        return f"http_{status}"
    if isinstance(exc, BotProtectionHermesError) or provider.is_protection_error(exc):
        return "captcha"
    name = type(exc).__name__.lower()
    if isinstance(exc, requests.Timeout) or "timeout" in name:
        return "timeout"
    if isinstance(exc, requests.ConnectionError) or "connection" in name:
        return "connection"
    if isinstance(exc, HermesError):
        # Hermes reached the page but could not find a product or price on it.
        return "unreadable"
    return "error"


def is_normal_empty_result(exc: BaseException) -> bool:
    """A valid search or product page without a matching offer is not an error."""
    return isinstance(exc, EmptySearchResultsHermesError)


@dataclass
class RecordedOffers:
    """The watch entry (after a stock-return notice) and the offers read for it."""

    entry: Dict[str, Any]
    search_group: str = ""
    search_group_label: str = ""
    offer_keys: List[str] = field(default_factory=list)


@dataclass
class CycleRun:
    """Everything one cycle collects while it reads watches."""

    state: Dict[str, Any]
    summary_rows: List[PriceSummaryRow] = field(default_factory=list)
    stock_rows: List[StockSummaryRow] = field(default_factory=list)
    search_failures: List[Dict[str, Any]] = field(default_factory=list)
    priority_scope: Dict[str, Dict[str, int]] = field(
        default_factory=lambda: {priority: {"due": 0, "deferred": 0, "started": 0} for priority in PRIORITIES}
    )


class Monitor:
    """Reads every due watch with its site provider and keeps state consistent."""

    def __init__(self, config: HermesConfig, providers: Optional[ProviderSet] = None, notifier: Optional[Pushover] = None,
                 files: Optional[DataFiles] = None, sleep: Callable[[float], None] = time.sleep,
                 should_stop: Callable[[], bool] = lambda: False, home_assistant: Optional[HomeAssistantBridge] = None) -> None:
        self.config = config
        self.home_assistant = home_assistant
        self.should_stop = should_stop
        self.providers = providers or ProviderSet()
        self.notifier = notifier or Pushover(config.pushover_user_key, config.pushover_api_token, config.request_timeout_seconds)
        self.files = files or DataFiles()
        self.sleep = sleep
        # Site queues run in parallel; every change to the cycle's shared
        # state, summary and files happens under this lock.
        self._lock = threading.RLock()
        # Minimum gaps between request starts per site; they span cycles.
        self._spacing: Dict[str, RequestSpacing] = {}
        # False after a cycle that read no watch (nothing due or all paused); such a cycle logs nothing.
        self.last_cycle_read = False
        self._last_durations = (0.0, 0.0)
        self.history = History.at(self.files.database)
        self.history.migrate_json(self.files.state, self.files.cycle_history)
        self.history.drop_idle_cycles(self.config.interval_seconds)

    def close(self) -> None:
        self.providers.close()
        self.history.close()

    # -- helpers ---------------------------------------------------------------

    def pace(self, label: str) -> None:
        """Random delay between requests, so sites see a human-like rhythm."""
        delay = random.randint(self.config.request_delay_min_seconds, self.config.request_delay_max_seconds)
        log(f"{label} isteği öncesi {delay} saniye bekleniyor.")
        if delay > 0:
            self.sleep(delay)

    def load_state(self) -> Dict[str, Any]:
        loaded = load_json(self.files.state, {})
        return loaded if isinstance(loaded, dict) else {}

    def save_state(self, state: Dict[str, Any]) -> None:
        save_json(self.files.state, state)

    def _watch_names(self) -> Dict[str, List[str]]:
        names: Dict[str, List[str]] = {}
        for watch in self.config.watches:
            if watch.name:
                names.setdefault(watch.site, []).append(watch.name)
        return names

    def _notify(self, title: str, message: str, url: str) -> bool:
        """Send one notification; a delivery failure never fails the price read."""
        try:
            self.notifier.send(title, message, url)
            return True
        except Exception as exc:  # noqa: BLE001
            log(f"Bildirim gönderilemedi, sonraki çevrimde yeniden denenecek: {title} | {exc}")
            return False

    # -- the cycle -------------------------------------------------------------

    def run_cycle(self) -> None:
        started_at = time.monotonic()
        run = CycleRun(state=self.load_state())
        state_ops.drop_watch_guards(run.state)
        self.providers.begin_cycle()
        queues: Dict[str, List[WatchRule]] = {}
        for watch in self.config.watches:
            if self._plan(run, watch):
                queues.setdefault(watch.site, []).append(watch)
        self.last_cycle_read = False
        if self._run_site_queues(run, queues):
            # Shutting down: keep what was read, publish nothing partial.
            log("Hermes kapanıyor; çevrim yarıda bırakıldı, okunan sonuçlar kaydedildi.")
            self.save_state(run.state)
            return

        if self.last_cycle_read:
            log("Çevrim öncelik kapsamı: " + " | ".join(
                f"{label}={run.priority_scope[key]['started']} başladı, {run.priority_scope[key]['due']} sırası geldi, "
                f"{run.priority_scope[key]['deferred']} ertelendi"
                for key, label in (("high", "yüksek"), ("medium", "orta"), ("low", "düşük"))
            ))
        if self.config.watches:
            # A cycle lasts until the slowest site queue has finished.
            if self.last_cycle_read:
                scan_seconds = time.monotonic() - started_at
                self._last_durations = (scan_seconds + self.config.interval_seconds, scan_seconds)
                self.history.record_cycle(self._last_durations[0])
            # A cycle that read nothing (nothing due, or every due watch paused)
            # is not a cycle in the statistics; the last working one stays shown.
            cycle_seconds, scan_seconds = self._last_durations
            rows = summary.deduplicate_summary_rows(run.summary_rows)
            summary.publish_price_summary(self.files.summary, rows, run.stock_rows,
                                          cycle_seconds if self.last_cycle_read else None,
                                          scan_seconds if self.last_cycle_read else None)
            alerts.maybe_alert_summary_drop(run.state, rows, self.config, self.notifier)
            alerts.maybe_alert_search_failures(run.state, run.search_failures, self.notifier)
            self.on_cycle_published(run, rows, cycle_seconds, scan_seconds)
        self.save_state(run.state)

    def on_cycle_published(self, run: "CycleRun", rows: List[PriceSummaryRow], cycle_seconds: float, scan_seconds: float) -> None:
        """Mirror the finished cycle as Home Assistant sensors."""
        if self.home_assistant is not None and self.home_assistant.enabled:
            self.home_assistant.publish_cycle(rows, len(run.stock_rows), run.state, cycle_seconds, scan_seconds)

    def on_opportunity(self, watch: WatchRule, offer: OfferResult, display_name: str, url: str) -> None:
        """Fire a Home Assistant event for a delivered opportunity notification."""
        if self.home_assistant is not None and self.home_assistant.enabled:
            self.home_assistant.publish_opportunity(watch, offer, display_name, url)

    def _site_pace(self, site: str) -> Callable[[str], None]:
        """The random delay, then the site's minimum gap since its previous request start."""
        if self.providers[site].spaces_own_requests:
            return self.pace
        spacing = self._spacing.setdefault(site, RequestSpacing(SITE_MIN_REQUEST_GAP_SECONDS.get(site, 0), sleep=self.sleep))

        def pace(label: str) -> None:
            self.pace(label)
            waited = spacing.wait()
            if waited >= 0.05:
                log(f"{site_label(site)} istek aralığı için {waited:.1f} sn ek bekleme.")

        return pace

    def _run_site_queues(self, run: "CycleRun", queues: Dict[str, List[WatchRule]]) -> bool:
        """Read every site's due watches in its own queue; True when stopped early.

        Each site keeps one sequential queue with its own request pacing, so a
        slow or protected site never delays the others.
        """
        watch_names = self._watch_names()
        stopped = threading.Event()
        failures: List[BaseException] = []

        def work(site_watches: List[WatchRule]) -> None:
            try:
                with requests.Session() as session:
                    site = site_watches[0].site
                    ctx = ReadContext(timeout=self.config.request_timeout_seconds, session=session,
                                      pace=self._site_pace(site), watch_names=watch_names,
                                      measure=lambda method, kind, result, ms, site=site:
                                      self.history.record_request(site, method, kind, result, ms))
                    for watch in scheduling.priority_order(site_watches, self.providers[site].read_rank):
                        if self.should_stop():
                            stopped.set()
                            return
                        self.check_watch(run, ctx, watch)
            except BaseException as exc:  # noqa: BLE001 - re-raised in the cycle's own thread
                failures.append(exc)

        workers = [threading.Thread(target=work, args=(site_watches,), name=f"hermes-{site}", daemon=True)
                   for site, site_watches in queues.items()]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        if failures:
            raise failures[0]
        return stopped.is_set()

    def _plan(self, run: CycleRun, watch: WatchRule) -> bool:
        """True for a due watch; a deferred one keeps its last result."""
        priority = scheduling.watch_priority(watch)
        provider = self.providers[watch.site]
        key = make_watch_key(watch)
        entry = run.state.get(key, {})
        entry = entry if isinstance(entry, dict) else {}
        seller = site_label(watch.site)
        retry_after = parse_iso_datetime(entry.get("amazon_no_offer_retry_after"))
        absence_deferred = (not scheduling.manually_due(watch, entry) and retry_after is not None
                            and datetime.now(timezone.utc) < retry_after)
        if (absence_deferred or not provider.read_due(watch)
                or not scheduling.watch_check_due(watch, entry, self.config.interval_seconds)):
            if not scheduling.manually_due(watch, entry) or absence_deferred:
                run.priority_scope[priority]["deferred"] += 1
                run.summary_rows.extend(summary.cached_summary_rows(watch, key, run.state, seller))
                run.stock_rows.extend(summary.cached_stock_rows(watch, entry, seller))
                return False
        run.priority_scope[priority]["due"] += 1
        return True

    def check_watch(self, run: CycleRun, ctx: ReadContext, watch: WatchRule) -> None:
        provider = self.providers[watch.site]
        key = make_watch_key(watch)
        seller = site_label(watch.site)
        outcome = WatchRead()
        with self._lock:
            entry = run.state.get(key, {})
            entry = entry if isinstance(entry, dict) else {}
            if provider.backs_off_on_protection and self._guarded(run, watch, key, entry, seller):
                return
            run.priority_scope[scheduling.watch_priority(watch)]["started"] += 1
            if not self.last_cycle_read:
                # Logged with the first watch that is read: cycles in which every
                # due watch is paused or nothing is due stay out of the log.
                self.last_cycle_read = True
                log_cycle_banner(self.config)
        ctx.pace(f"{seller} | {(watch.name or watch.url)[:64]}")
        # Read duration includes the provider's own extra page delays.
        started_at = time.monotonic()
        result = "ok"
        try:
            offers = (offer for offer in provider.read(watch, ctx, outcome) if provider.keeps_offer(watch, offer))
            recorded = self._record_offers(run, provider, watch, entry, seller, offers)
            if outcome.blocked:
                result = read_outcome(provider, outcome.blocked)
            with self._lock:
                self._record_success(run, provider, watch, key, seller, recorded, outcome)
        except OutOfStockHermesError as exc:
            result = "stock"
            with self._lock:
                self._record_out_of_stock(run, provider, watch, key, entry, seller, outcome, exc)
        except Exception as exc:  # noqa: BLE001
            result = "empty" if is_normal_empty_result(exc) else read_outcome(provider, outcome.blocked or exc)
            with self._lock:
                self._record_failure(run, provider, watch, key, entry, seller, outcome, exc)
        finally:
            self.history.record_read(watch.site, result, round((time.monotonic() - started_at) * 1000), key,
                                     scheduling.watch_priority(watch))

    def _guarded(self, run: CycleRun, watch: WatchRule, key: str, entry: Dict[str, Any], seller: str) -> bool:
        """True while the site is paused after a protection page; the watch keeps its last result."""
        guard_key = state_ops.site_guard_key(watch.site)
        remaining = state_ops.guard_remaining_seconds(run.state, guard_key)
        if remaining <= 0:
            return False
        run.summary_rows.extend(summary.cached_summary_rows(watch, key, run.state, seller))
        run.stock_rows.extend(summary.cached_stock_rows(watch, entry, seller))
        guard = state_ops.guard_store(run.state).get(guard_key, {})
        last_logged = parse_iso_datetime(guard.get("last_skip_logged_at"))
        if not last_logged or (local_now().astimezone(timezone.utc) - last_logged).total_seconds() >= 60:
            guard["last_skip_logged_at"] = utc_now()
            log(f"{seller} erişim molasında ({guard.get('kind', 'captcha')}): tüm {seller} kartları bekliyor | "
                f"kalan={max(1, math.ceil(remaining / 60))} dk")
        return True

    def _stock_return(self, provider: Provider, watch: WatchRule, entry: Dict[str, Any], seller: str, offers):
        """Notify once when a product that was out of stock is available again."""
        if not (provider.notifies_stock_return and entry.get("last_out_of_stock_at")):
            return offers, entry, False
        offers = list(offers)
        if not offers:
            return offers, entry, False
        lowest = min(offers, key=lambda offer: offer.price)
        title = lowest.title or watch.name or watch.url
        message = f"Site: {seller}\n{title}\nStok yeniden geldi.\nGüncel fiyat: {format_tl(lowest.price, with_currency=True)}"
        if not self._notify(f"{seller} stok alarmı", message, lowest.url or watch.url):
            return offers, entry, False
        log(f"Stok geri geldi bildirimi gönderildi: {seller} | {title}")
        entry = dict(entry)
        entry.pop("last_out_of_stock_at", None)
        return offers, entry, True

    def _record_offers(self, run: CycleRun, provider: Provider, watch: WatchRule, entry: Dict[str, Any],
                       seller: str, offers) -> "RecordedOffers":
        """Record streamed offers; network reads and notifications stay outside the lock."""
        state = run.state
        offers, entry, stock_return_sent = self._stock_return(provider, watch, entry, seller, offers)
        offer_iterator = iter(offers)
        first_offer = next(offer_iterator, None)
        group_fallback = str(first_offer.title or "").split(" / ", 1)[0].strip() if first_offer else ""
        recorded = RecordedOffers(entry, *summary.result_group_for_watch(watch, group_fallback))
        for offer in chain([first_offer], offer_iterator) if first_offer else ():
            display_name = provider.display_title(offer.title or watch.name or watch.url)
            if offer.stock_quantity is not None:
                display_name = f"{display_name} (Stok {offer.stock_quantity})"
            skip_reason = skipped_offer_reason(watch, offer, display_name)
            if skip_reason:
                log(f"Sonuç dikkate alınmadı: {seller} | {display_name} | {skip_reason}")
                continue
            matched_url = offer.url or watch.url
            context = f"{seller} | {display_name}"
            # An offer the provider replays from its own memory keeps the time it was read;
            # it is neither a new price point nor a new alert.
            replayed = offer.checked_at is not None
            price_checked_at = offer.checked_at or datetime.now(timezone.utc).isoformat()
            with self._lock:
                item_key = self._offer_key(state, watch, entry, matched_url, offer.is_warehouse)
                offer_entry = state.get(item_key, {})
                offer_entry = offer_entry if isinstance(offer_entry, dict) else {}
                recorded.offer_keys.append(item_key)
                min_price, max_price = state_ops.sanitized_price_bounds(offer_entry, offer.price, watch.target_price, context)
                if not replayed and not state_ops.is_absurd_price(offer_entry, offer.price, watch.target_price):
                    self.history.record_price(item_key, watch.site, display_name, offer.price)
                run.summary_rows.append(PriceSummaryRow(
                    seller=seller, product_title=display_name, product_url=matched_url, price=offer.price,
                    target_price=watch.target_price, min_price=min_price, max_price=max_price,
                    search_group=recorded.search_group, search_group_label=recorded.search_group_label,
                    is_warehouse=offer.is_warehouse, tracking_id=watch.tracking_id, priority=watch.priority,
                    price_checked_at=price_checked_at,
                ))
                wants_alert = not replayed and not stock_return_sent and state_ops.should_alert(
                    offer_entry, offer.price, watch.target_price, watch.notify_once_in_24h)
            if replayed:
                with self._lock:
                    state[item_key] = {**offer_entry, "last_price_checked_at": price_checked_at, "last_error": None,
                                       "last_error_status": None, "site": watch.site, "is_warehouse": offer.is_warehouse,
                                       "title": display_name, "url": matched_url, "configured_url": watch.url,
                                       "watch_name": watch.name, "tracking_id": watch.tracking_id, "size": watch.size,
                                       "include_variations": watch.include_variations, "priority": watch.priority,
                                       "search_group": recorded.search_group, "search_group_label": recorded.search_group_label,
                                       "warehouse_evidence": bool(offer.is_warehouse)}
                continue
            log(f"Kontrol edildi: {seller} | {display_name} | fiyat={format_tl(offer.price, with_currency=True)} | "
                f"hedef={format_tl(watch.target_price, with_currency=True)}")

            # A stock-return notification already reached the user for this
            # watch; it counts as the alert instead of a second message.
            alert_sent = False
            if wants_alert:
                seller_note = f" ({offer.seller})" if offer.seller and (provider.alert_shows_seller or offer.is_warehouse) else ""
                message = (f"Site: {seller}\n{display_name}\n"
                           f"Güncel fiyat: {format_tl(offer.price, with_currency=True)}{seller_note}\n"
                           f"Hedef fiyat: {format_tl(watch.target_price, with_currency=True)}")
                title = f"{seller} Depo fırsatı" if offer.is_warehouse else f"{seller} fiyat alarmı"
                alert_sent = self._notify(title, message, matched_url)
            elif offer.price <= watch.target_price and watch.notify_once_in_24h:
                log(f"Bildirim atlandı, 24 saat dolmadı veya fiyat daha düşük değil: {seller} | {matched_url}")

            with self._lock:
                updated = state_ops.updated_offer_entry(state.get(item_key, offer_entry), offer.price, watch.target_price,
                                                        alert_sent or stock_return_sent, context)
                updated.update({
                    "title": display_name, "url": matched_url, "configured_url": watch.url, "watch_name": watch.name,
                    "tracking_id": watch.tracking_id, "size": watch.size, "site": watch.site,
                    "include_variations": watch.include_variations, "priority": watch.priority,
                    "last_price_checked_at": price_checked_at, "search_group": recorded.search_group,
                    "search_group_label": recorded.search_group_label, "is_warehouse": offer.is_warehouse,
                    "warehouse_evidence": bool(offer.is_warehouse), "last_error": None, "last_error_status": None,
                })
                state[item_key] = updated
                if alert_sent:
                    log(f"Bildirim gönderildi: {seller} | {display_name}")
                    # Publish the opportunity now; rows of watches still pending stay visible.
                    summary.save_incremental_summary(self.files.summary, run.summary_rows, run.stock_rows)
                    self.save_state(state)
            if alert_sent:
                self.on_opportunity(watch, offer, display_name, matched_url)
        return recorded

    @staticmethod
    def _offer_key(state: Dict[str, Any], watch: WatchRule, entry: Dict[str, Any], url: str, is_warehouse: bool) -> str:
        """Reuse the key of the same canonical offer so history and suppression survive URL changes."""
        canonical = canonical_tracking_url(url)
        for previous_key in entry.get("offer_keys", []) if isinstance(entry.get("offer_keys"), list) else []:
            previous = state.get(previous_key, {})
            if (isinstance(previous, dict) and bool(previous.get("is_warehouse")) == is_warehouse
                    and canonical_tracking_url(previous.get("url", "")) == canonical):
                return previous_key
        return make_offer_key(watch, url, is_warehouse)

    def _watch_fields(self, watch: WatchRule) -> Dict[str, Any]:
        return {
            "site": watch.site, "watch_name": watch.name, "tracking_id": watch.tracking_id,
            "configured_url": watch.url, "size": watch.size, "check_now_token": watch.check_now_token,
            "last_checked_at": utc_now(),
        }

    def _record_success(self, run: CycleRun, provider: Provider, watch: WatchRule, key: str, seller: str,
                        recorded: "RecordedOffers", outcome: WatchRead) -> None:
        base = dict(recorded.entry)
        base.pop("amazon_variant_catalog", None)
        run.state[key] = {
            **base,
            **self._watch_fields(watch),
            "include_variations": watch.include_variations,
            "search_group": recorded.search_group,
            "search_group_label": recorded.search_group_label,
            "offer_keys": recorded.offer_keys,
            "last_error": None,
            "last_error_status": None,
            "amazon_partial_result": False,
            "amazon_no_offer_retry_after": None,
            "unavailable_variants": list(outcome.unavailable),
        }
        run.stock_rows.extend(summary.cached_stock_rows(watch, run.state[key], seller))
        if not provider.backs_off_on_protection:
            return
        if outcome.blocked:
            # A page that fails on its own rests alone (the provider quarantines it); only two
            # different pages failing one after the other pause the whole site.
            if not provider.absorb_block(watch):
                state_ops.note_guard(run.state, state_ops.site_guard_key(watch.site), watch.name or watch.url,
                                     outcome.blocked, seller)
            run.state[key]["last_error"] = str(outcome.blocked)
            run.state[key]["amazon_partial_result"] = True
        else:
            state_ops.clear_guard(run.state, state_ops.site_guard_key(watch.site), seller)

    def _record_out_of_stock(self, run: CycleRun, provider: Provider, watch: WatchRule, key: str, entry: Dict[str, Any],
                             seller: str, outcome: WatchRead, exc: OutOfStockHermesError) -> None:
        stale_ids = summary.cached_offer_ids(watch, key, run.state, seller)
        stock_title = exc.product_title or watch.name or watch.url
        log(f"Stokta yok: {seller} | {stock_title} | {exc}")
        unavailable = list(outcome.unavailable) or [
            {"product_title": stock_title, "product_url": exc.product_url or watch.url, "reason": str(exc)}
        ]
        failed = state_ops.reset_alert_after_missing(dict(entry), seller, stock_title)
        failed.update({
            **self._watch_fields(watch),
            "offer_keys": [],
            "last_error": None,
            "last_error_status": None,
            "last_out_of_stock_at": utc_now(),
            "unavailable_variants": unavailable,
            "amazon_no_offer_retry_after": outcome.retry_after,
        })
        run.state[key] = failed
        stock_rows = summary.cached_stock_rows(watch, failed, seller)
        run.stock_rows.extend(stock_rows)
        if provider.backs_off_on_protection:
            state_ops.clear_guard(run.state, state_ops.site_guard_key(watch.site), seller)
        # A missing item must not keep its previous price visible until the cycle ends.
        summary.save_incremental_summary(self.files.summary, [], stock_rows, removed_price_ids=stale_ids)

    def _record_failure(self, run: CycleRun, provider: Provider, watch: WatchRule, key: str, entry: Dict[str, Any],
                        seller: str, outcome: WatchRead, exc: BaseException) -> None:
        stale_ids = summary.cached_offer_ids(watch, key, run.state, seller)
        is_search = provider.is_search_url(watch.url)
        normal_empty = is_normal_empty_result(exc)
        if normal_empty:
            log(f"Arama sonucu boş: {seller} | {watch.name or watch.url}")
        else:
            log(f"Hata: {seller} | {watch.url} | {exc}")
        access_error = outcome.blocked or exc
        # A protection page keeps the watch's last rows on the table; they carry their read time.
        kept_after_block = provider.backs_off_on_protection and bool(outcome.blocked or provider.is_protection_error(exc))
        if provider.backs_off_on_protection:
            if outcome.blocked or provider.is_protection_error(exc):
                if not provider.absorb_block(watch):
                    state_ops.note_guard(run.state, state_ops.site_guard_key(watch.site), watch.name or watch.url,
                                         access_error, seller)
            else:
                # A recovery probe is consumed once even when access worked but found
                # nothing; an old guard cannot keep bypassing the priority schedule.
                state_ops.clear_guard(run.state, state_ops.site_guard_key(watch.site), seller)
        silent = alerts.is_silent_access_error(access_error)
        reportable_search_error = is_search and not normal_empty and not silent
        if reportable_search_error:
            run.search_failures.append({"page": watch.name, "failed_links": 1})
        failed = dict(entry)
        if alerts.missing_price_resets_alert(exc):
            failed = state_ops.reset_alert_after_missing(failed, seller, watch.name or watch.url)
        if reportable_search_error and alerts.search_error_notification_due(failed):
            message = (f"{seller} arama: {watch.name}\nAranan keyword: {watch.name}\nHata: {exc}\nLink: {watch.url}\n"
                       "Kontrol etmen gerekebilir: link geçersiz olabilir, site koruması olabilir veya sayfa yapısı değişmiş olabilir.")
            if self._notify(f"{seller} arama hatası", message[:900], watch.url):
                failed["last_error_notified_at"] = utc_now()
                log(f"Arama hata bildirimi gönderildi: {seller} | {watch.name}")
        failed.update({
            **self._watch_fields(watch),
            "offer_keys": list(entry.get("offer_keys") or []) if kept_after_block else [],
            "unavailable_variants": list(entry.get("unavailable_variants") or []) if kept_after_block else [],
            "amazon_no_offer_retry_after": outcome.retry_after,
            "last_error": None if normal_empty else str(exc),
            "last_error_status": None if normal_empty else error_status(access_error),
            "amazon_partial_result": False,
        })
        if is_search and getattr(exc, "no_results_notice", False):
            # Explicit marketplace absence: keep the query visible as a stock row
            # and do not request the same empty search every cycle.
            failed["unavailable_variants"] = [{"product_title": watch.name or watch.url, "product_url": watch.url, "reason": str(exc)}]
            failed["amazon_no_offer_retry_after"] = (
                datetime.now(timezone.utc) + timedelta(seconds=NO_RESULTS_RECHECK_SECONDS)
            ).isoformat()
        run.state[key] = failed
        stock_rows = summary.cached_stock_rows(watch, failed, seller)
        run.stock_rows.extend(stock_rows)
        if kept_after_block:
            run.summary_rows.extend(summary.cached_summary_rows(watch, key, run.state, seller))
            return
        # The dashboard may still show the last successful cycle; remove only this watch's stale rows now.
        summary.save_incremental_summary(self.files.summary, [], stock_rows, removed_price_ids=stale_ids)
