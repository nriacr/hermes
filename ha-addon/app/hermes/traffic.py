"""Counts the internet traffic Hermes itself causes, per site and per day.

Every HTTP response that Hermes reads is added to an in-memory tally (requests made,
bytes received on the wire, bytes after decompression, bytes sent). A background thread
writes the tally into the `traffic` table of `hermes.db` once a minute and one summary
line is logged every ten minutes. Counting never changes a request and never raises:
a response that cannot be measured is simply not counted.

The figures are a lower bound of the real line usage: TLS handshakes, TCP/IP headers,
DNS and the Telegram (Telethon) connection are not included. Wire bytes are the compressed
body plus the response headers; sent bytes are the request line, headers and body.
"""

import contextlib
import threading
import time
from collections import Counter
from datetime import datetime
from typing import Dict, List, Optional

import requests

from .logging_utils import log

FLUSH_EVERY_SECONDS = 60
LOG_EVERY_SECONDS = 600
UNBOUND_SITE = "diger"

_local = threading.local()
_lock = threading.Lock()
_pending: Dict[str, List[int]] = {}   # site -> [requests, wire_rx, body_rx, tx], not yet stored
_window: Dict[str, List[int]] = {}    # same figures, for the ten-minute log line
_window_since = time.monotonic()
_flusher: Optional[threading.Thread] = None
_history = None


@contextlib.contextmanager
def bind(site: str):
    """The calling thread's requests are counted for this site until the block ends."""
    previous = getattr(_local, "site", None)
    _local.site = site
    try:
        yield
    finally:
        _local.site = previous


def current_site() -> str:
    return getattr(_local, "site", None) or UNBOUND_SITE


def add(site: str, wire_rx: int, body_rx: int, tx: int, requests_made: int = 1) -> None:
    figures = (requests_made, max(0, int(wire_rx)), max(0, int(body_rx)), max(0, int(tx)))
    with _lock:
        for target in (_pending, _window):
            row = target.setdefault(site, [0, 0, 0, 0])
            for index, value in enumerate(figures):
                row[index] += value


def _headers_size(headers) -> int:
    return sum(len(str(name)) + len(str(value)) + 4 for name, value in headers.items())


def _request_bytes(prepared) -> int:
    body = prepared.body
    body_size = len(body) if isinstance(body, (bytes, str)) else 0
    return len(prepared.method or "") + len(prepared.path_url or "") + 13 + _headers_size(prepared.headers) + body_size


def _one_response(response) -> List[int]:
    raw = getattr(response, "raw", None)
    tell = getattr(raw, "tell", None)
    body = response.content if getattr(response, "_content", False) is not False else b""
    wire_body = int(tell()) if callable(tell) else len(body)
    status_line = 15 + len(str(getattr(response, "reason", "") or ""))
    wire = wire_body + status_line + _headers_size(response.headers)
    return [wire, len(body), _request_bytes(response.request)]


def count_requests(response, site: Optional[str] = None) -> None:
    """A `requests` response, with every redirect hop that led to it."""
    try:
        wire = body = sent = hops = 0
        for item in [*response.history, response]:
            one = _one_response(item)
            wire, body, sent, hops = wire + one[0], body + one[1], sent + one[2], hops + 1
        add(site or current_site(), wire, body, sent, hops)
    except Exception:  # noqa: BLE001 - counting must never disturb a request
        pass


def count_curl(response, site: Optional[str] = None):
    """A curl_cffi response (other responses are ignored). Returns the response unchanged."""
    try:
        if hasattr(response, "download_size"):
            wire = int(response.download_size) + int(response.header_size)
            sent = int(response.request_size) + int(response.upload_size)
            add(site or current_site(), wire, len(response.content), sent)
    except Exception:  # noqa: BLE001
        pass
    return response


class CountedSession(requests.Session):
    """A requests session that counts every response it receives."""

    def request(self, method, url, **kwargs):
        response = super().request(method, url, **kwargs)
        count_requests(response)
        return response


# -- storage ----------------------------------------------------------------------


def _day() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def flush(history=None) -> None:
    """Write what was counted since the last flush (one transaction, a few small rows)."""
    target = history or _history
    if target is None:
        return
    with _lock:
        taken = {site: list(row) for site, row in _pending.items() if any(row)}
        _pending.clear()
    if taken:
        try:
            stored = target.record_traffic(_day(), taken)
        except Exception:  # noqa: BLE001
            stored = False
        if not stored:  # keep the numbers for the next try
            with _lock:
                for site, row in taken.items():
                    again = _pending.setdefault(site, [0, 0, 0, 0])
                    for index, value in enumerate(row):
                        again[index] += value


def _summary_line() -> str:
    global _window_since
    with _lock:
        elapsed = time.monotonic() - _window_since
        if elapsed < LOG_EVERY_SECONDS or not _window:
            return ""
        window = {site: list(row) for site, row in _window.items()}
        _window.clear()
        _window_since = time.monotonic()
    total = Counter()
    parts = []
    for site, (count, wire, body, sent) in sorted(window.items(), key=lambda item: -item[1][1]):
        total.update({"count": count, "wire": wire, "body": body, "sent": sent})
        parts.append(f"{site} {wire / 1e6:.2f} MB ({count} istek)")
    minutes = elapsed / 60
    return (f"İnternet kullanımı (son {minutes:.0f} dk, Hermes'in kendi trafiği; TLS/DNS payı hariç): "
            f"indirilen {total['wire'] / 1e6:.2f} MB (açılmış {total['body'] / 1e6:.2f} MB), gönderilen {total['sent'] / 1e6:.2f} MB, "
            f"{total['count']} istek, yaklaşık {total['wire'] / 1e6 * 1440 / minutes:.0f} MB/gün hızıyla | " + " | ".join(parts))


def _flush_loop() -> None:
    while True:
        time.sleep(FLUSH_EVERY_SECONDS)
        flush()
        line = _summary_line()
        if line:
            log(line)


def start(history) -> None:
    """Begin the once-a-minute storage of the tally (the first call starts the thread)."""
    global _flusher, _history
    _history = history
    if _flusher is None:
        _flusher = threading.Thread(target=_flush_loop, name="traffic-flush", daemon=True)
        _flusher.start()
