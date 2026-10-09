"""The monitoring loop and the actions the web panel hands to it.

Only this loop writes `state.json` while Hermes runs. Panel actions are queued
and applied between cycles, so a reset can never be overwritten by a cycle
that loaded the state earlier.
"""

import queue
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from ..constants import APP_VERSION
from ..history import History
from ..homeassistant import HomeAssistantBridge
from ..logging_utils import log
from ..models import HermesConfig
from ..notifier import Pushover
from ..providers.registry import ProviderSet
from ..storage import file_lock, load_json, save_json
from . import state as state_ops, summary
from .cycle import DataFiles, Monitor

# A cycle running longer than this is considered stuck; /health then fails so
# the Supervisor watchdog restarts Hermes.
STUCK_CYCLE_SECONDS = 3 * 60 * 60


def reset_notifications(files: DataFiles) -> int:
    with file_lock(files.state):
        state = load_json(files.state, {})
        state = state if isinstance(state, dict) else {}
        count = state_ops.clear_notification_suppression(state)
        save_json(files.state, state)
    log(f"Bildirim susturma hafızası sıfırlandı: kayıt={count}")
    return count


def reset_price_history(files: DataFiles) -> int:
    with file_lock(files.state):
        state = load_json(files.state, {})
        state = state if isinstance(state, dict) else {}
        count = state_ops.clear_price_history(state)
        save_json(files.state, state)
    summary.reset_summary_price_ranges(files.summary)
    History.at(files.database).clear_prices()
    log(f"Min/maks fiyat geçmişi sıfırlandı: alan={count}")
    return count


def reset_error_history(files: DataFiles) -> int:
    count = History.at(files.database).clear_errors()
    log(f"İstatistik hata kayıtları sıfırlandı: okuma={count}")
    return count


@dataclass
class Command:
    name: str
    apply: Callable[[DataFiles], Any]
    # Start a cycle right after the command (e.g. re-check after a reset).
    run_cycle: bool = False
    done: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: Optional[BaseException] = None

    def wait(self, timeout: float) -> bool:
        return self.done.wait(timeout)


COMMANDS: Dict[str, Callable[[], Command]] = {
    "reset_notifications": lambda: Command("reset_notifications", reset_notifications, run_cycle=True),
    "reset_price_history": lambda: Command("reset_price_history", reset_price_history),
    "reset_error_history": lambda: Command("reset_error_history", reset_error_history),
}


class MonitorService:
    def __init__(self, config: HermesConfig, files: Optional[DataFiles] = None, providers: Optional[ProviderSet] = None,
                 notifier: Optional[Pushover] = None, home_assistant: Optional[HomeAssistantBridge] = None) -> None:
        self.config = config
        self.files = files or DataFiles()
        self._commands: "queue.Queue[Command]" = queue.Queue()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self.monitor = Monitor(config, providers=providers, notifier=notifier, files=self.files, sleep=self._sleep,
                               should_stop=self._stop.is_set,
                               home_assistant=home_assistant if home_assistant is not None else HomeAssistantBridge())
        # Set when the loop has ended; until then panel actions are queued.
        self.finished = False
        self.cycle_started_at: Optional[float] = None
        self.last_cycle_finished_at: Optional[float] = None

    # -- control from other threads ------------------------------------------

    def submit(self, name: str) -> Command:
        command = COMMANDS[name]()
        self._commands.put(command)
        self._wake.set()
        return command

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def health(self) -> tuple[bool, str]:
        if self.finished:
            return False, "izleyici çalışmıyor"
        started = self.cycle_started_at
        if started is not None and time.monotonic() - started > STUCK_CYCLE_SECONDS:
            return False, "çevrim çok uzun sürdü"
        for site, since in getattr(self, "site_started", {}).copy().items():
            last_progress = max(since, self.monitor.diagnostics.progress.get(site, since))
            if time.monotonic() - last_progress > self.monitor.providers[site].progress_timeout_seconds:
                return False, f"{site} okuyucusu ilerlemiyor"
        if self.monitor.delivery.failed_since is not None and time.monotonic() - self.monitor.delivery.failed_since > 60:
            return False, "bildirim kuyruğu ilerlemiyor"
        if self.monitor.history._failed:
            return False, "çalışma kayıtları yazılamıyor"
        if self.monitor.delivery.thread is not None and not self.monitor.delivery.thread.is_alive():
            return False, "bildirim göndericisi durdu"
        return True, "ok"

    # -- the loop -------------------------------------------------------------

    def _sleep(self, seconds: float) -> None:
        """Request pacing that ends early when Hermes stops."""
        self._stop.wait(seconds)

    def _apply_commands(self) -> bool:
        run_now = False
        while True:
            try:
                command = self._commands.get_nowait()
            except queue.Empty:
                return run_now
            try:
                command.result = command.apply(self.files)
            except Exception as exc:  # noqa: BLE001
                command.error = exc
                log(f"Panel işlemi uygulanamadı: {command.name} | {exc}")
            finally:
                command.done.set()
            run_now = run_now or command.run_cycle

    def run(self) -> None:
        log(f"Servis başladı. Hermes v{APP_VERSION} | Bağımsız site kuyrukları")
        completions = queue.Queue()
        workers = {}
        sites = sorted({watch.site for watch in self.config.watches if watch.active})
        next_due = {site: 0.0 for site in sites}
        failures = {site: 0 for site in sites}
        self.site_started = {}
        self.monitor.delivery.start()
        maintenance_at = 0.0
        diagnostics_at = 0.0
        communicated = {}

        def work(site):
            error = None
            try:
                self.monitor.run_cycle(site)
            except Exception as exc:  # noqa: BLE001 - one queue cannot end another
                error = exc
                log(f"{site} kontrolü tamamlanamadı: {type(exc).__name__}")
                try:
                    self.monitor.diagnostics.incident(site, "round", traceback.format_exc(), "Sınırlı yeniden deneme")
                except Exception:  # noqa: BLE001 - storage failure is surfaced by health too
                    pass
            finally:
                completions.put((site, error))

        try:
            while not self._stop.is_set():
                now = time.monotonic()
                while True:
                    try:
                        site, error = completions.get_nowait()
                    except queue.Empty:
                        break
                    workers.pop(site).join()
                    self.site_started.pop(site, None)
                    failures[site] = min(failures[site] + 1, 6) if error else 0
                    next_due[site] = now + max(self.config.interval_seconds, min(300, 5 * 2 ** failures[site]) if error else 0)
                    self.last_cycle_finished_at = now
                # Destructive panel commands never race a reader. Work is deferred briefly to apply them.
                if not workers and not self._commands.empty():
                    with self.monitor._lock:
                        run_now = self._apply_commands()
                        self.monitor._state = self.monitor.load_state()
                    if run_now:
                        next_due = {site: 0.0 for site in sites}
                if now >= diagnostics_at:
                    active = self.monitor.diagnostics.active()
                    bridge = self.monitor.home_assistant
                    critical = {item["id"]: item for item in active if
                        (item["component"] == "delivery" and item["recovery"] == "Müdahale gerekli")
                        or (item["kind"] == "round" and item["count"] >= 3)
                        or (item["kind"] == "read" and item["count"] >= 3 and item["updated"]-item["opened"] >= 600)}
                    if bridge and bridge.enabled:
                        for identity, item in critical.items():
                            if communicated.get(identity) == item["opened"]:
                                continue
                            message = ("Bildirim gönderimi durdu. Pushover ayarlarını ve kotanı kontrol et. "
                                "Ürün kontrolleri devam ediyor; sorunlu bildirim kaydı korunuyor."
                                if item["component"] == "delivery" else
                                "Bir sitenin kontrolü tekrar tekrar tamamlanamadı. Diğer siteler çalışmaya devam ediyor. "
                                "Sınırlı yeniden deneme yapıldı; kayıt geliştirici incelemesi için saklandı.")
                            if item["kind"] == "read":
                                message = "Bir takip en az 10 dakikadır tekrar tekrar okunamadı. " + item["recovery"] + ". Ayrıntı Hermes panelinde; kontrol edilmesi gerekiyor."
                            if item["kind"] == "round":
                                self.monitor.delivery.send("Hermes takip uyarısı", message,
                                    event_id=f"incident:{identity}:{item['opened']}")
                            if bridge.persistent_problem(identity, message):
                                communicated[identity] = item["opened"]
                        for identity in set(communicated) - set(critical):
                            if bridge.clear_problem(identity):
                                communicated.pop(identity)
                    diagnostics_at = now + 5
                if now >= maintenance_at:
                    self.monitor.diagnostics.prune()
                    self.monitor.delivery.prune()
                    maintenance_at = now + 86400
                for site in sites:
                    if site in workers or now < next_due[site] or (not self._commands.empty() and workers):
                        continue
                    thread = threading.Thread(target=work, args=(site,), name=f"hermes-{site}-round", daemon=True)
                    workers[site] = thread
                    self.site_started[site] = now
                    thread.start()
                self.cycle_started_at = min(self.site_started.values()) if self.site_started else None
                self._wake.wait(0.5)
                self._wake.clear()
        finally:
            self.finished = True
            for thread in workers.values():
                thread.join(timeout=25)
            if not any(thread.is_alive() for thread in workers.values()):
                self.monitor.close()
            log("İzleyici durdu.")
