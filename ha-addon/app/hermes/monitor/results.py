"""Validated offer evaluation, notification decisions and incremental publication."""

import math
import time
import hashlib
import json
from typing import TYPE_CHECKING

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from itertools import chain
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field, asdict

from ..errors import HermesError, EmptySearchResultsHermesError, OutOfStockHermesError, error_status
from ..logging_utils import log
from ..models import OfferResult, PriceSummaryRow, WatchRule
from ..providers.base import Provider, WatchRead, excluded_term_in_title
from ..utils import SystemLoad, canonical_tracking_url, format_tl, local_now, parse_iso_datetime, utc_now
from . import alerts, state as state_ops, summary
from .state import offer_key as make_offer_key, watch_key as make_watch_key


if TYPE_CHECKING:
    from .cycle import CycleRun

NO_RESULTS_RECHECK_SECONDS = 5 * 60

def skipped_offer_reason(watch: WatchRule, offer: OfferResult, display_name: str) -> str:
    if watch.minimum_price is not None and offer.price < watch.minimum_price:
        return (f"minimum fiyat filtresi: {format_tl(offer.price, with_currency=True)} < "
                f"{format_tl(watch.minimum_price, with_currency=True)}")
    excluded_term = excluded_term_in_title(watch, display_name)
    return f"hariç tut filtresi: {excluded_term}" if excluded_term else ""


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


class ResultRecorder:
    def __init__(self, monitor):
        self.monitor = monitor

        self.config = monitor.config
        self.files = monitor.files
        self.history = monitor.history
        self.notifier = monitor.delivery
        self._lock = monitor._lock
        self.save_state = monitor.save_state

    def _notify(self, title: str, message: str, url: str) -> bool:
        """Persist a warning; its delivery is independent of the price read."""
        try:
            self.notifier.send(title, message, url)
            return True
        except Exception as exc:  # noqa: BLE001
            log(f"Bildirim gönderilemedi, sonraki çevrimde yeniden denenecek: {title} | {exc}")
            return False


    @staticmethod
    def _begin_rows(run: "CycleRun", key: str) -> None:
        """Forget the rows this watch put on the table earlier in the cycle (it is read or planned again)."""
        mine = run.rows_of.pop(key, None)
        if mine:
            gone = {id(row) for row in mine[0] + mine[1]}
            run.summary_rows[:] = [row for row in run.summary_rows if id(row) not in gone]
            run.stock_rows[:] = [row for row in run.stock_rows if id(row) not in gone]


    @staticmethod
    def _add_rows(run: "CycleRun", key: str, summary_rows=(), stock_rows=()) -> None:
        mine = run.rows_of.setdefault(key, ([], []))
        mine[0].extend(summary_rows)
        mine[1].extend(stock_rows)
        run.summary_rows.extend(summary_rows)
        run.stock_rows.extend(stock_rows)


    def _guarded(self, run: "CycleRun", watch: WatchRule, key: str, entry: Dict[str, Any], seller: str) -> bool:
        """True while the site is paused after a protection page; the watch keeps its last result."""
        guard_key = state_ops.site_guard_key(watch.site)
        remaining = state_ops.guard_remaining_seconds(run.state, guard_key)
        if remaining <= 0:
            return False
        self._begin_rows(run, key)
        self._add_rows(run, key, summary.cached_summary_rows(watch, key, run.state, seller),
                       summary.cached_stock_rows(watch, entry, seller))
        self._log_pause(run, guard_key, seller, remaining)
        return True


    @staticmethod
    def _log_pause(run: "CycleRun", guard_key: str, seller: str, remaining: int) -> None:
        """One line a minute while a site is paused."""
        guard = state_ops.guard_store(run.state).get(guard_key, {})
        last_logged = parse_iso_datetime(guard.get("last_skip_logged_at"))
        if not last_logged or (local_now().astimezone(timezone.utc) - last_logged).total_seconds() >= 60:
            guard["last_skip_logged_at"] = utc_now()
            log(f"{seller} erişim molasında ({guard.get('kind', 'captcha')}): tüm {seller} kartları bekliyor | "
                f"kalan={max(1, math.ceil(remaining / 60))} dk")


    def _stock_return(self, provider: Provider, watch: WatchRule, entry: Dict[str, Any], seller: str, offers):
        """Notify once when a product that was out of stock is available again."""
        if not (provider.notifies_stock_return and entry.get("last_out_of_stock_at")):
            return offers, entry, False
        offers = list(offers)
        if not offers:
            return offers, entry, False

        return offers, entry, True


    def _record_offers(self, run: "CycleRun", provider: Provider, watch: WatchRule, entry: Dict[str, Any],
                       seller: str, offers) -> "RecordedOffers":
        """Record streamed offers; network reads and notifications stay outside the lock."""
        state = run.state
        key = make_watch_key(watch)
        offers, entry, stock_return_sent = self._stock_return(provider, watch, entry, seller, offers)
        offer_iterator = iter(offers)
        first_offer = next(offer_iterator, None)
        group_fallback = str(first_offer.title or "").split(" / ", 1)[0].strip() if first_offer else ""
        recorded = RecordedOffers(entry, *summary.result_group_for_watch(watch, group_fallback))
        for offer in chain([first_offer], offer_iterator) if first_offer else ():
            if not offer.price.is_finite() or offer.price <= 0 or offer.currency != "TRY":
                raise HermesError("Ürün fiyatı veya para birimi doğrulanamadı.")
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
                price_statements = []
                if not replayed and not state_ops.is_absurd_price(offer_entry, offer.price, watch.target_price):
                    price_statements.append(self.history.price_statement(item_key, watch.site, display_name, offer.price))
                self._add_rows(run, key, [PriceSummaryRow(
                    seller=seller, product_title=display_name, product_url=matched_url, price=offer.price,
                    target_price=watch.target_price, min_price=min_price, max_price=max_price,
                    search_group=recorded.search_group, search_group_label=recorded.search_group_label,
                    is_warehouse=offer.is_warehouse, tracking_id=watch.tracking_id, priority=watch.priority,
                    price_checked_at=price_checked_at,
                )])
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

            alert_queued = wants_alert
            seller_note = f" ({offer.seller})" if offer.seller and (provider.alert_shows_seller or offer.is_warehouse) else ""
            message = (f"Site: {seller}\n{display_name}\n"
                       f"Güncel fiyat: {format_tl(offer.price, with_currency=True)}{seller_note}\n"
                       f"Hedef fiyat: {format_tl(watch.target_price, with_currency=True)}")
            title = f"{seller} Depo fırsatı" if offer.is_warehouse else f"{seller} fiyat alarmı"
            with self._lock:
                updated = state_ops.updated_offer_entry(state.get(item_key, offer_entry), offer.price, watch.target_price,
                                                        False, context)
                updated.update({
                    "title": display_name, "url": matched_url, "configured_url": watch.url, "watch_name": watch.name,
                    "tracking_id": watch.tracking_id, "size": watch.size, "site": watch.site,
                    "include_variations": watch.include_variations, "priority": watch.priority,
                    "last_price_checked_at": price_checked_at, "search_group": recorded.search_group,
                    "search_group_label": recorded.search_group_label, "is_warehouse": offer.is_warehouse,
                    "warehouse_evidence": bool(offer.is_warehouse), "source": offer.source or provider.site,
                    "currency": offer.currency, "conditions": offer.conditions, "last_error": None, "last_error_status": None,
                })
                if alert_queued:
                    updated["pending_alert_price"] = str(offer.price)
                state[item_key] = updated
                persistence_started = time.monotonic()
                try:
                    # Observation and delivery intent commit together before any network send.
                    if alert_queued:
                        identity = hashlib.sha256(json.dumps([item_key, str(offer.price),
                            offer_entry.get("last_alerted_at"), offer_entry.get("last_missing_at"),
                            state.get("_meta", {}).get("notification_generation")]).encode()).hexdigest()
                        payload = {"title": title, "message": message, "url": matched_url,
                                   "offer_key": item_key, "price": str(offer.price), "target": str(watch.target_price),
                                   "generation": state.get("_meta", {}).get("notification_generation"),
                                   "opportunity": json.loads(json.dumps([asdict(watch), asdict(offer), display_name, matched_url], default=str))}
                        self.notifier.enqueue(payload, identity, state, price_statements)
                        with self.notifier.db.lock:
                            status = self.notifier.db.connect().execute("SELECT state FROM outbox WHERE id=?", (identity,)).fetchone()[0]
                        if status != "pending":
                            updated.pop("pending_alert_price", None)
                    else:
                        self.notifier.db.transaction(price_statements + [
                            ("INSERT OR REPLACE INTO snapshots VALUES (?,?)", ("state.json", json.dumps(state, ensure_ascii=False)))])
                except Exception:
                    state[item_key] = offer_entry
                    self.history._failed = True
                    self._begin_rows(run, key)
                    raise
                self.save_state(state, committed=True)
                summary.save_incremental_summary(self.files.summary, run.summary_rows, run.stock_rows)
                self.monitor._jobs.persist_ms = getattr(self.monitor._jobs, "persist_ms", 0) + round((time.monotonic()-persistence_started)*1000)

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


    def _record_success(self, run: "CycleRun", provider: Provider, watch: WatchRule, key: str, seller: str,
                        recorded: "RecordedOffers", outcome: WatchRead) -> None:
        disappeared = set(recorded.entry.get("offer_keys") or []) - set(recorded.offer_keys)
        for stale_key in disappeared if not (outcome.blocked or outcome.errors) else []:
            stale = run.state.get(stale_key, {})
            if isinstance(stale, dict):
                stale["last_error"] = "Ürün son okumada bulunamadı."
                stale.pop("pending_alert_price", None)
        base = dict(recorded.entry)
        returned_at = base.pop("last_out_of_stock_at", None)
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
            "unavailable_checked_at": utc_now(),
        }
        stale_ids = summary.cached_offer_ids(watch, key, {**run.state, key: recorded.entry}, seller)
        if stale_ids:
            summary.save_incremental_summary(self.files.summary, run.summary_rows, run.stock_rows,
                removed_price_ids=stale_ids)
        if returned_at and provider.notifies_stock_return and recorded.offer_keys:
            available = [run.state[item] for item in recorded.offer_keys]
            lowest = min(available, key=lambda item: Decimal(item["last_price"]))
            offer_key = next(item for item in recorded.offer_keys if run.state[item] is lowest)
            self.notifier.enqueue({"title": f"{seller} stok alarmı",
                "message": f"Site: {seller}\n{lowest['title']}\nStok yeniden geldi.\nGüncel fiyat: {format_tl(Decimal(lowest['last_price']), with_currency=True)}",
                "url": lowest["url"], "offer_key": offer_key, "price": lowest["last_price"],
                "target": str(watch.target_price), "stock_return": True,
                "generation": run.state.get("_meta", {}).get("notification_generation")},
                hashlib.sha256(f"stock:{key}:{returned_at}".encode()).hexdigest(), run.state)
        self._add_rows(run, key, (), summary.cached_stock_rows(watch, run.state[key], seller))
        if not provider.backs_off_on_protection:
            return
        if outcome.blocked:
            # A block marks the visitor, not the page: the whole site pauses.
            state_ops.note_guard(run.state, state_ops.site_guard_key(watch.site), watch.name or watch.url,
                                 outcome.blocked, seller)
            run.state[key]["last_error"] = str(outcome.blocked)
            run.state[key]["amazon_partial_result"] = True
        else:
            state_ops.clear_guard(run.state, state_ops.site_guard_key(watch.site), seller)


    def _record_out_of_stock(self, run: "CycleRun", provider: Provider, watch: WatchRule, key: str, entry: Dict[str, Any],
                             seller: str, outcome: WatchRead, exc: OutOfStockHermesError) -> None:
        stale_ids = summary.cached_offer_ids(watch, key, run.state, seller)
        for offer_key in entry.get("offer_keys") or []:
            previous = run.state.get(offer_key, {})
            previous = state_ops.reset_alert_after_missing(previous, seller, watch.name or watch.url)
            previous.pop("pending_alert_price", None)
            previous["last_error"] = "Ürün stokta yok."
            run.state[offer_key] = previous
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
            "unavailable_checked_at": utc_now(),
            "amazon_no_offer_retry_after": outcome.retry_after,
        })
        run.state[key] = failed
        stock_rows = summary.cached_stock_rows(watch, failed, seller)
        self._add_rows(run, key, (), stock_rows)
        if provider.backs_off_on_protection:
            state_ops.clear_guard(run.state, state_ops.site_guard_key(watch.site), seller)
        # A missing item must not keep its previous price visible until the cycle ends.
        summary.save_incremental_summary(self.files.summary, [], stock_rows, removed_price_ids=stale_ids)


    def _record_failure(self, run: "CycleRun", provider: Provider, watch: WatchRule, key: str, entry: Dict[str, Any],
                        seller: str, outcome: WatchRead, exc: BaseException, load: Optional[SystemLoad] = None) -> None:
        stale_ids = summary.cached_offer_ids(watch, key, run.state, seller)
        is_search = provider.is_search_url(watch.url)
        normal_empty = is_normal_empty_result(exc)
        if normal_empty:
            log(f"Arama sonucu boş: {seller} | {watch.name or watch.url}")
        else:
            log(f"Hata: {seller} | {watch.url} | {exc}" + (f" | {load.text}" if load and load.text else ""))
        for offer_key in entry.get("offer_keys") or []:
            previous = run.state.get(offer_key, {})
            previous["last_error"] = str(exc)
            previous.pop("pending_alert_price", None)
            if normal_empty or alerts.missing_price_resets_alert(exc):
                previous = state_ops.reset_alert_after_missing(previous, seller, watch.name or watch.url)
            run.state[offer_key] = previous
        access_error = outcome.blocked or exc
        # A protection page keeps the watch's last rows on the table; they carry their read time.
        kept_after_block = provider.backs_off_on_protection and bool(outcome.blocked or provider.is_protection_error(exc))
        if provider.backs_off_on_protection:
            if outcome.blocked or provider.is_protection_error(exc):
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
            failed["unavailable_checked_at"] = utc_now()
            failed["amazon_no_offer_retry_after"] = (
                datetime.now(timezone.utc) + timedelta(seconds=NO_RESULTS_RECHECK_SECONDS)
            ).isoformat()
        run.state[key] = failed
        stock_rows = summary.cached_stock_rows(watch, failed, seller)
        self._add_rows(run, key, (), stock_rows)
        if kept_after_block:
            self._add_rows(run, key, summary.cached_summary_rows(watch, key, run.state, seller))
            return
        # The dashboard may still show the last successful cycle; remove only this watch's stale rows now.
        summary.save_incremental_summary(self.files.summary, [], stock_rows, removed_price_ids=stale_ids)
