import json
import os
import re
import shutil
import tempfile
import time
from collections import deque
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from .constants import RETRY_DELAYS_SECONDS, RETRY_STATUS_CODES
from .errors import HermesError, HttpStatusHermesError
from .logging_utils import log
from .providers import amazon as amazon_provider
from .utils import build_headers, canonical_amazon_product_url, extract_asin_from_url, normalize_offer_text, repair_mojibake, referer_for_url, utc_now

try:
    from curl_cffi import requests as curl_requests
except Exception:
    curl_requests = None

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service as ChromeService
except ImportError:
    webdriver = None
    ChromeService = None

AMAZON_STABLE_SEARCH_PARAMS = {
    "__mk_tr_TR",
    "bbn",
    "dc",
    "field-keywords",
    "i",
    "k",
    "node",
    "rh",
    "srs",
    "url",
}

AMAZON_STABLE_PRODUCT_PARAMS = {
    "smid",
    "psc",
    "th",
}

AMAZON_CHROME_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

AMAZON_BROWSER_MIN_TIMEOUT_SECONDS = 25
AMAZON_BROWSER_SETTLE_SECONDS = 0.6
AMAZON_BROWSER_AUDIT_INTERVAL = 10
AMAZON_DIAGNOSTIC_SNIPPET_LENGTH = 220
HM_API_BASE_URL = "https://api.hm.com/search-services/v1/tr_tr/search/byids"


class AmazonClient:
    """Process-lived anonymous transports; page/offer caches stay cycle-local."""

    def __init__(self, requests_session=None, transport="http", browser_policy="ready_cached"):
        self.requests_session = requests_session if requests_session is not None else requests.Session()
        self.owns_requests_session = requests_session is None
        self.curl_session = None
        self.browser_profile = None
        self.browser_driver = None
        self.browser_timeout = None
        self.browser_policy = browser_policy
        self.browser_coverage = {}
        self.browser_audits = deque(maxlen=128)
        self.browser_audit_total = 0
        self.browser_timings = deque(maxlen=256)
        self.browser_timing_total = 0
        self.browser_header_error = None
        self.transport = transport
        # Only absent/unreadable offers, with discovery metadata, never successful prices.
        self.unavailable_product_pages = {}
        self.started_at = time.monotonic()
        self.attempt_times = deque()
        self.total_attempts = 0
        self.attempts_since_block = 0
        self.block_count = 0
        self.last_attempt_at = None
        self.min_request_gap_seconds = 0
        self.last_transport = ""
        self.last_address = ""
        self.block_events = deque(maxlen=200)
        _seed_amazon_session(self.requests_session)

    def close(self):
        try:
            if self.curl_session is not None:
                self.curl_session.close()
        finally:
            try:
                if self.owns_requests_session:
                    self.requests_session.close()
            finally:
                try:
                    if self.browser_driver is not None:
                        self.browser_driver.quit()
                finally:
                    if self.browser_profile is not None:
                        self.browser_profile.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def _amazon_client(session) -> AmazonClient:
    client = getattr(session, "_hermes_amazon_client", None)
    if client is None:
        client = AmazonClient(requests_session=session)
        setattr(session, "_hermes_amazon_client", client)
    return client


class _HtmlResponse:
    def __init__(self, url: str, html: str, status_code=None):
        self.url = url
        self.status_code = status_code
        self.headers = {"content-type": "text/html; charset=utf-8"}
        self.text = html
        self.content = html.encode("utf-8", errors="replace")

    def raise_for_status(self) -> None:
        if isinstance(self.status_code, int) and self.status_code >= 400:
            raise HttpStatusHermesError(self.status_code, self.url)


def decode_response_text(response: requests.Response) -> str:
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
    encoding = (response.encoding or "").lower()
    return utf8_text if not encoding or encoding in {"iso-8859-1", "latin-1"} else fallback


def fetch_with_retries(session: requests.Session, url: str, timeout: int) -> requests.Response:
    last_status: Optional[int] = None
    attempts = len(RETRY_DELAYS_SECONDS) + 1
    for attempt in range(attempts):
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


def amazon_headers(url: str):
    return {
        "User-Agent": AMAZON_CHROME_USER_AGENT,
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
        "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Linux"',
        "Referer": referer_for_url(url),
    }


def _clean_amazon_search_url(url: str) -> str:
    parsed = urlsplit(url)
    if "amazon." not in parsed.netloc.lower() or parsed.path.rstrip("/") not in {"/s", "/-/tr/s"}:
        return url
    kept_params = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key in AMAZON_STABLE_SEARCH_PARAMS
    ]
    if not kept_params:
        return url
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(kept_params), parsed.fragment))


def _is_amazon_product_url(url: str) -> bool:
    parsed = urlsplit(url)
    return "amazon." in parsed.netloc.lower() and any(part in parsed.path for part in ("/dp/", "/gp/product/"))


def _clean_amazon_product_url(url: str) -> str:
    if not _is_amazon_product_url(url):
        return url
    parsed = urlsplit(url)
    clean_url = canonical_amazon_product_url(url)
    kept_params = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key in AMAZON_STABLE_PRODUCT_PARAMS
    ]
    if kept_params:
        clean_url = f"{clean_url}?{urlencode(kept_params)}"
    return clean_url


def _with_amazon_locale_path(url: str) -> str:
    parsed = urlsplit(url)
    if "amazon." not in parsed.netloc.lower():
        return url
    path = parsed.path.rstrip("/")
    if path == "/s":
        return urlunsplit((parsed.scheme, parsed.netloc, "/-/tr/s", parsed.query, parsed.fragment))
    if path == "/-/tr/s":
        return urlunsplit((parsed.scheme, parsed.netloc, "/s", parsed.query, parsed.fragment))
    return url


def amazon_url_variants(url: str):
    variants = []

    def add(candidate: str) -> None:
        if candidate and candidate not in variants:
            variants.append(candidate)

    cleaned_product_url = _clean_amazon_product_url(url)
    add(cleaned_product_url)
    add(_with_amazon_locale_path(cleaned_product_url))
    add(url)
    add(_with_amazon_locale_path(url))
    cleaned_url = _clean_amazon_search_url(url)
    add(cleaned_url)
    add(_with_amazon_locale_path(cleaned_url))
    return variants


class _AmazonChallengeParser(HTMLParser):
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


def _amazon_protection_reason(html: str) -> str:
    if not any(marker in html.lower() for marker in ("captcha", "robot", "automated access", "characters")):
        return ""
    parser = _AmazonChallengeParser()
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


def is_amazon_protection_page(html: str) -> bool:
    return bool(_amazon_protection_reason(html))


def _amazon_response_protection_reason(response) -> str:
    reason = getattr(response, "_hermes_amazon_protection_reason", None)
    if reason is None:
        reason = _amazon_protection_reason(decode_response_text(response))
        response._hermes_amazon_protection_reason = reason
    return reason


def _is_usable_amazon_response(response, expect_search: bool) -> bool:
    html = decode_response_text(response)
    reason = _amazon_response_protection_reason(response)
    if reason:
        error = HermesError("Amazon bot korumasi nedeniyle captcha/koruma sayfasi dondu.")
        error.amazon_challenge_reason = reason
        error.amazon_http_status = response.status_code
        raise error
    lowered = html.lower()
    if "amazon" not in lowered:
        return False
    if not expect_search:
        return True
    return any(
        marker in lowered
        for marker in (
            "data-component-type=\"s-search-result\"",
            "data-component-type='s-search-result'",
            "s-search-result",
            'id="search"',
            "id='search'",
            "data-cy=\"title-recipe\"",
            "data-cy='title-recipe'",
            "puis-card-container",
            "/dp/",
            "/gp/product/",
        )
    )


def _note_amazon_request(session, transport: str, candidate: str) -> None:
    client = _amazon_client(session)
    now = time.monotonic()
    wait_started = now
    if client.min_request_gap_seconds and client.last_attempt_at is not None:
        remaining = client.last_attempt_at + client.min_request_gap_seconds - now
        if remaining > 0:
            log(f"Amazon karşılaştırma sorgu aralığı: {remaining:.2f} sn bekleniyor | yöntem={transport}")
            time.sleep(remaining)
            now = time.monotonic()
    pacing_wait_ms = round((now - wait_started) * 1000)
    while client.attempt_times and client.attempt_times[0] <= now - 60:
        client.attempt_times.popleft()
    gap_ms = round((now - client.last_attempt_at) * 1000) if client.last_attempt_at is not None else "-"
    client.attempt_times.append(now)
    client.total_attempts += 1
    client.attempts_since_block += 1
    client.last_attempt_at = now
    client.last_transport = transport
    client.last_address = _amazon_timing_url(candidate)
    _increment_amazon_metric(session, "network_attempts")
    trial_requests = getattr(session, "_hermes_amazon_trial_requests", None)
    if isinstance(trial_requests, list):
        trial_requests.append({"transport": transport, "address": client.last_address, "at": utc_now(),
                               "gap_ms": gap_ms, "pacing_wait_ms": pacing_wait_ms})
    log(
        "Amazon istek ölçümü: "
        f"taşıma={transport} | oturum_s={round(now - client.started_at)} | "
        f"oturum_deneme={client.total_attempts} | son_60sn_deneme={len(client.attempt_times)} | "
        f"ara_ms={gap_ms} | deney_bekleme_ms={pacing_wait_ms} | adres={_amazon_timing_url(candidate)}"
    )


def _log_amazon_response(response, transport: str) -> None:
    html = decode_response_text(response)
    log(
        "Amazon yanıt teşhisi: "
        f"taşıma={transport} | http={response.status_code if response.status_code is not None else 'bilinmiyor'} | "
        f"bayt={len(response.content)} | yönlendirme={len(getattr(response, 'history', []))} | "
        f"koruma={_amazon_response_protection_reason(response) or 'yok'} | "
        f"ürün_işareti={int('productTitle' in html)} | arama_işareti={int('s-search-result' in html)}"
    )


def _timed_amazon_network_call(session, transport: str, candidate: str, expect_search: bool, request):
    _note_amazon_request(session, transport, candidate)
    started_at = time.monotonic()  # Trial pacing is not network response latency.
    try:
        response = request()
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = round((time.monotonic() - started_at) * 1000)
        log(
            "Amazon ağ yanıt süresi: "
            f"taşıma={transport} | tip={_amazon_request_type(candidate, expect_search)} | "
            f"durum={amazon_error_status(exc) or 'hata'} | süre={elapsed_ms} ms | "
            f"adres={_amazon_timing_url(candidate)}"
        )
        raise
    elapsed_ms = round((time.monotonic() - started_at) * 1000)
    log(
        "Amazon ağ yanıt süresi: "
        f"taşıma={transport} | tip={_amazon_request_type(candidate, expect_search)} | "
        f"durum={getattr(response, 'status_code', '-')} | süre={elapsed_ms} ms | "
        f"adres={_amazon_timing_url(candidate)}"
    )
    _log_amazon_response(response, transport)
    return response


def _amazon_cycle_metrics(session) -> Dict[str, int]:
    metrics = getattr(session, "_hermes_amazon_cycle_metrics", None)
    if not isinstance(metrics, dict):
        metrics = {}
        try:
            setattr(session, "_hermes_amazon_cycle_metrics", metrics)
        except (AttributeError, TypeError):
            pass
    return metrics


def _increment_amazon_metric(session, key: str) -> None:
    metrics = _amazon_cycle_metrics(session)
    metrics[key] = int(metrics.get(key, 0)) + 1


def _amazon_timing_url(url: str) -> str:
    parsed = urlsplit(str(url or ""))
    return f"{parsed.netloc}{parsed.path}"[:120]


def _get_amazon_response(session, candidate: str, timeout: int, expect_search: bool):
    cache = _amazon_response_cache(session)
    cache_key = _amazon_cache_key(candidate, expect_search)
    cached_response = cache.get(cache_key)
    if cached_response is not None:
        _increment_amazon_metric(session, "response_cache_hits")
        return cached_response

    response = _timed_amazon_network_call(
        session,
        "requests",
        candidate,
        expect_search,
        lambda: _amazon_client(session).requests_session.get(
            candidate,
            headers=amazon_headers(candidate),
            timeout=timeout,
            allow_redirects=True,
        ),
    )
    response.raise_for_status()
    if not _is_usable_amazon_response(response, expect_search):
        raise HermesError("Amazon beklenen arama/urun sayfasi yerine bos veya farkli bir sayfa dondurdu.")
    cache[cache_key] = response
    return response


def _get_amazon_response_with_curl(session: requests.Session, candidate: str, timeout: int, expect_search: bool):
    if curl_requests is None:
        raise HermesError("Amazon icin tarayici-benzeri alternatif istek kullanilamiyor.")
    cache = _amazon_response_cache(session)
    cache_key = _amazon_cache_key(candidate, expect_search)
    cached_response = cache.get(cache_key)
    if cached_response is not None:
        _increment_amazon_metric(session, "response_cache_hits")
        return cached_response

    client = _amazon_client(session)
    if client.curl_session is None:
        client.curl_session = curl_requests.Session()
        _seed_amazon_session(client.curl_session)
    response = _timed_amazon_network_call(
        session,
        "curl_chrome",
        candidate,
        expect_search,
        lambda: client.curl_session.get(
            candidate,
            headers=amazon_headers(candidate),
            timeout=timeout,
            allow_redirects=True,
            impersonate="chrome124",
        ),
    )
    response.raise_for_status()
    if not _is_usable_amazon_response(response, expect_search):
        raise HermesError("Amazon alternatif istekte de bos veya farkli bir sayfa dondurdu.")
    cache[cache_key] = response
    return response


def _chromium_binary(site_name: str = "Hermes") -> str:
    configured = os.getenv("HERMES_CHROMIUM_PATH", "").strip()
    if configured:
        return configured
    for candidate in ("/usr/lib/chromium/chromium", "/usr/lib/chromium-browser/chromium-browser"):
        if os.path.exists(candidate) and os.access(candidate, os.X_OK):
            return candidate
    for candidate in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        found = shutil.which(candidate)
        if found:
            return found
    raise HermesError(f"{site_name} icin gercek tarayici modu kullanilamiyor; Chromium bulunamadi.")


def _start_amazon_browser(client: AmazonClient):
    """Use the installed matching Chromium/driver, with its natural browser identity."""
    if webdriver is None or ChromeService is None:
        raise HermesError("Amazon gerçek tarayıcı desteği kurulmamış.")
    driver_binary = shutil.which("chromedriver")
    if not driver_binary:
        raise HermesError("Amazon gerçek tarayıcı sürücüsü bulunamadı.")
    if client.browser_profile is None:
        client.browser_profile = tempfile.TemporaryDirectory(prefix="hermes-amazon-browser-")
    options = webdriver.ChromeOptions()
    options.binary_location = _chromium_binary()
    options.page_load_strategy = "normal" if client.browser_policy == "full_cold" else "eager"
    for argument in (
        "--headless=new", "--no-sandbox", "--disable-dev-shm-usage",
        "--disable-background-networking", "--no-first-run", "--lang=tr-TR",
        "--window-size=1365,900", "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={client.browser_profile.name}",
    ):
        options.add_argument(argument)
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    driver = webdriver.Chrome(service=ChromeService(executable_path=driver_binary), options=options)
    try:
        driver.execute_cdp_cmd("Network.enable", {})
        driver.execute_cdp_cmd("Network.setBypassServiceWorker", {"bypass": True})
        driver.execute_cdp_cmd("Network.setCacheDisabled", {"cacheDisabled": client.browser_policy != "ready_cached"})
        if client.browser_policy == "ready_cached":
            _configure_amazon_document_revalidation(driver, client)
    except Exception:
        driver.quit()
        raise
    return driver


def _configure_amazon_document_revalidation(driver, client):
    """Revalidate documents only; static assets keep Chromium's ordinary cache."""
    devtools, connection = driver.start_devtools()

    def continue_document(event):
        headers = [devtools.fetch.HeaderEntry(str(name), str(value)) for name, value in event.request.headers.items()
                   if name.casefold() not in {"cache-control", "pragma"}]
        headers.extend([devtools.fetch.HeaderEntry("Cache-Control", "no-cache"),
                        devtools.fetch.HeaderEntry("Pragma", "no-cache")])
        try:
            connection.execute(devtools.fetch.continue_request(event.request_id, headers=headers))
        except Exception as exc:
            # Do not publish an unvalidated document or retry its Amazon URL.
            client.browser_header_error = type(exc).__name__
            log(f"Amazon tarayıcı belge doğrulaması başarısız: {type(exc).__name__}")

    connection.add_callback(devtools.fetch.RequestPaused, continue_document)
    connection.execute(devtools.fetch.enable(patterns=[devtools.fetch.RequestPattern(
        resource_type=devtools.network.ResourceType.DOCUMENT, request_stage=devtools.fetch.RequestStage.REQUEST,
    )]))


def _browser_document_response(driver, previous=None):
    """Validate this navigation from existing events, reusing its frame identity only."""
    messages = []
    for entry in driver.get_log("performance"):
        try:
            message = json.loads(entry["message"])["message"]
            if isinstance(message, dict) and isinstance(message.get("params", {}), dict):
                messages.append(message)
        except (KeyError, TypeError, ValueError):
            continue
    frame_id = previous.get("_frame_id") if previous else None
    loader_id = previous.get("_loader_id") if previous else None
    for message in messages:
        frame = message.get("params", {}).get("frame", {})
        if message.get("method") == "Page.frameNavigated" and frame.get("id") and not frame.get("parentId"):
            frame_id, loader_id = frame["id"], frame.get("loaderId")
    if not frame_id:
        # Missing frame events are uncommon; keep a conservative fallback.
        frame = driver.execute_cdp_cmd("Page.getFrameTree", {})["frameTree"]["frame"]
        frame_id, loader_id = frame["id"], frame.get("loaderId")
    same_document = (previous and previous.get("_frame_id") == frame_id
                     and previous.get("_loader_id") == loader_id)
    document = previous if same_document else None
    for message in messages:
        params = message.get("params", {})
        if params.get("type") != "Document" or params.get("frameId") != frame_id:
            continue
        if message.get("method") not in {"Network.requestWillBeSent", "Network.responseReceived"}:
            continue
        if loader_id and params.get("loaderId") != loader_id:
            # A later navigation has started but its committed main document
            # is not proven yet. Never pair an earlier 200 with the newer DOM.
            document = None
            continue
        if message.get("method") == "Network.responseReceived":
            try:
                document = {**params["response"], "status": int(params["response"]["status"]),
                            "_frame_id": frame_id, "_loader_id": params.get("loaderId")}
            except (KeyError, TypeError, ValueError):
                document = None
    return document


def _timed_browser_operation(timings, phase, operation):
    """Measure existing work, including failures; never perform an additional read."""
    started = time.monotonic()
    try:
        return operation()
    finally:
        timings[phase] = timings.get(phase, 0.0) + time.monotonic() - started


def _browser_page_source(driver, timings):
    timings["html_reads"] = timings.get("html_reads", 0) + 1
    return _timed_browser_operation(timings, "html", lambda: driver.page_source)


def _read_amazon_browser_html(driver, client, candidate, expect_search, deadline, timings=None):
    """Use a stable selected-product DOM, audit against complete DOM on the same navigation."""
    timings = {} if timings is None else timings
    key = (expect_search, candidate)
    record = client.browser_coverage.setdefault(key, {"reads": 0, "full_only": False, "minimum_seconds": 0, "validated": False})
    if len(client.browser_coverage) > 256:
        client.browser_coverage.pop(next(iter(client.browser_coverage)))
    record["reads"] += 1
    if client.browser_policy == "full_cold":
        return _browser_page_source(driver, timings), "tam"
    read_started = time.monotonic()
    previous = None
    stable_since = time.monotonic()
    early_html = None
    complete = False
    while time.monotonic() < deadline:
        observation = _timed_browser_operation(timings, "ready_script", lambda: driver.execute_script(
            amazon_provider.BROWSER_READY_SCRIPT, expect_search, amazon_provider.AMAZON_PRODUCT_SELECTORS))
        timings["observations"] = timings.get("observations", 0) + 1
        complete = observation["state"] == "complete"
        signature = observation["signature"]
        if signature != previous:
            timings["signature_changes"] = timings.get("signature_changes", 0) + 1
            previous, stable_since = signature, time.monotonic()
        elif (time.monotonic() - stable_since >= AMAZON_BROWSER_SETTLE_SECONDS
              and (observation["ready"] or complete)):
            if record["full_only"]:
                if complete and time.monotonic() - read_started >= record["minimum_seconds"]:
                    break
            elif not observation["ready"]:
                if complete:
                    break
            else:
                early_html = _browser_page_source(driver, timings)
                break
        _timed_browser_operation(timings, "ready_wait", lambda: time.sleep(0.2))
    else:
        raise HermesError("Amazon tarayıcıda ürün/arama verisinin hazır olması zaman aşımına uğradı.")
    audit = early_html is not None and (not record["validated"] or record["reads"] % AMAZON_BROWSER_AUDIT_INTERVAL == 0)
    if not audit:
        return early_html if early_html is not None else _browser_page_source(driver, timings), "erken" if early_html is not None else "tam"
    early_snapshot = _timed_browser_operation(timings, "coverage_parse", lambda:
        amazon_provider.browser_coverage_snapshot(early_html, candidate, expect_search))
    # An initial observation must not approve a page merely because cached
    # images made load finish before its late purchase/Twister updates.
    reference_after = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        complete_state = _timed_browser_operation(timings, "audit_wait", lambda: driver.execute_script("return document.readyState"))
        if complete_state == "complete" and time.monotonic() >= reference_after:
            break
        _timed_browser_operation(timings, "audit_wait", lambda: time.sleep(0.2))
    else:
        raise HermesError("Amazon tarayıcı kapsam kontrolünde tam yükleme zaman aşımına uğradı.")
    full_html = _browser_page_source(driver, timings)
    full_snapshot = _timed_browser_operation(timings, "coverage_parse", lambda:
        amazon_provider.browser_coverage_snapshot(full_html, candidate, expect_search))
    changed = sorted(name for name in set(early_snapshot) | set(full_snapshot)
                     if early_snapshot.get(name) != full_snapshot.get(name))
    record["full_only"] = bool(changed)
    record["validated"] = not changed
    if changed:
        record["minimum_seconds"] = time.monotonic() - read_started
    client.browser_audit_total += 1
    client.browser_audits.append({"sequence": client.browser_audit_total, "url": candidate, "at": utc_now(), "changed": changed,
                                 "early": early_snapshot, "full": full_snapshot})
    log(f"Amazon tarayıcı kapsam kontrolü: sonuç={'tam_yükleme_gerekli' if changed else 'eşleşti'} | "
        f"fark={','.join(changed) or 'yok'} | adres={_amazon_timing_url(candidate)}")
    # Audit reads always return the complete DOM. A discrepancy locks this URL to full reads.
    return full_html, "kapsam_farkı" if changed else "kapsam_doğrulandı"


def _get_amazon_response_with_browser(session: requests.Session, candidate: str, timeout: int, expect_search: bool):
    cache = _amazon_response_cache(session)
    cache_key = _amazon_cache_key(f"browser:{candidate}", expect_search)
    if cache_key in cache:
        _increment_amazon_metric(session, "response_cache_hits")
        return cache[cache_key]
    client = _amazon_client(session)
    if client.browser_driver is None:
        try:
            client.browser_driver = _start_amazon_browser(client)
            client.browser_timeout = None
        except Exception as exc:
            raise HermesError(f"Amazon gerçek tarayıcı oturumu başlatılamadı: {exc}") from exc
    driver = client.browser_driver
    effective_timeout = max(AMAZON_BROWSER_MIN_TIMEOUT_SECONDS, int(timeout))
    if client.browser_timeout != effective_timeout:
        driver.set_page_load_timeout(effective_timeout)
        client.browser_timeout = effective_timeout
    driver.get_log("performance")  # Drain the previous page's events.
    _note_amazon_request(session, "browser", candidate)
    started_at = time.monotonic()
    timings = {}
    readiness = "başarısız"
    try:
        _timed_browser_operation(timings, "navigation", lambda: driver.get(candidate))
        document = _timed_browser_operation(timings, "document", lambda: _browser_document_response(driver))
        if not document:
            raise HermesError("Amazon tarayıcıda ana belge ağ yanıtı doğrulanamadı.")
        _HtmlResponse(candidate, "", document["status"]).raise_for_status()
        deadline = started_at + effective_timeout
        html, readiness = _read_amazon_browser_html(driver, client, candidate, expect_search, deadline, timings)
        document = _timed_browser_operation(timings, "document", lambda: _browser_document_response(driver, document))
        if (client.browser_header_error or not document or any(document.get(field) for field in
                ("fromDiskCache", "fromServiceWorker", "fromPrefetchCache"))):
            raise HermesError("Amazon tarayıcıda güncel ana belge ağ üzerinden doğrulanamadı.")
        final_url = driver.current_url
        requested_asin, response_asin = extract_asin_from_url(candidate), extract_asin_from_url(final_url)
        if requested_asin and requested_asin != response_asin:
            raise HermesError("Amazon tarayıcı farklı ürün kimliğine yönlendirildi; fiyat kullanılmadı.")
        response = _HtmlResponse(final_url, html, document["status"])
        log(f"Amazon tarayıcı okuması: politika={client.browser_policy} | veri={readiness} | belge_önbelleği=0")
    except HermesError:
        raise
    except Exception as exc:
        raise HermesError(f"Amazon gerçek tarayıcı sayfası okunamadı ({type(exc).__name__}).") from exc
    finally:
        total = time.monotonic() - started_at
        phases = {key: timings.get(key, 0.0) for key in (
            "navigation", "ready_script", "ready_wait", "html", "document", "coverage_parse", "audit_wait")}
        other = max(0.0, total - sum(phases.values()))
        client.browser_timing_total += 1
        client.browser_timings.append({"sequence": client.browser_timing_total, "at": utc_now(),
            "url": _amazon_timing_url(candidate), "readiness": readiness,
            "phases_ms": {**{key: round(value * 1000) for key, value in phases.items()},
                          "other": round(other * 1000), "total": round(total * 1000)},
            "observations": timings.get("observations", 0), "signature_changes": timings.get("signature_changes", 0),
            "html_reads": timings.get("html_reads", 0)})
        log("Amazon tarayıcı aşama süreleri: "
            f"veri={readiness} | " + " | ".join(f"{key}={round(value * 1000)} ms" for key, value in phases.items()) +
            f" | other={round(other * 1000)} ms | total={round(total * 1000)} ms"
            f" | observations={timings.get('observations', 0)} | signature_changes={timings.get('signature_changes', 0)}"
            f" | html_reads={timings.get('html_reads', 0)} | adres={_amazon_timing_url(candidate)}")
        log(
            "Amazon ağ yanıt süresi: "
            f"taşıma=browser | tip={_amazon_request_type(candidate, expect_search)} | "
            f"süre={round(total * 1000)} ms | "
            f"adres={_amazon_timing_url(candidate)}"
        )
    _log_amazon_response(response, "browser")
    usable = _is_usable_amazon_response(response, expect_search)
    response.raise_for_status()
    if not usable:
        raise HermesError("Amazon gerçek tarayıcıda beklenen ürün/arama sayfası yerine boş veya farklı sayfa döndürdü.")
    cache[cache_key] = response
    return response


def amazon_error_status(exc: Exception) -> Optional[int]:
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    return response_status if isinstance(response_status, int) else None


def _is_hard_amazon_block_error(exc: Exception) -> bool:
    status_code = amazon_error_status(exc)
    if status_code in {429, 503}:
        return True
    message = normalize_offer_text(str(exc))
    return "bot korumasi" in message or "captcha" in message or "robot" in message


def _amazon_request_type(url: str, expect_search: bool) -> str:
    if expect_search:
        return "arama"
    return "ürün" if _is_amazon_product_url(url) else "sayfa"


def _short_amazon_url(url: str) -> str:
    clean = repair_mojibake(str(url or "")).strip()
    return clean[:117] + "..." if len(clean) > 120 else clean


def _amazon_error_reason(exc: Exception) -> str:
    status_code = amazon_error_status(exc)
    message = normalize_offer_text(str(exc))
    if status_code:
        return f"http_{status_code}"
    if "captcha" in message or "robot" in message or "bot korumasi" in message:
        return "bot_korumasi"
    if "bos veya farkli bir sayfa" in message:
        return "beklenmeyen_sayfa"
    if "arama/urun sayfasi yerine" in message:
        return "beklenmeyen_sayfa"
    return type(exc).__name__


def _diagnostic_snippet(value: str) -> str:
    text = normalize_offer_text(repair_mojibake(str(value or "")))
    return text[:AMAZON_DIAGNOSTIC_SNIPPET_LENGTH] if text else "-"


def _record_amazon_attempt(
    attempts: List[Dict[str, Any]],
    method: str,
    candidate: str,
    expect_search: bool,
    exc: Exception,
) -> None:
    attempts.append(
        {
            "method": method,
            "type": _amazon_request_type(candidate, expect_search),
            "status": amazon_error_status(exc),
            "reason": _amazon_error_reason(exc),
            "url": candidate,
        }
    )


def _log_amazon_diagnostics(original_url: str, expect_search: bool, attempts: List[Dict[str, Any]]) -> None:
    if not attempts:
        return
    last = attempts[-1]
    flow = " > ".join(
        f"{attempt['method']}:{attempt.get('status') or attempt['reason']}" for attempt in attempts[-6:]
    )
    log(
        "Amazon teşhis: "
        f"tip={_amazon_request_type(original_url, expect_search)} | "
        f"deneme={len(attempts)} | "
        f"son_yontem={last['method']} | "
        f"son_status={last.get('status') or '-'} | "
        f"son_sebep={last['reason']} | "
        f"akis={flow} | "
        f"url={_short_amazon_url(str(last['url']))}"
    )


def _amazon_cache_key(url: str, expect_search: bool) -> Tuple[bool, str]:
    return expect_search, str(url or "").strip()


def _amazon_response_cache(session: requests.Session) -> Dict[Tuple[bool, str], requests.Response]:
    cache = getattr(session, "_hermes_amazon_response_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        setattr(session, "_hermes_amazon_response_cache", cache)
    return cache


def _seed_amazon_session(session: requests.Session) -> None:
    if getattr(session, "_hermes_amazon_seeded", False):
        return
    session.cookies.set("i18n-prefs", "TRY", domain=".amazon.com.tr")
    session.cookies.set("lc-acbtr", "tr_TR", domain=".amazon.com.tr")
    setattr(session, "_hermes_amazon_seeded", True)


def _log_amazon_block(session, exc: Exception) -> None:
    client = _amazon_client(session)
    client.block_count += 1
    now = time.monotonic()
    recent = sum(at > now - 60 for at in client.attempt_times)
    client.block_events.append({
        "at": utc_now(),
        "first_block": client.block_count == 1,
        "block_count": client.block_count,
        "reason": _amazon_error_reason(exc),
        "challenge": getattr(exc, "amazon_challenge_reason", ""),
        "http_status": getattr(exc, "amazon_http_status", None) or amazon_error_status(exc),
        "transport": client.last_transport,
        "address": client.last_address,
        "session_age_seconds": round(now - client.started_at),
        "session_attempts": client.total_attempts,
        "attempts_last_60_seconds": recent,
        "attempts_since_previous_block": client.attempts_since_block,
    })
    log(
        "Amazon engel ölçümü: "
        f"ilk_engel={int(client.block_count == 1)} | engel_sayısı={client.block_count} | "
        f"sebep={_amazon_error_reason(exc)} | oturum_s={round(now - client.started_at)} | "
        f"oturum_deneme={client.total_attempts} | son_60sn_deneme={recent} | "
        f"önceki_engelden_sonra_deneme={client.attempts_since_block}"
    )
    client.attempts_since_block = 0


def fetch_amazon_page(session: requests.Session, url: str, timeout: int, expect_search: bool = False):
    """One canonical request; at most one browser fallback for non-protection failures.

    A challenge or HTTP 429/503 is terminal. Never reset cookies or multiply
    requests through URL/transport variants after the server rejects a read.
    """
    _increment_amazon_metric(session, "page_fetch_calls")
    candidate = amazon_url_variants(url)[0]
    if _amazon_client(session).transport == "browser":
        # Explicit Pi comparison mode: one browser navigation, no HTTP rescue chain.
        try:
            return _get_amazon_response_with_browser(session, candidate, timeout, expect_search)
        except Exception as exc:
            if _is_hard_amazon_block_error(exc):
                _log_amazon_block(session, exc)
            raise
    primary = _get_amazon_response_with_curl if curl_requests is not None else _get_amazon_response
    method = "curl" if curl_requests is not None else "requests"
    attempts: List[Dict[str, Any]] = []
    try:
        return primary(session, candidate, timeout, expect_search)
    except Exception as exc:  # noqa: BLE001
        _record_amazon_attempt(attempts, method, candidate, expect_search, exc)
        if _is_hard_amazon_block_error(exc):
            _log_amazon_block(session, exc)
            _log_amazon_diagnostics(url, expect_search, attempts)
            raise
    try:
        response = _get_amazon_response_with_browser(session, candidate, timeout, expect_search)
        _amazon_response_cache(session)[_amazon_cache_key(candidate, expect_search)] = response
        return response
    except Exception as exc:  # noqa: BLE001
        _record_amazon_attempt(attempts, "browser", candidate, expect_search, exc)
        if _is_hard_amazon_block_error(exc):
            _log_amazon_block(session, exc)
        _log_amazon_diagnostics(url, expect_search, attempts)
        raise


def hepsiburada_headers(url: str):
    headers = build_headers(url)
    headers.update(
        {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Referer": "https://www.hepsiburada.com/",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
        }
    )
    return headers


def _add_query(url: str, query: str) -> str:
    parsed = urlsplit(url)
    existing = parsed.query.strip("&")
    new_query = "&".join(part for part in (existing, query) if part)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment))


def _is_hepsiburada_product_url(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return "-p-" in path or "-pm-" in path


def _is_hepsiburada_search_url(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.path.rstrip("/").lower() == "/ara" or "q=" in parsed.query.lower()


def _clean_hepsiburada_search_url(url: str) -> str:
    if not _is_hepsiburada_search_url(url):
        return url
    parsed = urlsplit(url)
    kept_params = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key == "q"]
    if not kept_params:
        return url
    return urlunsplit((parsed.scheme, parsed.netloc, "/ara", urlencode(kept_params), ""))


def hepsiburada_url_variants(url: str):
    variants = []

    def add(candidate: str) -> None:
        if candidate and candidate not in variants:
            variants.append(candidate)

    add(url)
    if _is_hepsiburada_search_url(url):
        add(_clean_hepsiburada_search_url(url))
        return variants

    clean_url = url.split("?", 1)[0]
    if clean_url != url:
        add(clean_url)
    if _is_hepsiburada_product_url(clean_url):
        add(_add_query(clean_url, "magaza=Hepsiburada"))
    if "-pm-" in clean_url:
        add(clean_url.replace("-pm-", "-p-", 1))
    if "-p-" in clean_url:
        add(clean_url.replace("-p-", "-pm-", 1))
    return variants


def _is_usable_hepsiburada_response(response) -> bool:
    final_url = getattr(response, "url", "") or ""
    text = decode_response_text(response)
    lowered = text.lower()
    if _is_hepsiburada_search_url(final_url):
        return "hepsiburada" in lowered and ("ara" in lowered or "ürün" in lowered or "urun" in lowered)
    if not _is_hepsiburada_product_url(final_url):
        return False
    return "sepete ekle" in lowered or "satıcı" in lowered or "satici" in lowered or "stok kodu" in lowered


def _get_hepsiburada_response(session, candidate: str, timeout: int):
    response = session.get(
        candidate,
        headers=hepsiburada_headers(candidate),
        timeout=timeout,
        allow_redirects=True,
    )
    if response.status_code == 403:
        raise HttpStatusHermesError(403, candidate)
    response.raise_for_status()
    if not _is_usable_hepsiburada_response(response):
        raise HermesError("Hepsiburada linki beklenen ürün veya arama sayfası yerine farklı bir sayfaya yönlendi.")
    return response


def _record_hepsiburada_attempt(attempts: List[str], method: str, candidate: str, exc: Exception) -> None:
    reason = getattr(exc, "status_code", None) or exc.__class__.__name__
    attempts.append(f"{method}:{reason}:{candidate[:100]}")


def _log_hepsiburada_diagnostics(url: str, attempts: List[str]) -> None:
    if attempts:
        log(f"Hepsiburada teşhis: deneme={len(attempts)} | akis={' > '.join(attempts[-6:])} | url={url}")


def fetch_hepsiburada_page(session: requests.Session, url: str, timeout: int) -> requests.Response:
    try:
        session.get(
            "https://www.hepsiburada.com/",
            headers=hepsiburada_headers("https://www.hepsiburada.com/"),
            timeout=timeout,
            allow_redirects=True,
        )
    except Exception:
        pass

    last_error: Optional[Exception] = None
    attempts: List[str] = []
    variants = hepsiburada_url_variants(url)
    for candidate in hepsiburada_url_variants(url):
        try:
            return _get_hepsiburada_response(session, candidate, timeout)
        except Exception as exc:
            last_error = exc
            _record_hepsiburada_attempt(attempts, "requests", candidate, exc)

    fresh_session = requests.Session()
    for candidate in variants:
        try:
            return _get_hepsiburada_response(fresh_session, candidate, timeout)
        except Exception as exc:
            last_error = exc
            _record_hepsiburada_attempt(attempts, "requests_fresh", candidate, exc)

    if curl_requests is not None:
        try:
            curl_session = curl_requests.Session()
            for candidate in variants:
                try:
                    response = curl_session.get(
                        candidate,
                        headers=hepsiburada_headers(candidate),
                        timeout=timeout,
                        allow_redirects=True,
                        impersonate="chrome124",
                    )
                    if response.status_code == 403:
                        raise HttpStatusHermesError(403, candidate)
                    response.raise_for_status()
                    if not _is_usable_hepsiburada_response(response):
                        raise HermesError("Hepsiburada linki beklenen ürün veya arama sayfası yerine farklı bir sayfaya yönlendi.")
                    return response
                except Exception as exc:
                    last_error = exc
                    _record_hepsiburada_attempt(attempts, "curl", candidate, exc)
        except Exception as exc:
            last_error = exc
            _record_hepsiburada_attempt(attempts, "curl_setup", url, exc)

    if last_error:
        _log_hepsiburada_diagnostics(url, attempts)
        raise last_error
    _log_hepsiburada_diagnostics(url, attempts)
    raise HttpStatusHermesError(0, url)


def beymenclub_headers(url: str) -> Dict[str, str]:
    """Return the browser-shaped headers required by Beymen Club's WAF."""
    return {
        "User-Agent": AMAZON_CHROME_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "max-age=0",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Linux"',
        "Referer": referer_for_url(url),
    }


def _is_usable_beymenclub_response(response) -> bool:
    text = decode_response_text(response)
    return "BEYMEN.productMain" in text or "m-priceWrapper" in text or "o-productDetail" in text


def _get_beymenclub_response(session, url: str, timeout: int):
    response = session.get(
        url,
        headers=beymenclub_headers(url),
        timeout=timeout,
        allow_redirects=True,
    )
    if response.status_code == 403:
        raise HttpStatusHermesError(403, url)
    response.raise_for_status()
    if not _is_usable_beymenclub_response(response):
        raise HermesError("Beymen Club ürün sayfası beklenen fiyat verisini içermiyor.")
    return response


def fetch_beymenclub_page(session: requests.Session, url: str, timeout: int) -> requests.Response:
    """Fetch Beymen Club through its WAF-friendly, site-specific request path."""
    last_error: Optional[Exception] = None
    attempts: List[str] = []

    for method, client in (("requests", session), ("requests_fresh", requests.Session())):
        try:
            return _get_beymenclub_response(client, url, timeout)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            status = getattr(exc, "status_code", None) or exc.__class__.__name__
            attempts.append(f"{method}:{status}")

    if curl_requests is not None:
        try:
            curl_session = curl_requests.Session()
            response = curl_session.get(
                url,
                headers=beymenclub_headers(url),
                timeout=timeout,
                allow_redirects=True,
                impersonate="chrome124",
            )
            if response.status_code == 403:
                raise HttpStatusHermesError(403, url)
            response.raise_for_status()
            if not _is_usable_beymenclub_response(response):
                raise HermesError("Beymen Club ürün sayfası beklenen fiyat verisini içermiyor.")
            return response
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            status = getattr(exc, "status_code", None) or exc.__class__.__name__
            attempts.append(f"curl:{status}")

    if attempts:
        log(f"Beymen Club teşhis: deneme={len(attempts)} | akis={' > '.join(attempts)} | url={url}")
    if last_error is not None:
        raise last_error
    raise HttpStatusHermesError(0, url)


def bengurme_headers(url: str) -> Dict[str, str]:
    """Return headers for Ben Gurme's public Shopify product endpoints."""
    headers = build_headers(url)
    headers.update(
        {
            "User-Agent": AMAZON_CHROME_USER_AGENT,
            "Accept": "application/json,text/javascript,*/*;q=0.8",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": referer_for_url(url),
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    return headers


def bengurme_product_json_url(url: str) -> str:
    """Build Shopify's public product JSON endpoint from a Ben Gurme link."""
    parsed = urlsplit(url)
    match = re.search(r"/products/([^/?#]+)", parsed.path, re.IGNORECASE)
    if not match:
        raise HermesError("Ben Gurme linkinden ürün kimliği okunamadı.")
    path = f"/products/{match.group(1)}.js"
    return urlunsplit((parsed.scheme or "https", parsed.netloc, path, "", ""))


def _is_usable_bengurme_response(response) -> bool:
    text = decode_response_text(response).lstrip()
    normalized = normalize_offer_text(text)
    return (
        (text.startswith("{") and '"variants"' in text)
        or "shopify" in normalized
        or "product" in normalized and "variants" in normalized
        or "sepete ekle" in normalized
        or "tukendi" in normalized
    )


def _get_bengurme_response(session, url: str, timeout: int):
    response = session.get(
        url,
        headers=bengurme_headers(url),
        timeout=timeout,
        allow_redirects=True,
    )
    if response.status_code in {403, 429}:
        raise HttpStatusHermesError(response.status_code, url)
    response.raise_for_status()
    if not _is_usable_bengurme_response(response):
        raise HermesError("Ben Gurme ürün sayfası beklenen stok veya fiyat verisini içermiyor.")
    return response


def fetch_bengurme_page(session: requests.Session, url: str, timeout: int) -> requests.Response:
    """Read Ben Gurme from Shopify JSON first, with the product page as fallback.

    The JSON endpoint contains every variant's live availability and price, so it
    is both faster and less brittle than reading storefront button text.
    """
    cache = getattr(session, "_hermes_bengurme_response_cache", None)
    if cache is None:
        cache = {}
        setattr(session, "_hermes_bengurme_response_cache", cache)
    if url in cache:
        return cache[url]

    attempts: List[str] = []
    last_error: Optional[Exception] = None
    candidates = (bengurme_product_json_url(url), url)
    for label, client in (("requests", session), ("requests_fresh", requests.Session())):
        for candidate_url in candidates:
            try:
                response = _get_bengurme_response(client, candidate_url, timeout)
                cache[url] = response
                return response
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                status = getattr(exc, "status_code", None) or exc.__class__.__name__
                attempts.append(f"{label}:{status}")

    if curl_requests is not None:
        for candidate_url in candidates:
            try:
                response = curl_requests.get(
                    candidate_url,
                    headers=bengurme_headers(candidate_url),
                    timeout=timeout,
                    allow_redirects=True,
                    impersonate="chrome124",
                )
                if response.status_code in {403, 429}:
                    raise HttpStatusHermesError(response.status_code, candidate_url)
                response.raise_for_status()
                if not _is_usable_bengurme_response(response):
                    raise HermesError("Ben Gurme ürün verisi okunamadı.")
                cache[url] = response
                return response
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                status = getattr(exc, "status_code", None) or exc.__class__.__name__
                attempts.append(f"curl:{status}")

    if attempts:
        log(f"Ben Gurme teşhis: deneme={len(attempts)} | akis={' > '.join(attempts)} | url={url}")
    if last_error is not None:
        raise last_error
    raise HttpStatusHermesError(0, url)


def _beymenclub_summary_headers(product_url: str) -> Dict[str, str]:
    parsed = urlsplit(product_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    headers = beymenclub_headers(product_url)
    headers.update(
        {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": origin,
            "Referer": product_url,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    return headers


def _get_beymenclub_size_summary(session, product_url: str, product_id: int, timeout: int) -> Dict[str, Any]:
    parsed = urlsplit(product_url)
    summary_url = f"{parsed.scheme}://{parsed.netloc}/sf-api/api/product/{product_id}/productsummary"
    response = session.post(
        summary_url,
        headers=_beymenclub_summary_headers(product_url),
        timeout=timeout,
        allow_redirects=True,
    )
    if response.status_code == 403:
        raise HttpStatusHermesError(403, summary_url)
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise HermesError("Beymen Club beden stok verisi okunamadı.") from exc
    if not isinstance(payload, dict):
        raise HermesError("Beymen Club beden stok verisi beklenen biçimde dönmedi.")
    return payload


def fetch_beymenclub_size_summary(
    session: requests.Session, product_url: str, product_id: int, timeout: int
) -> Dict[str, Any]:
    """Read the protected size API using the same browser identity as the product page."""
    attempts: List[str] = []
    last_error: Optional[Exception] = None

    try:
        return _get_beymenclub_size_summary(session, product_url, product_id, timeout)
    except Exception as exc:  # noqa: BLE001
        last_error = exc
        status = getattr(exc, "status_code", None) or exc.__class__.__name__
        attempts.append(f"requests:{status}")

    fresh_session = requests.Session()
    try:
        _get_beymenclub_response(fresh_session, product_url, timeout)
        return _get_beymenclub_size_summary(fresh_session, product_url, product_id, timeout)
    except Exception as exc:  # noqa: BLE001
        last_error = exc
        status = getattr(exc, "status_code", None) or exc.__class__.__name__
        attempts.append(f"requests_fresh:{status}")

    if curl_requests is not None:
        try:
            curl_session = curl_requests.Session()
            response = curl_session.get(
                product_url,
                headers=beymenclub_headers(product_url),
                timeout=timeout,
                allow_redirects=True,
                impersonate="chrome124",
            )
            if response.status_code == 403:
                raise HttpStatusHermesError(403, product_url)
            response.raise_for_status()
            if not _is_usable_beymenclub_response(response):
                raise HermesError("Beymen Club ürün sayfası beklenen fiyat verisini içermiyor.")
            return _get_beymenclub_size_summary(curl_session, product_url, product_id, timeout)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            status = getattr(exc, "status_code", None) or exc.__class__.__name__
            attempts.append(f"curl:{status}")

    if attempts:
        log(f"Beymen Club beden teşhis: deneme={len(attempts)} | akis={' > '.join(attempts)} | url={product_url}")
    if isinstance(last_error, HttpStatusHermesError):
        raise HermesError("Beymen Club beden stok verisine erişim reddedildi; sonraki turda yeniden denenecek.")
    if last_error is not None:
        raise HermesError(f"Beymen Club beden stok verisi okunamadı: {last_error}") from last_error
    raise HermesError("Beymen Club beden stok verisi okunamadı.")


def _is_zara_interstitial(html: str) -> bool:
    normalized = normalize_offer_text(html)
    return "bm-verify" in normalized and "_sec/verify" in normalized


def _zara_origin(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else "https://www.zara.com"


def zara_headers(url: str) -> Dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Connection": "keep-alive",
        "Referer": referer_for_url(url),
        "Upgrade-Insecure-Requests": "1",
    }


def _zara_verification_payload(html: str) -> Dict[str, Any]:
    token_match = re.search(r'["\']bm-verify["\']\s*:\s*["\']([^"\']+)["\']', html)
    number_match = re.search(r'Number\(\s*["\'](\d+)["\']\s*\+\s*["\'](\d+)["\']\s*\)', html)
    base_match = re.search(r"var\s+i\s*=\s*(\d+)", html)
    if not token_match or not number_match or not base_match:
        raise HermesError("Zara doğrulama sayfası çözümlenemedi.")
    return {
        "bm-verify": token_match.group(1),
        "pow": int(base_match.group(1)) + int(number_match.group(1) + number_match.group(2)),
    }


def _is_usable_zara_response(html: str) -> bool:
    normalized = normalize_offer_text(html)
    return any(
        marker in normalized
        for marker in (
            "application/ld+json",
            "product-detail-info",
            "product-detail-size-selector",
            "hasvariant",
            "price__amount",
        )
    )


def _get_zara_response(session: requests.Session, url: str, timeout: int) -> requests.Response:
    response = session.get(url, headers=zara_headers(url), timeout=timeout, allow_redirects=True)
    response.raise_for_status()
    return response


def fetch_zara_page(session: requests.Session, url: str, timeout: int) -> requests.Response:
    response = _get_zara_response(session, url, timeout)
    html = decode_response_text(response)
    if not _is_zara_interstitial(html):
        if _is_usable_zara_response(html):
            return response
        fresh_session = requests.Session()
        fresh_response = _get_zara_response(fresh_session, url, timeout)
        fresh_html = decode_response_text(fresh_response)
        if _is_zara_interstitial(fresh_html):
            session = fresh_session
            response = fresh_response
            html = fresh_html
        elif _is_usable_zara_response(fresh_html):
            return fresh_response
        else:
            raise HermesError("Zara ürün verisi eksik döndü; sayfa fiyat/beden bilgisi içermiyor.")

    if not _is_zara_interstitial(html):
        return response

    payload = _zara_verification_payload(html)
    verify_url = f"{_zara_origin(url)}/_sec/verify?provider=interstitial"
    headers = zara_headers(url)
    headers.update({"Content-Type": "application/json", "Origin": _zara_origin(url), "Referer": url})
    verify_response = session.post(
        verify_url,
        data=json.dumps(payload),
        headers=headers,
        timeout=timeout,
        allow_redirects=True,
    )
    verify_response.raise_for_status()
    response = _get_zara_response(session, url, timeout)
    verified_html = decode_response_text(response)
    if _is_zara_interstitial(verified_html):
        raise HermesError("Zara bot korumasi nedeniyle doğrulama sayfasi dondu.")
    if not _is_usable_zara_response(verified_html):
        raise HermesError("Zara doğrulama sonrası ürün verisi eksik döndü.")
    return response


def hm_headers(url: str) -> Dict[str, str]:
    headers = build_headers(url)
    headers.update(
        {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Origin": "https://www2.hm.com",
            "Referer": url,
            "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
        }
    )
    return headers


def _hm_article_id_from_url(url: str) -> str:
    match = re.search(r"productpage\.(\d+)\.html", url)
    if match:
        return match.group(1)
    raise HermesError("H&M linkinden ürün kodu okunamadı.")


def _hm_api_url(article_ids: List[str]) -> str:
    unique_ids = []
    seen = set()
    for article_id in article_ids:
        if article_id and article_id not in seen:
            unique_ids.append(article_id)
            seen.add(article_id)
    query = urlencode({"ids": "|".join(unique_ids), "touchPoint": "DESKTOP"})
    return f"{HM_API_BASE_URL}?{query}"


def _hm_api_get(session: requests.Session, api_url: str, source_url: str, timeout: int) -> Dict[str, Any]:
    headers = hm_headers(source_url)
    if curl_requests is not None:
        response = curl_requests.get(
            api_url,
            headers=headers,
            timeout=timeout,
            impersonate="chrome124",
        )
    else:
        response = session.get(api_url, headers=headers, timeout=timeout)
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        raise HermesError("H&M API ürün verisini JSON olarak döndürmedi.") from exc
    if not isinstance(data, dict):
        raise HermesError("H&M API beklenmeyen ürün verisi döndürdü.")
    return data


def _hm_products_from_api_data(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    articles = data.get("articles")
    if not isinstance(articles, dict):
        return []
    product_list = articles.get("productList")
    if not isinstance(product_list, list):
        return []
    return [item for item in product_list if isinstance(item, dict)]


def _hm_swatch_article_ids(products: List[Dict[str, Any]]) -> List[str]:
    article_ids: List[str] = []
    for product in products:
        for swatch in product.get("swatches") or []:
            if not isinstance(swatch, dict):
                continue
            article_id = str(swatch.get("articleId") or "").strip()
            if article_id:
                article_ids.append(article_id)
    return article_ids


def fetch_hm_page(session: requests.Session, url: str, timeout: int) -> requests.Response:
    cache = getattr(session, "_hermes_hm_api_cache", None)
    if cache is None:
        cache = {}
        setattr(session, "_hermes_hm_api_cache", cache)
    if url in cache:
        return cache[url]

    article_id = _hm_article_id_from_url(url)
    data = _hm_api_get(session, _hm_api_url([article_id]), url, timeout)
    products = _hm_products_from_api_data(data)
    if not products:
        raise HermesError("H&M API ürün verisi döndürmedi.")

    swatch_ids = _hm_swatch_article_ids(products)
    if swatch_ids:
        data = _hm_api_get(session, _hm_api_url([article_id, *swatch_ids]), url, timeout)
        products = _hm_products_from_api_data(data)
        if not products:
            raise HermesError("H&M API varyasyon verisi döndürmedi.")

    html = (
        '<html><body><script type="application/json" id="hm-product-data">'
        f"{json.dumps({'products': products}, ensure_ascii=False)}"
        "</script></body></html>"
    )
    response = _HtmlResponse(url, html)
    cache[url] = response
    return response


def cleaned_html(response: requests.Response) -> str:
    return repair_mojibake(decode_response_text(response))
