import threading
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
