"""Amazon transport: one canonical request, terminal protection, one browser fallback.

The client lives for the whole process so anonymous cookies and connections
survive between cycles. Page caches are passed in per cycle; a later cycle
always reads prices from the server again.
"""

import os
import threading
import time
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit

import requests

from ...constants import (
    AMAZON_COOKIE_MAX_AGE_SECONDS,
    CHROME_CLIENT_HINTS,
    CHROME_USER_AGENT,
    SITE_AMAZON,
    SITE_MIN_REQUEST_GAP_SECONDS,
)
from ...errors import BotProtectionHermesError, HermesError, error_status
from ...logging_utils import log
from ...storage import load_json, save_json
from ...utils import canonical_amazon_product_url, extract_asin_from_url, normalize_offer_text, referer_for_url, repair_mojibake
from ..base import RequestSpacing
from ..http import cleaned_html, curl_requests, decode_response_text
from .access import MAIN_LANE, AmazonAccess
from .browser import AmazonBrowser

PROTECTION_MESSAGE = "Amazon bot koruması nedeniyle doğrulama (captcha) sayfası döndü."
STABLE_PRODUCT_PARAMS = {"smid", "psc", "th"}
SEARCH_PAGE_MARKERS = (
    'data-component-type="s-search-result"',
    "data-component-type='s-search-result'",
    "s-search-result",
    'id="search"',
    "id='search'",
    'data-cy="title-recipe"',
    "data-cy='title-recipe'",
    "puis-card-container",
    "/dp/",
    "/gp/product/",
)
# A cycle's response cache, keyed by (expect_search, request URL).
PageCache = Dict[Tuple[bool, str], str]


def amazon_headers(url: str) -> Dict[str, str]:
    return {
        "User-Agent": CHROME_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        **CHROME_CLIENT_HINTS,
        "Referer": referer_for_url(url),
    }


def is_product_url(url: str) -> bool:
    parsed = urlsplit(url)
    return "amazon." in parsed.netloc.lower() and any(part in parsed.path for part in ("/dp/", "/gp/product/"))


def request_url(url: str) -> str:
    """Canonical product address with only variant-selecting parameters kept."""
    if not is_product_url(url):
        return url
    kept_params = [(key, value) for key, value in parse_qsl(urlsplit(url).query, keep_blank_values=True)
                   if key in STABLE_PRODUCT_PARAMS]
    clean_url = canonical_amazon_product_url(url)
    return f"{clean_url}?{urlencode(kept_params)}" if kept_params else clean_url


class _ChallengeParser(HTMLParser):
    """Visible text and validation forms, ignoring scripts and templates."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ignored_depth = 0
        self.in_title = False
        self.title = []
        self.text = []
        self.reason = ""

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "template", "noscript"}:
            self.ignored_depth += 1
        if self.ignored_depth:
            return
        attrs = dict(attrs)
        if tag == "form" and "validatecaptcha" in str(attrs.get("action", "")).lower():
            self.reason = "captcha_formu"
        if tag == "input" and str(attrs.get("id", "")).lower() == "captchacharacters":
            self.reason = "captcha_alani"
        if tag == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag in {"script", "style", "template", "noscript"}:
            self.ignored_depth = max(0, self.ignored_depth - 1)
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if not self.ignored_depth:
            self.text.append(data)
            if self.in_title:
                self.title.append(data)


def protection_reason(html: str) -> str:
    """Why a page is a real challenge; a raw `captcha` word in a script is not one."""
    if not any(marker in html.lower() for marker in ("captcha", "robot", "automated access", "characters")):
        return ""
    parser = _ChallengeParser()
    parser.feed(html)
    if parser.reason:
        return parser.reason
    if "robot check" in normalize_offer_text(" ".join(parser.title)):
        return "robot_check_basligi"
    visible = normalize_offer_text(" ".join(parser.text))
    for marker in ("enter the characters you see below", "type the characters you see",
                   "robot olmadiginizi", "for automated access to amazon"):
        if marker in visible:
            return "gorunen_dogrulama_metni"
    return ""


def is_protection_page(html: str) -> bool:
    return bool(protection_reason(html))


def is_protection_error(exc: BaseException) -> bool:
    """A challenge page or HTTP 429/503: terminal for this read, back off the watch."""
    if isinstance(exc, BotProtectionHermesError) or error_status(exc) in {429, 503}:
        return True
    message = normalize_offer_text(str(exc))
    return any(marker in message for marker in ("captcha", "robot", "bot korumasi", "koruma sayfasi"))


def block_reason(exc: BaseException) -> str:
    status_code = error_status(exc)
    if status_code:
        return f"http_{status_code}"
    if is_protection_error(exc):
        return "bot_korumasi"
    return type(exc).__name__


def _request_type(url: str, expect_search: bool) -> str:
    if expect_search:
        return "arama"
    return "ürün" if is_product_url(url) else "sayfa"


def _short_url(url: str) -> str:
    parsed = urlsplit(str(url or ""))
    return f"{parsed.netloc}{parsed.path}"[:120]


def checked_html(response, expect_search: bool) -> str:
    """Raise for a challenge page, a failed status or a page of the wrong kind."""
    # A failed status wins: an HTTP 503 page is classified as 503 even when it
    # also contains a challenge form.
    response.raise_for_status()
    html = decode_response_text(response)
    reason = protection_reason(html)
    if reason:
        raise BotProtectionHermesError(PROTECTION_MESSAGE, challenge_reason=reason, http_status=response.status_code)
    lowered = html.lower()
    if "amazon" not in lowered or (expect_search and not any(marker in lowered for marker in SEARCH_PAGE_MARKERS)):
        raise HermesError("Amazon beklenen arama/ürün sayfası yerine boş veya farklı bir sayfa döndürdü.")
    return cleaned_html(response)


def _seed_session(session) -> None:
    session.cookies.set("i18n-prefs", "TRY", domain=".amazon.com.tr")
    session.cookies.set("lc-acbtr", "tr_TR", domain=".amazon.com.tr")


COOKIE_SAVE_EVERY_SECONDS = 60


def _cookie_jar(session):
    cookies = session.cookies
    return getattr(cookies, "jar", cookies)


def load_cookies(session, path: Optional[Path]) -> int:
    """Put the saved anonymous cookies back, so a restart does not look like a new visitor."""
    if path is None:
        return 0
    try:
        if time.time() - path.stat().st_mtime > AMAZON_COOKIE_MAX_AGE_SECONDS:
            return 0
    except OSError:
        return 0
    stored = load_json(path, [])
    restored = 0
    for item in stored if isinstance(stored, list) else []:
        try:
            if not str(item["domain"]).endswith("amazon.com.tr"):
                continue
            expires = item.get("expires")
            if expires is not None and float(expires) <= time.time():
                continue
            session.cookies.set(str(item["name"]), str(item["value"]), domain=str(item["domain"]),
                                path=str(item.get("path") or "/"))
            restored += 1
        except (KeyError, TypeError, ValueError):
            continue
    return restored


def save_cookies(session, path: Optional[Path]) -> None:
    if path is None or session is None:
        return
    items = [{"name": cookie.name, "value": cookie.value, "domain": cookie.domain, "path": cookie.path,
              "expires": cookie.expires}
             for cookie in _cookie_jar(session) if str(cookie.domain).endswith("amazon.com.tr")]
    try:
        save_json(path, items)
        os.chmod(path, 0o600)
    except OSError as exc:
        log(f"Amazon çerezleri kaydedilemedi: {exc}")


class AmazonClient:
    """Process-lived anonymous transports; page/offer caches stay cycle-local."""

    def __init__(self, transport: str = "http", spacing: Optional[RequestSpacing] = None,
                 access: Optional[AmazonAccess] = None, cookies_path: Optional[Path] = None):
        # "http" reads with curl (Chrome TLS) and falls back to Chromium once;
        # "browser" reads only through Chromium (used by the link test option).
        self.transport = transport
        self.requests_session = requests.Session()
        _seed_session(self.requests_session)
        self.curl_session = None
        self.browser = AmazonBrowser()
        # Request budget (window, slow start) and the cookie jar that survives restarts.
        self.access = access or AmazonAccess()
        # Two lane threads read through this client: the Depo lane ("main") and the variant sweep ("").
        # Requests go one at a time; the Depo lane goes first when both wait.
        self._local = threading.local()
        self._request_lock = threading.Lock()
        self._main_waiting = 0
        self._main_waiting_lock = threading.Lock()
        self.cookies_path = cookies_path
        self._cookies_saved_at = 0.0
        # Every network request (product, variant, listing, search detail,
        # browser fallback) waits for this gap; cached pages never do.
        self.spacing = spacing or RequestSpacing(SITE_MIN_REQUEST_GAP_SECONDS[SITE_AMAZON])
        self.base_gap_seconds = self.spacing.min_gap_seconds
        # Only absent/unreadable offers, with discovery metadata, never successful prices.
        self.unavailable_product_pages: dict = {}
        # Variant pages a watch excludes by title: their neighbours (edges) are kept for a while
        # so the page itself is not requested every sweep. Process-wide, bounded.
        self.excluded_pages: dict = {}

    @property
    def lane(self) -> str:
        return getattr(self._local, "lane", "")

    @lane.setter
    def lane(self, value: str) -> None:
        self._local.lane = value

    def close(self) -> None:
        try:
            if self.curl_session is not None:
                save_cookies(self.curl_session, self.cookies_path)
                self.curl_session.close()
            self.requests_session.close()
        finally:
            self.browser.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def fetch(self, url: str, timeout: int, expect_search: bool = False, cache: Optional[PageCache] = None,
              on_request: Optional[Callable[[str, str, str, int], None]] = None) -> str:
        """Return the cleaned HTML of one Amazon page.

        A challenge or HTTP 429/503 is terminal. Never reset cookies or multiply
        requests through URL/transport variants after the server rejects a read.
        Another failure gets at most one Chromium read of the same address.
        `on_request` receives every network request: (method, kind, outcome, ms).
        """
        cache = {} if cache is None else cache
        candidate = request_url(url)
        key = (expect_search, candidate)
        if key in cache:
            return cache[key]
        if self.transport == "browser":
            html = self._timed("browser", candidate, expect_search, lambda: self._browser_read(candidate, timeout, expect_search), on_request)
        else:
            method = "curl" if curl_requests is not None else "requests"
            try:
                html = self._timed(method, candidate, expect_search, lambda: self._http_read(candidate, timeout, expect_search), on_request)
            except Exception as exc:  # noqa: BLE001
                if is_protection_error(exc):
                    raise
                log(f"Amazon {method} okuması başarısız, tarayıcıyla bir kez denenecek: {block_reason(exc)} | {_short_url(candidate)}")
                html = self._timed("browser", candidate, expect_search, lambda: self._browser_read(candidate, timeout, expect_search), on_request)
        cache[key] = html
        return html

    def _timed(self, method: str, url: str, expect_search: bool, read, on_request=None):
        lane = self.lane
        # The window is waited for before the request lock, so a full lane never holds the other one back.
        self.access.wait_for_window(lane)
        with self._turn(lane):
            return self._timed_locked(method, url, expect_search, read, on_request, lane)

    @contextmanager
    def _turn(self, lane: str):
        """One request at a time; the Depo lane goes before a waiting sweep."""
        if lane == MAIN_LANE:
            with self._main_waiting_lock:
                self._main_waiting += 1
            try:
                self._request_lock.acquire()
            finally:
                with self._main_waiting_lock:
                    self._main_waiting -= 1
        else:
            waited = 0.0
            while self._main_waiting and waited < 10:
                time.sleep(0.05)
                waited += 0.05
            self._request_lock.acquire()
        try:
            yield
        finally:
            self._request_lock.release()

    def _timed_locked(self, method: str, url: str, expect_search: bool, read, on_request, lane: str):
        # Half speed after a block and right after a start: the gap doubles.
        self.spacing.min_gap_seconds = self.base_gap_seconds * self.access.gap_multiplier()
        waited = self.spacing.wait()
        if waited >= 0.05:
            log(f"Amazon istek aralığı için {waited:.1f} sn ek bekleme.")
        self.access.request_started(lane)
        self.access.count("istek_depo" if lane == MAIN_LANE else "istek_tarama")
        started_at = time.monotonic()
        outcome = "ok"
        blocked = False
        try:
            return read()
        except Exception as exc:  # noqa: BLE001
            outcome = block_reason(exc)
            blocked = is_protection_error(exc)
            if blocked:
                log(f"Amazon engeli: sebep={outcome} | yöntem={method} | adres={_short_url(url)}")
            raise
        finally:
            self.access.request_finished(blocked, extract_asin_from_url(url) or url)
            if outcome == "ok":
                self._save_cookies_soon()
            elapsed_ms = round((time.monotonic() - started_at) * 1000)
            kind = _request_type(url, expect_search)
            log(f"Amazon isteği: yöntem={method} | tip={kind} | şerit={'depo' if lane == MAIN_LANE else 'tarama'} | "
                f"sonuç={outcome} | süre={elapsed_ms} ms | adres={_short_url(url)}")
            if on_request is not None:
                on_request(method, kind, outcome, elapsed_ms)

    def _http_read(self, url: str, timeout: int, expect_search: bool) -> str:
        if curl_requests is not None:
            if self.curl_session is None:
                self.curl_session = curl_requests.Session()
                _seed_session(self.curl_session)
                restored = load_cookies(self.curl_session, self.cookies_path)
                if restored:
                    log(f"Amazon çerezleri geri yüklendi: adet={restored}")
            response = self.curl_session.get(
                url, headers=amazon_headers(url), timeout=timeout, allow_redirects=True, impersonate="chrome124"
            )
        else:
            response = self.requests_session.get(url, headers=amazon_headers(url), timeout=timeout, allow_redirects=True)
        return checked_html(response, expect_search)

    def _save_cookies_soon(self) -> None:
        now = time.monotonic()
        if self.curl_session is not None and now - self._cookies_saved_at >= COOKIE_SAVE_EVERY_SECONDS:
            self._cookies_saved_at = now
            save_cookies(self.curl_session, self.cookies_path)

    def _browser_read(self, url: str, timeout: int, expect_search: bool) -> str:
        return checked_html(self.browser.read(url, timeout), expect_search)


def short_amazon_url(url: str) -> str:
    clean = repair_mojibake(str(url or "")).strip()
    return clean[:117] + "..." if len(clean) > 120 else clean
