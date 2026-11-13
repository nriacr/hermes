"""Fresh modem observations and the stages exposed by a failed request."""

import re
import socket
import ssl
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

import requests

from ..errors import BotProtectionHermesError, HermesError, PriceUnavailableHermesError, error_status
from ..utils import parse_iso_datetime

NETWORK_COMPONENT = "internet"
NETWORK_LABELS = {"planned": "Planlı modem işlemi", "offline": "İnternet erişimi yok"}
TRANSPORT_STAGES = {"dns", "tcp", "tls", "timeout", "transport"}


class NetworkPaused(BaseException):
    """Stop a pending read without invoking a provider's retry or site error rules."""


def _fresh(value, now, max_age):
    timestamp = parse_iso_datetime(value) if isinstance(value, str) else None
    return timestamp is not None and -2 <= (now - timestamp).total_seconds() <= max_age


@dataclass(frozen=True)
class Access:
    state: str = "unknown"
    observed_at: str | None = None
    operation: str | None = None
    operation_age_seconds: int | None = None
    probe_at: str | None = None
    web_probe_at: str | None = None
    reachable: bool | None = None
    dns_ok: bool | None = None
    http_ok: bool | None = None
    dns_partial: bool | None = None
    http_partial: bool | None = None

    @classmethod
    def from_modem(cls, modem, now=None):
        now = now or datetime.now(timezone.utc)
        ping = modem.get("ping")
        ping = ping if isinstance(ping, dict) else {}
        planned = modem.get("planned")
        planned = planned if isinstance(planned, dict) else {}
        operation = planned.get("operation")
        age = planned.get("seconds")
        fresh = _fresh(ping.get("at"), now, 40)
        web_fresh = _fresh(ping.get("net_checked_at"), now, 60)
        active = type(age) is int and 0 <= age < {"reboot": 300, "reconnect": 120}.get(str(operation), 0)
        state = "unknown"
        if active:
            state = "planned"
        elif fresh and (ping.get("reachable") is False or (
                web_fresh and ping.get("dns_ok") is False and ping.get("http_ok") is False)):
            state = "offline"
        elif fresh and ping.get("reachable") is True and (
                not web_fresh or ping.get("dns_ok") is True or ping.get("http_ok") is True):
            state = "online"
        return cls(state, now.isoformat(), operation if active else None, age if active else None,
                   ping.get("at"), ping.get("net_checked_at"),
                   ping.get("reachable") if fresh else None,
                   *(ping.get(key) if fresh and web_fresh else None
                     for key in ("dns_ok", "http_ok", "dns_partial", "http_partial")))

    @property
    def paused(self):
        return self.state in NETWORK_LABELS

    @property
    def label(self):
        if self.state == "planned":
            return "Planlı modem yeniden başlatması" if self.operation == "reboot" else "Planlı modem bağlantı yenilemesi"
        return NETWORK_LABELS.get(self.state, "")

    def evidence(self):
        return {key: value for key, value in asdict(self).items() if value is not None}


def failure_details(exc):
    """Classify only phases supported by the actual exception chain."""
    chain = []
    pending = [exc]
    while pending and len(chain) < 12:
        item = pending.pop(0)
        if any(item is previous for previous in chain):
            continue
        chain.append(item)
        pending.extend(cause for cause in (item.__cause__ or item.__context__, getattr(item, "reason", None),
                                           *item.args)
                       if isinstance(cause, BaseException))
    names = [type(item).__name__ for item in chain]
    kinds = " ".join(names).lower()
    message = str(exc).lower()
    http = next((status for item in chain if (status := error_status(item))), None)
    code = next((int(value) for item in chain if isinstance(value := getattr(item, "code", None), int)
                 and type(item).__module__.startswith("curl_cffi")), None)
    if code is None and (match := re.search(r"curl:\s*\((\d+)\)", message)):
        code = int(match[1])
    if http or isinstance(exc, BotProtectionHermesError):
        stage = "http"
    elif code in {5, 6} or any(isinstance(item, socket.gaierror) for item in chain) or "nameresolution" in kinds or "could not resolve" in message:
        stage = "dns"
    elif code in {35, 51, 58, 59, 60, 77, 82, 83, 90, 91} or "sslerror" in kinds or "certificateverify" in kinds or any(isinstance(item, ssl.SSLError) for item in chain):
        stage = "tls"
    elif code == 7 or "connecttimeout" in kinds or "newconnectionerror" in kinds or "no route to host" in message:
        stage = "tcp"
    elif "readtimeout" in kinds:
        stage = "response_timeout"
    elif code == 28 or "timeout" in kinds or "timed out" in message:
        stage = "timeout"
    elif any(isinstance(item, (requests.ConnectionError, ConnectionError)) for item in chain) or code in {52, 55, 56}:
        stage = "transport"
    elif isinstance(exc, PriceUnavailableHermesError):
        stage = "price"
    elif isinstance(exc, HermesError):
        stage = "content"
    else:
        stage = "unknown"
    errno = next((item.errno for item in chain if isinstance(getattr(item, "errno", None), int)), None)
    transport = next((item.hermes_transport for item in chain if isinstance(getattr(item, "hermes_transport", None), dict)), {})
    return {key: value for key, value in {"stage": stage, "exceptions": names, "http_status": http,
                                            "curl_code": code, "errno": errno, **transport}.items() if value is not None}
