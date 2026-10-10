import threading
import time
from collections import OrderedDict
import re
from datetime import datetime
from typing import Callable

_SECRETS: set[str] = set()

def configure_secrets(values) -> None:
    _SECRETS.update(str(value) for value in values if value and len(str(value)) >= 5)

def redact(value: str) -> str:
    text = re.sub(r"/public/[^/\s?\"']+", "/public/[gizli]", str(value))
    text = re.sub(r"(?i)(token|api_key|api_hash|password|session)=([^&\s]+)", r"\1=[gizli]", text)
    for secret in sorted(_SECRETS, key=len, reverse=True):
        text = text.replace(secret, "[gizli]")
    return text

_PRINT_LOCK = threading.Lock()


def _print(line: str) -> None:
    print(line, flush=True)


_output: Callable[[str], None] = _print


def set_output(output: Callable[[str], None]) -> None:
    """Redirect log lines, e.g. to keep test output readable."""
    global _output
    _output = output


def log(message: str) -> None:
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    # Monitor, web and Telegram threads share stdout; keep each line intact.
    with _PRINT_LOCK:
        _output(f"[{now}] {redact(message)}")


class ProblemLog:
    """Bounded per-identity summaries; full incidents remain in the durable store."""
    def __init__(self, emit=log, clock=time.monotonic, interval=300, limit=512):
        self.emit, self.clock, self.interval, self.limit = emit, clock, interval, limit
        self.entries = OrderedDict()
        self.lock = threading.Lock()

    def failure(self, key, message):
        with self.lock:
            now = self.clock()
            previous = self.entries.pop(key, None)
            if previous is None:
                self.emit(message)
                self.entries[key] = (now, 0, message)
            else:
                started, repeats, _ = previous
                repeats += 1
                if now - started >= self.interval:
                    self.emit(f"{message} | {repeats} tekrar / {round(now-started)} sn")
                    started, repeats = now, 0
                self.entries[key] = (started, repeats, message)
            while len(self.entries) > self.limit:
                _, (started, repeats, message) = self.entries.popitem(last=False)
                if repeats:
                    self.emit(f"{message} | {repeats} tekrar / {round(now-started)} sn")

    def recovered(self, key):
        with self.lock:
            previous = self.entries.pop(key, None)
            if previous:
                _, repeats, message = previous
                self.emit(f"Sorun düzeldi: {message} | {repeats} ek tekrar")
