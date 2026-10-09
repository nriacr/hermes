"""One monitoring cycle: read due watches, record prices, notify, publish the table."""

import random
import hashlib
from dataclasses import asdict
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

from ..constants import (
    APP_VERSION,
    CYCLE_HISTORY_PATH,
    DATABASE_PATH,
    PRIORITIES,
    PRIORITY_INTERVAL_SECONDS,
    PRIORITY_LABELS,
    SITE_MIN_REQUEST_GAP_SECONDS,
    STATE_PATH,
    SUMMARY_PATH,
)
from ..errors import BotProtectionHermesError, HermesError, OutOfStockHermesError, error_status
from ..history import History, read_requests
from ..delivery import DeliveryQueue
from ..diagnostics import Diagnostics
from ..homeassistant import HomeAssistantBridge
from ..logging_utils import log
from ..models import HermesConfig, OfferResult, PriceSummaryRow, StockSummaryRow, WatchRule
from ..notifier import Pushover
from ..providers.base import DEPO_LANE, Provider, ReadContext, RequestSpacing, WatchRead
from ..providers.registry import ProviderSet
from ..providers.http import MeasuredSession
from ..storage import load_json, save_json, export_json
from ..utils import SystemLoad, parse_iso_datetime, site_label, system_load, utc_now
from . import alerts, scheduling, state as state_ops, summary
from .state import watch_key as make_watch_key
from .results import is_normal_empty_result, ResultRecorder

# How often the Depo lane looks for a main-page read that has become due.
DEPO_LANE_POLL_SECONDS = 1.0


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


def read_outcome(provider: Provider, exc: BaseException) -> str:
    """Measurement label of a failed read: captcha, http_<status>, timeout, connection, unreadable or error."""
    status = error_status(exc)
    if status:
        return f"http_{status}"
    if isinstance(exc, BotProtectionHermesError) or provider.is_protection_error(exc):
        return "captcha"
    # A provider error may wrap the transport failure (e.g. the browser's TimeoutException).
    causes = (exc, exc.__cause__) if exc.__cause__ is not None else (exc,)
    names = [type(item).__name__.lower() for item in causes]
    if any(isinstance(item, requests.Timeout) for item in causes) or any("timeout" in name for name in names):
        return "timeout"
    if any(isinstance(item, requests.ConnectionError) for item in causes) or any("connection" in name for name in names):
        return "connection"
    if isinstance(exc, HermesError):
        # Hermes reached the page but could not find a product or price on it.
        return "unreadable"
    return "error"


@dataclass
class CycleRun:
    """Everything one cycle collects while it reads watches."""

    state: Dict[str, Any]
    summary_rows: List[PriceSummaryRow] = field(default_factory=list)
    stock_rows: List[StockSummaryRow] = field(default_factory=list)
    # The rows each watch put on the table this cycle: a watch that is read again replaces its own rows.
    rows_of: Dict[str, tuple] = field(default_factory=dict)
    search_failures: List[Dict[str, Any]] = field(default_factory=list)
    read_started: bool = False
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
        for provider in self.providers:
            provider.set_request_delay(config.request_delay_min_seconds, config.request_delay_max_seconds)
        self.notifier = notifier or Pushover(config.pushover_user_key, config.pushover_api_token, config.request_timeout_seconds)
        self.files = files or DataFiles()
        self.sleep = sleep
        # Site queues run in parallel; every change to the cycle's shared
        # state, summary and files happens under this lock.
        self._lock = threading.RLock()
        # Minimum gaps between request starts per site; they span cycles.
        self._spacing: Dict[str, RequestSpacing] = {}
        self._jobs = threading.local()
        # False after a cycle that read no watch (nothing due or all paused); such a cycle logs nothing.
        self.last_cycle_read = False
        self._last_durations = (0.0, 0.0)
        self.history = History.at(self.files.database)
        self.history.migrate_json(self.files.state, self.files.cycle_history)
        self.history.drop_idle_cycles(self.config.interval_seconds)
        self._state = self.load_state()
        with self.history._lock:
            points = self.history._db().execute("SELECT count(*) FROM prices").fetchone()[0]
        migration = {"state_entries": len([key for key in self._state if key != "_meta"]),
                     "price_points": points, "suppression_entries": sum(bool(entry.get("last_alerted_at"))
                         for entry in self._state.values() if isinstance(entry, dict))}
        self.history.database.transaction([("INSERT OR IGNORE INTO meta VALUES (?,?)",
                                           ("v4_migration", json.dumps(migration)))])
        self.save_state(self._state)
        self.diagnostics = Diagnostics(self.files.database)
        self.diagnostics.interrupt_abandoned()
        self.diagnostics.recover("config", "Ayarlar doğrulandı")
        for message in self.config.config_errors:
            self.diagnostics.incident("config", "watch", message, "Hatalı kart atlandı; geçerli takipler devam ediyor")
        self.delivery = DeliveryQueue(self.files.database, self.notifier, self._delivery_finished)
        self.results = ResultRecorder(self)
        # A restart must not forget the last hour of requests (Amazon's window and block counts).
        since = datetime.now(timezone.utc) - timedelta(hours=1)
        for provider in self.providers:
            provider.restore_requests(read_requests(self.files.database, provider.site, since))

    def close(self) -> None:
        self.delivery.close()
        if self.delivery.thread is not None and self.delivery.thread.is_alive():
            return  # In-flight send retains its DB; process shutdown owns final termination.
        self.delivery.drain()
        self.providers.close()
        self.notifier.close()
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

    def save_state(self, state: Dict[str, Any], *, committed=False) -> None:
        with self._lock:
            if committed:
                export_json(self.files.state, state)
            else:
                save_json(self.files.state, state)

    def _watch_names(self) -> Dict[str, List[str]]:
        names: Dict[str, List[str]] = {}
        for watch in self.config.watches:
            if watch.name:
                names.setdefault(watch.site, []).append(watch.name)
        return names


    # -- the cycle -------------------------------------------------------------

    def run_cycle(self, site: str | None = None) -> None:
        started_at = time.monotonic()
        if site is None:
            with self._lock:
                self._state = self.load_state()
        run = CycleRun(state=self._state)
        with self._lock:
            state_ops.drop_watch_guards(run.state)
        if site is None:
            self.providers.begin_cycle()
        else:
            self.providers[site].begin_cycle()
        queues: Dict[str, List[WatchRule]] = {}
        for watch in self.config.watches:
            if site is not None and watch.site != site:
                continue
            with self._lock:
                if self._plan(run, watch):
                    queues.setdefault(watch.site, []).append(watch)
        self.last_cycle_read = False
        if self._run_site_queues(run, queues):
            # Shutting down: keep what was read, publish nothing partial.
            log("Hermes kapanıyor; çevrim yarıda bırakıldı, okunan sonuçlar kaydedildi.")
            self.save_state(run.state)
            return

        if run.read_started:
            log("Çevrim öncelik kapsamı: " + " | ".join(
                f"{label}={run.priority_scope[key]['started']} başladı, {run.priority_scope[key]['due']} sırası geldi, "
                f"{run.priority_scope[key]['deferred']} ertelendi"
                for key, label in PRIORITY_LABELS.items()
            ))
        with self._lock:
            if self.config.watches:
                # Runtime cycles belong to one site; the finite diagnostic cycle may span sites.
                if run.read_started:
                    scan_seconds = time.monotonic() - started_at
                    self._last_durations = (scan_seconds + self.config.interval_seconds, scan_seconds)
                    self.history.record_cycle(self._last_durations[0])
                # A cycle that read nothing (nothing due, or every due watch paused)
                # is not a cycle in the statistics; the last working one stays shown.
                cycle_seconds, scan_seconds = self._last_durations
                rows = summary.deduplicate_summary_rows(run.summary_rows)
                if site is not None:
                    with self._lock:
                        summary.save_incremental_summary(self.files.summary, rows, run.stock_rows)
                        previous = load_json(self.files.summary, {})
                        rows = summary.rows_from_payload(previous)
                        stock = summary.stock_rows_from_payload(previous)
                else:
                    stock = run.stock_rows
                summary.publish_price_summary(self.files.summary, rows, stock,
                                              cycle_seconds if run.read_started else None,
                                              scan_seconds if run.read_started else None)
                alerts.maybe_alert_summary_drop(run.state, rows, self.config, self.delivery)
                alerts.maybe_alert_search_failures(run.state, run.search_failures, self.delivery)
        if self.config.watches:
            self.on_cycle_published(run, rows, cycle_seconds, scan_seconds)
        if site is None:
            self.delivery.drain()
        self.save_state(run.state)

    def _delivery_finished(self, payload, delivered, identity, status):
        key = payload.get("offer_key")
        with self._lock:
            if key:
                entry = self._state.get(key, {})
                generation = self._state.get("_meta", {}).get("notification_generation")
                if delivered and generation == payload.get("generation"):
                    entry["last_alerted_price"] = payload["price"]
                    entry["last_alerted_at"] = utc_now()
                if generation == payload.get("generation") and entry.get("pending_alert_price") == payload.get("price"):
                    entry.pop("pending_alert_price", None)
            # Delivery acknowledgement and suppression are one durable transaction.
            self.delivery.db.transaction([
                ("INSERT OR REPLACE INTO snapshots VALUES (?,?)", ("state.json", json.dumps(self._state, ensure_ascii=False))),
                ("UPDATE outbox SET state=?,delivered=? WHERE id=?", (status, time.time() if delivered else None, identity)),
            ])
            self.save_state(self._state, committed=True)
        if delivered and payload.get("opportunity"):
            watch, offer, title, url = payload["opportunity"]
            from decimal import Decimal
            watch["target_price"] = Decimal(watch["target_price"])
            if watch.get("minimum_price") is not None:
                watch["minimum_price"] = Decimal(watch["minimum_price"])
            offer["price"] = Decimal(offer["price"])
            self.on_opportunity(WatchRule(**watch), OfferResult(**offer), title, url)

    def on_cycle_published(self, run: "CycleRun", rows: List[PriceSummaryRow], cycle_seconds: float, scan_seconds: float) -> None:
        """Mirror the finished cycle as Home Assistant sensors."""
        if self.home_assistant is not None and self.home_assistant.enabled:
            self.home_assistant.publish_cycle(rows, len(run.stock_rows), run.state, cycle_seconds, scan_seconds)

    def on_opportunity(self, watch: WatchRule, offer: OfferResult, display_name: str, url: str) -> None:
        """Fire a Home Assistant event for a delivered opportunity notification."""
        if self.home_assistant is not None and self.home_assistant.enabled:
            self.home_assistant.publish_opportunity(watch, offer, display_name, url)

    def _measure_request(self, site, method, kind, outcome, duration_ms):
        self.diagnostics.progress[site] = time.monotonic()
        self._jobs.request_ms = getattr(self._jobs, "request_ms", 0) + duration_ms
        self._jobs.request_count = getattr(self._jobs, "request_count", 0) + 1
        self.history.record_request(site, method, kind, outcome, duration_ms, getattr(self._jobs, "current", ""))

    def _site_pace(self, site: str) -> Callable[[str], None]:
        """The random delay, then the site's minimum gap since its previous request start."""
        if self.providers[site].spaces_own_requests:
            # The provider waits before each of its own requests (Amazon, see set_request_delay).
            return lambda _label: None
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
        slow or protected site never delays the others. A provider with a Depo
        lane (Amazon) gets a second thread beside its sweep queue: the lane
        repeats the quick main-page reads of the watches whose next read is
        one, for as long as the sweep queue is busy, so a long variant sweep
        never holds them back. Both threads share the site's budget and pause.
        """
        watch_names = self._watch_names()
        stopped = threading.Event()
        failures: List[BaseException] = []

        def context(session, site: str, lane: str = "") -> ReadContext:
            return ReadContext(timeout=self.config.request_timeout_seconds, session=session,
                               pace=self._site_pace(site), watch_names=watch_names, lane=lane,
                               measure=lambda method, kind, result, ms, site=site:
                               self._measure_request(site, method, kind, result, ms))

        def work(site_watches: List[WatchRule], sweep_done: Optional[threading.Event] = None) -> None:
            try:
                if not site_watches:
                    return
                site = site_watches[0].site
                with MeasuredSession(lambda method, kind, result, ms: self._measure_request(site, method, kind, result, ms)) as session:
                    ctx = context(session, site)
                    provider = self.providers[site]
                    for watch in scheduling.priority_order(site_watches, provider.read_rank):
                        # The Depo lane may be reading this watch's main page right now: wait for it.
                        while provider.is_watch_busy(watch) and not self.should_stop():
                            self.sleep(0.5)
                        if self.should_stop():
                            stopped.set()
                            return
                        self.check_watch(run, ctx, watch)
            except BaseException as exc:  # noqa: BLE001 - re-raised in the cycle's own thread
                failures.append(exc)
            finally:
                if sweep_done is not None:
                    sweep_done.set()

        def work_depo(site: str, site_watches: List[WatchRule], sweep_done: threading.Event) -> None:
            provider = self.providers[site]
            try:
                with requests.Session() as session:
                    ctx = context(session, site, DEPO_LANE)
                    while True:
                        progressed = False
                        for watch in scheduling.priority_order(site_watches, provider.read_rank):
                            if self.should_stop():
                                stopped.set()
                                return
                            if self._depo_lane_due(run, provider, watch):
                                self.check_watch(run, ctx, watch)
                                progressed = True
                        if sweep_done.is_set():
                            return
                        if not progressed:
                            sweep_done.wait(DEPO_LANE_POLL_SECONDS)
            except BaseException as exc:  # noqa: BLE001 - re-raised in the cycle's own thread
                failures.append(exc)

        workers = []
        for site, site_watches in queues.items():
            provider = self.providers[site]
            if provider.has_depo_lane:
                # The sweep queue gets the watches whose family is due for a sweep; the Depo lane looks after the
                # main page of every active watch of the site, also those not due when the cycle began.
                sweeps = [watch for watch in site_watches if provider.needs_sweep(watch)]
                # Planned watches only the Depo lane reads keep their last rows on the table until it has read them.
                with self._lock:
                    for watch in site_watches:
                        if watch not in sweeps:
                            key = make_watch_key(watch)
                            entry = run.state.get(key, {})
                            entry = entry if isinstance(entry, dict) else {}
                            self.results._begin_rows(run, key)
                            self.results._add_rows(run, key, summary.cached_summary_rows(watch, key, run.state, site_label(watch.site)),
                                           summary.cached_stock_rows(watch, entry, site_label(watch.site)))
                depo_watches = [watch for watch in self.config.watches if watch.site == site and watch.active]
                sweep_done = threading.Event()
                workers.append(threading.Thread(target=work, args=(sweeps, sweep_done), name=f"hermes-{site}-tarama", daemon=True))
                workers.append(threading.Thread(target=work_depo, args=(site, depo_watches, sweep_done), name=f"hermes-{site}-depo", daemon=True))
                if not sweeps:
                    sweep_done.set()
            else:
                workers.append(threading.Thread(target=work, args=(site_watches,), name=f"hermes-{site}", daemon=True))
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
                self.results._begin_rows(run, key)
                self.results._add_rows(run, key, summary.cached_summary_rows(watch, key, run.state, seller),
                               summary.cached_stock_rows(watch, entry, seller))
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
            if provider.backs_off_on_protection and self.results._guarded(run, watch, key, entry, seller):
                return
            self.results._begin_rows(run, key)
            run.priority_scope[scheduling.watch_priority(watch)]["started"] += 1
            if not run.read_started:
                run.read_started = True
                # Logged with the first watch that is read: cycles in which every
                # due watch is paused or nothing is due stay out of the log.
                self.last_cycle_read = True
                log_cycle_banner(self.config)
        started_at = time.monotonic()
        previous_check = parse_iso_datetime(entry.get("last_checked_at"))
        interval = max(self.config.interval_seconds, PRIORITY_INTERVAL_SECONDS.get(scheduling.watch_priority(watch), 0))
        planned = previous_check.timestamp() + interval if previous_check and not scheduling.manually_due(watch, entry) else None
        job = self.diagnostics.start(watch.site, key, planned)
        self._jobs.current = job
        self._jobs.request_ms = self._jobs.request_count = self._jobs.persist_ms = 0
        result = "ok"
        failure: Optional[BaseException] = None
        load: Optional[SystemLoad] = None
        try:
            ctx.pace(f"{seller} | {(watch.name or watch.url)[:64]}")
            offers = (offer for offer in provider.read(watch, ctx, outcome) if provider.keeps_offer(watch, offer))
            recorded = self.results._record_offers(run, provider, watch, entry, seller, offers)
            if outcome.errors:
                self.diagnostics.incident(key, "partial", "; ".join(outcome.errors), "Diğer varyantlar okunmaya devam etti")
            else:
                self.diagnostics.recover(key, "Okuma tamamlandı")
            if outcome.blocked:
                failure = outcome.blocked
                result = read_outcome(provider, failure)
                load = system_load()
                self.diagnostics.incident(key, "read", str(failure), f"{seller}: mevcut erişim kuralı uygulanıyor")
            with self._lock:
                self.results._record_success(run, provider, watch, key, seller, recorded, outcome)
        except OutOfStockHermesError as exc:
            result = "stock"
            self.diagnostics.recover(key, "Stok durumu doğrulandı")
            with self._lock:
                self.results._record_out_of_stock(run, provider, watch, key, entry, seller, outcome, exc)
        except Exception as exc:  # noqa: BLE001
            if is_normal_empty_result(exc):
                result = "empty"
                self.diagnostics.recover(key, "Arama sonucu doğrulandı")
            else:
                failure = outcome.blocked or exc
                result = read_outcome(provider, failure)
                load = system_load()
                self.diagnostics.incident(key, "read", str(failure), f"{seller}: sonraki planlı okumada tekrar denenecek")
            with self._lock:
                self.results._record_failure(run, provider, watch, key, entry, seller, outcome, exc, load)
        finally:
            elapsed = round((time.monotonic() - started_at) * 1000)
            self.diagnostics.finish(job, watch.site, result, elapsed, str(failure) if failure else "",
                                    {"unavailable": len(outcome.unavailable), "partial_errors": outcome.errors,
                                     "requests": self._jobs.request_count, "request_ms": self._jobs.request_ms,
                                     "persist_ms": self._jobs.persist_ms,
                                     "provider_and_wait_ms": max(0, elapsed-self._jobs.request_ms-self._jobs.persist_ms),
                                     "config_id": hashlib.sha256(json.dumps(asdict(watch), sort_keys=True, default=str).encode()).hexdigest(),
                                     "offers": [{"key": item, "source": run.state.get(item, {}).get("source"),
                                         "currency": run.state.get(item, {}).get("currency"),
                                         "conditions": run.state.get(item, {}).get("conditions"),
                                         "checked_at": run.state.get(item, {}).get("last_price_checked_at")}
                                        for item in run.state.get(key, {}).get("offer_keys", [])]})
            self.history.record_read(watch.site, result, round((time.monotonic() - started_at) * 1000), key,
                                     scheduling.watch_priority(watch), str(failure) if failure else "", load)

    def _depo_lane_due(self, run: CycleRun, provider: Provider, watch: WatchRule) -> bool:
        """True for a watch whose next read is a quick main-page read and whose rhythm and schedule say it is due."""
        with self._lock:
            guard_key = state_ops.site_guard_key(watch.site)
            remaining = state_ops.guard_remaining_seconds(run.state, guard_key)
            if remaining > 0:
                self.results._log_pause(run, guard_key, site_label(watch.site), remaining)
                return False
            entry = run.state.get(make_watch_key(watch), {})
            entry = entry if isinstance(entry, dict) else {}
            due = scheduling.watch_check_due(watch, entry, self.config.interval_seconds)
        return due and provider.next_read_is_main(watch) and provider.read_due(watch)
