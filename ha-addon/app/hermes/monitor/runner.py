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
from datetime import timedelta
from typing import Any, Callable, Dict, Optional

from ..constants import APP_VERSION
from ..logging_utils import log
from ..models import HermesConfig
from ..notifier import Pushover
from ..providers.registry import ProviderSet
from ..storage import file_lock, load_json, save_json
from ..utils import format_local_datetime, local_now
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
    log(f"Min/maks fiyat geçmişi sıfırlandı: alan={count}")
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
}


def log_cycle_banner(config: HermesConfig) -> None:
    line = "=" * 92
    log(line)
    log(f">>> HERMES v{APP_VERSION} | YENİ KONTROL TURU | Kontrol aralığı: {config.interval_seconds} saniye <<<")
    log(line)


class MonitorService:
    def __init__(self, config: HermesConfig, files: Optional[DataFiles] = None, providers: Optional[ProviderSet] = None,
                 notifier: Optional[Pushover] = None) -> None:
        self.config = config
        self.files = files or DataFiles()
        self._commands: "queue.Queue[Command]" = queue.Queue()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self.monitor = Monitor(config, providers=providers, notifier=notifier, files=self.files, sleep=self._sleep,
                               should_stop=self._stop.is_set)
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
        log(f"Servis başladı. Hermes v{APP_VERSION} | Kontrol aralığı: {self.config.interval_seconds} saniye")
        try:
            while not self._stop.is_set():
                self._apply_commands()
                log_cycle_banner(self.config)
                self.cycle_started_at = time.monotonic()
                try:
                    self.monitor.run_cycle()
                except Exception:  # noqa: BLE001 - one failed cycle must not stop monitoring
                    log("Çevrim beklenmeyen bir hatayla bitti; sonraki çevrimde devam edilecek.\n" + traceback.format_exc())
                finally:
                    self.cycle_started_at = None
                    self.last_cycle_finished_at = time.monotonic()
                if self._stop.is_set():
                    break
                log(f"Sonraki kontrol: {format_local_datetime(local_now() + timedelta(seconds=self.config.interval_seconds))}")
                self._wait_for_next_cycle()
        finally:
            self.finished = True
            self.monitor.close()
            log("İzleyici durdu.")

    def _wait_for_next_cycle(self) -> None:
        deadline = time.monotonic() + self.config.interval_seconds
        while not self._stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._wake.wait(remaining)
            self._wake.clear()
            if self._apply_commands():
                log("Panel isteğiyle yeni kontrol turu hemen başlıyor.")
                return
