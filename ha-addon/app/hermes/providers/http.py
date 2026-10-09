"""HTTP helpers shared by providers. Site-specific request flows live with each site."""

import time
from typing import Optional

import requests

from ..constants import RETRY_DELAYS_SECONDS, RETRY_STATUS_CODES
from ..errors import BotProtectionHermesError, HermesError, HttpStatusHermesError
from ..logging_utils import log
from ..utils import build_headers, normalize_offer_text, repair_mojibake

try:
    from curl_cffi import requests as curl_requests
except Exception:  # noqa: BLE001 - optional native dependency
    curl_requests = None

AGE_VERIFICATION_MARKERS = (
    "yas dogrulamasi",
    "yaş doğrulaması",
    "18 yasindan buyuk musunuz",
    "18 yaşından büyük müsünüz",
)


class HtmlResponse:
    """A response-like page produced by an API adapter or the browser."""

    def __init__(self, url: str, html: str, status_code=None):
        self.url = url
        self.status_code = status_code
        self.headers = {"content-type": "text/html; charset=utf-8"}
        self.text = html
        self.content = html.encode("utf-8", errors="replace")
        self.encoding = "utf-8"

    def raise_for_status(self) -> None:
        if isinstance(self.status_code, int) and self.status_code >= 400:
            raise HttpStatusHermesError(self.status_code, self.url)


def decode_response_text(response) -> str:
    fallback = response.text
    content_type = response.headers.get("content-type", "").lower()
    if "charset=" in content_type and "Ã" not in fallback:
        return fallback
    try:
        utf8_text = response.content.decode("utf-8")
    except UnicodeDecodeError:
        return fallback
    if "Ã" in fallback and "Ã" not in utf8_text:
        return utf8_text
    encoding = (getattr(response, "encoding", None) or "").lower()
    return utf8_text if not encoding or encoding in {"iso-8859-1", "latin-1"} else fallback


def cleaned_html(response) -> str:
    return repair_mojibake(decode_response_text(response))


def fetch_with_retries(session: requests.Session, url: str, timeout: int) -> requests.Response:
    """Plain GET that waits and retries transient server errors."""
    last_status: Optional[int] = None
    for attempt in range(len(RETRY_DELAYS_SECONDS) + 1):
        response = session.get(url, headers=build_headers(url), timeout=timeout)
        if response.status_code not in RETRY_STATUS_CODES:
            response.raise_for_status()
            return response
        last_status = response.status_code
        if attempt < len(RETRY_DELAYS_SECONDS):
            delay = RETRY_DELAYS_SECONDS[attempt]
            log(f"Site geçici hata verdi ({response.status_code}); {delay} saniye sonra tekrar denenecek.")
            time.sleep(delay)
    raise HttpStatusHermesError(last_status or 0, url)


def raise_if_age_verification(html: str) -> None:
    normalized = normalize_offer_text(html)
    if any(marker in normalized for marker in AGE_VERIFICATION_MARKERS):
        raise HermesError("Yaş doğrulaması gerekiyor. Bu sayfa otomatik takip edilemiyor.")


def has_generic_challenge(html: str) -> bool:
    """A page mentioning both captcha and robot is treated as a challenge."""
    lowered = html.lower()
    return "captcha" in lowered and "robot" in lowered


def bot_protection_message(label: str) -> str:
    return f"{label} bot koruması nedeniyle doğrulama (captcha) sayfası döndü."


def read_site_html(response, label: str, is_challenge=has_generic_challenge, message: str = "") -> str:
    """Decode a fetched page and reject age gates and challenge pages."""
    html = cleaned_html(response)
    raise_if_age_verification(html)
    if is_challenge(html):
        raise BotProtectionHermesError(message or bot_protection_message(label))
    return html


class MeasuredSession(requests.Session):
    """Persistent connections with one measurement for every actual request attempt."""

    def __init__(self, measure):
        super().__init__()
        self.measure = measure

    def request(self, method, url, **kwargs):
        started = time.monotonic()
        outcome = "error"
        try:
            response = super().request(method, url, **kwargs)
            outcome = f"http_{response.status_code}" if response.status_code >= 400 else "ok"
            return response
        except requests.Timeout:
            outcome = "timeout"
            raise
        except requests.ConnectionError:
            outcome = "connection"
            raise
        finally:
            self.measure("requests", method.lower(), outcome, round((time.monotonic() - started) * 1000))
