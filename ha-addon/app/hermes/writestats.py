"""Counts the bytes Hermes itself hands to the disk, so disk-write questions have numbers.

Only what Hermes writes is counted (SQL parameters and exported JSON files), not the extra
journal/fsync work of SQLite; one summary line is logged every few minutes.
"""

import threading
import time
from collections import Counter

from .logging_utils import log

LOG_EVERY_SECONDS = 600

_lock = threading.Lock()
_bytes: Counter = Counter()
_calls: Counter = Counter()
_since = time.monotonic()


def record(label: str, size: int) -> None:
    global _since
    summary = ""
    with _lock:
        _bytes[label] += size
        _calls[label] += 1
        elapsed = time.monotonic() - _since
        if elapsed >= LOG_EVERY_SECONDS:
            parts = [f"{name} {_bytes[name] / 1e6:.1f} MB ({_calls[name]}x)" for name, _ in _bytes.most_common()]
            total = sum(_bytes.values())
            summary = (f"Yazma özeti (son {elapsed / 60:.0f} dk, Hermes'in verdiği veri; SQLite'ın WAL/fsync payı hariç): "
                       f"toplam {total / 1e6:.1f} MB = {total / 1e6 / (elapsed / 60):.2f} MB/dk | " + " | ".join(parts))
            _bytes.clear()
            _calls.clear()
            _since = time.monotonic()
    if summary:
        log(summary)


def statement_bytes(statements) -> int:
    return sum(len(value) for _sql, params in statements for value in params if isinstance(value, str))
