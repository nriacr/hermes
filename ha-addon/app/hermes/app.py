"""Hermes application: one process for the monitor, both web panels and Telegram."""

import os
import signal
import threading
from typing import Optional, Tuple

from .config import load_config
from .constants import APP_VERSION, INGRESS_PORT, PUBLIC_PORT
from .logging_utils import log
from .models import HermesConfig
from .monitor.cycle import DataFiles
from .monitor.runner import MonitorService, reset_notifications, reset_price_history
from .telegram.listener import start_telegram_listener
from .web.server import Router, start_server

# How long a panel action waits for the monitor to apply it before answering.
ACTION_WAIT_SECONDS = 5


class HermesRuntime:
    """Connects the web panel to the monitor without sharing mutable state."""

    def __init__(self, config: Optional[HermesConfig], config_error: str = "", files: Optional[DataFiles] = None) -> None:
        self.config = config
        self.config_error = config_error
        self.files = files or DataFiles()
        self.stop_event = threading.Event()
        self.service: Optional[MonitorService] = MonitorService(config, self.files) if config else None

    def health(self) -> Tuple[bool, str]:
        if self.service is None:
            # Monitoring is stopped on purpose until the settings are fixed;
            # the panel itself is healthy so the user can correct them.
            return True, "ayar hatası"
        return self.service.health()

    def _action(self, name: str, apply, done_message: str, queued_message: str) -> Tuple[bool, str]:
        try:
            if self.service is None or self.service.finished:
                count = apply(self.files)
                return True, done_message.format(count=count)
            command = self.service.submit(name)
            if not command.wait(ACTION_WAIT_SECONDS):
                return True, queued_message
            if command.error:
                return False, f"İşlem tamamlanamadı: {command.error}"
            return True, done_message.format(count=command.result)
        except Exception as exc:  # noqa: BLE001
            log(f"Panel işlemi başarısız: {name} | {exc}")
            return False, f"İşlem tamamlanamadı: {exc}"

    def reset_notifications(self) -> Tuple[bool, str]:
        return self._action(
            "reset_notifications", reset_notifications,
            "Bildirim susturma hafızası sıfırlandı ({count} kayıt). Hedef altında kalan fırsatlar için yeni kontrol hemen başlıyor.",
            "Şu an bir tarama sürüyor. Bildirim sıfırlaması tarama biter bitmez uygulanacak ve hemen yeni kontrol başlayacak.",
        )

    def reset_price_history(self) -> Tuple[bool, str]:
        return self._action(
            "reset_price_history", reset_price_history,
            "Min/maks fiyat geçmişi sıfırlandı. Temizlenen kayıt alanı: {count}.",
            "Şu an bir tarama sürüyor. Min/maks sıfırlaması tarama biter bitmez uygulanacak.",
        )

    def stop(self) -> None:
        self.stop_event.set()
        if self.service is not None:
            self.service.stop()


def main() -> int:
    log(f"Hermes v{APP_VERSION} başlatılıyor.")
    try:
        config, config_error = load_config(), ""
    except Exception as exc:  # noqa: BLE001
        config, config_error = None, str(exc)
        log(f"Ayar hatası, izleme başlatılmadı; panel açık: {exc}")
    runtime = HermesRuntime(config, config_error)
    router = Router(runtime)
    servers = [start_server(router, INGRESS_PORT, public_only=False), start_server(router, PUBLIC_PORT, public_only=True)]

    def request_stop(signum, _frame) -> None:
        log(f"Durdurma sinyali alındı ({signal.Signals(signum).name}); Hermes kapanıyor.")
        runtime.stop()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        if config is not None:
            start_telegram_listener(config, runtime.stop_event)
            runtime.service.run()
        else:
            runtime.stop_event.wait()
    finally:
        for server in servers:
            server.shutdown()
    return 0


def run_once() -> int:
    """`RUN_ONCE=1`: a single cycle for manual diagnosis, without panel or Telegram."""
    service = MonitorService(load_config())
    try:
        service.monitor.run_cycle()
    finally:
        service.monitor.close()
    return 0


def entrypoint() -> int:
    return run_once() if os.getenv("RUN_ONCE", "").strip() == "1" else main()
