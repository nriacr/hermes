import json
import logging.handlers
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


_file_sink: logging.handlers.RotatingFileHandler | None = None


def enable_file_log(path, max_bytes: int = 1_000_000, backups: int = 4) -> None:
    """Also keep redacted JSON-lines diagnostics in a small rotating file (5 x 1 MB)."""
    global _file_sink
    try:
        sink = logging.handlers.RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
    except OSError:
        return
    sink.setFormatter(logging.Formatter("%(message)s"))
    _file_sink = sink


def _write_file(moment: datetime, text: str) -> None:
    sink = _file_sink
    if sink is None:
        return
    try:
        sink.handle(logging.makeLogRecord({"msg": json.dumps(
            {"ts": moment.isoformat(timespec="seconds"), "msg": text}, ensure_ascii=False)}))
    except Exception:
        pass  # diagnostics must never break monitoring


def log(message: str) -> None:
    moment = datetime.now().astimezone()
    text = redact(message)
    # Monitor, web and Telegram threads share stdout; keep each line intact.
    with _PRINT_LOCK:
        _output(f"[{moment.strftime('%Y-%m-%d %H:%M:%S')}] {text}")
        _write_file(moment, text)


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
