import threading
from datetime import datetime
from typing import Callable

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
        _output(f"[{now}] {message}")
