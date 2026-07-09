import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from .constants import RETRY_DELAYS_SECONDS, RETRY_STATUS_CODES
from .errors import HermesError, HttpStatusHermesError
from .logging_utils import log
from .utils import build_headers, canonical_amazon_product_url, normalize_offer_text, repair_mojibake, referer_for_url

try:
    from curl_cffi import requests as curl_requests
except Exception:
    curl_requests = None

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

AMAZON_HARD_BLOCK_RESCUE_VARIANT_LIMIT = 3
AMAZON_BROWSER_RESCUE_VARIANT_LIMIT = 2
AMAZON_BROWSER_MIN_TIMEOUT_SECONDS = 25
AMAZON_DIAGNOSTIC_SNIPPET_LENGTH = 220
HM_BROWSER_MIN_TIMEOUT_SECONDS = 25


class _HtmlResponse:
    def __init__(self, url: str, html: str):
        self.url = url
        self.status_code = 200
        self.headers = {"content-type": "text/html; charset=utf-8"}
        self.text = html
        self.content = html.encode("utf-8", errors="replace")

    def raise_for_status(self) -> None:
        return None


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


def _is_amazon_protection_page(html: str) -> bool:
    normalized = normalize_offer_text(html)
    return any(
        marker in normalized
        for marker in (
            "captcha",
            "automated access",
            "robot check",
            "not a robot",
            "robot olmadiginizi",
            "enter the characters you see below",
        )
    )


def _is_usable_amazon_response(response, expect_search: bool) -> bool:
    html = decode_response_text(response)
    if _is_amazon_protection_page(html):
        raise HermesError("Amazon bot korumasi nedeniyle captcha/koruma sayfasi dondu.")
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
            "data-cy=\"title-recipe\"",
            "data-cy='title-recipe'",
            "puis-card-container",
            "/dp/",
            "/gp/product/",
        )
    )


def _get_amazon_response(session, candidate: str, timeout: int, expect_search: bool):
    cache = _amazon_response_cache(session)
    cache_key = _amazon_cache_key(candidate, expect_search)
    cached_response = cache.get(cache_key)
    if cached_response is not None:
        return cached_response

    response = session.get(
        candidate,
        headers=amazon_headers(candidate),
        timeout=timeout,
        allow_redirects=True,
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
        return cached_response

    curl_session = getattr(session, "_hermes_amazon_curl_session", None)
    if curl_session is None:
        curl_session = curl_requests.Session()
        setattr(session, "_hermes_amazon_curl_session", curl_session)
    response = curl_session.get(
        candidate,
        headers=amazon_headers(candidate),
        timeout=timeout,
        allow_redirects=True,
        impersonate="chrome124",
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


def _get_amazon_response_with_browser(session: requests.Session, candidate: str, timeout: int, expect_search: bool):
    cache = _amazon_response_cache(session)
    cache_key = _amazon_cache_key(f"browser:{candidate}", expect_search)
    cached_response = cache.get(cache_key)
    if cached_response is not None:
        return cached_response

    browser_timeout = max(AMAZON_BROWSER_MIN_TIMEOUT_SECONDS, int(timeout) + 10)
    with tempfile.TemporaryDirectory(prefix="hermes-amazon-browser-") as profile_dir:
        command = [
            _chromium_binary(),
            "--headless=new",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-background-networking",
            "--disable-default-apps",
            "--disable-extensions",
            "--disable-sync",
            "--disable-setuid-sandbox",
            "--disable-software-rasterizer",
            "--disable-crash-reporter",
            "--disable-features=Translate,MediaRouter,OptimizationHints",
            "--hide-scrollbars",
            "--no-first-run",
            "--no-zygote",
            "--no-sandbox",
            "--window-size=1365,900",
            "--lang=tr-TR",
            f"--user-agent={AMAZON_CHROME_USER_AGENT}",
            f"--user-data-dir={profile_dir}",
            "--dump-dom",
            candidate,
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=browser_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise HermesError("Amazon gercek tarayici modu zaman asimina ugradi.") from exc
        except OSError as exc:
            raise HermesError(f"Amazon gercek tarayici modu baslatilamadi: {exc}") from exc

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    response = _HtmlResponse(candidate, stdout)
    try:
        if stdout and _is_usable_amazon_response(response, expect_search):
            if completed.returncode != 0:
                log(
                    "Amazon tarayici modu HTML dondurdu: "
                    f"returncode={completed.returncode} | stderr={_diagnostic_snippet(stderr)} | "
                    f"url={_short_amazon_url(candidate)}"
                )
            cache[cache_key] = response
            return response
    except Exception as exc:  # noqa: BLE001
        if completed.returncode != 0:
            log(
                "Amazon tarayici modu HTML kullanilamadi: "
                f"returncode={completed.returncode} | sebep={_amazon_error_reason(exc)} | "
                f"stdout={_diagnostic_snippet(stdout)} | stderr={_diagnostic_snippet(stderr)} | "
                f"url={_short_amazon_url(candidate)}"
            )
        raise

    if completed.returncode != 0:
        error_text = _diagnostic_snippet(stderr or stdout)
        raise HermesError(f"Amazon gercek tarayici modu basarisiz oldu: {error_text[:160] or completed.returncode}")

    if not stdout:
        raise HermesError("Amazon gercek tarayici modunda bos HTML dondu.")
    if not _is_usable_amazon_response(response, expect_search):
        raise HermesError("Amazon gercek tarayici modunda da bos veya farkli bir sayfa dondurdu.")
    cache[cache_key] = response
    return response


def _amazon_error_status(exc: Exception) -> Optional[int]:
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    return response_status if isinstance(response_status, int) else None


def _is_hard_amazon_block_error(exc: Exception) -> bool:
    status_code = _amazon_error_status(exc)
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
    status_code = _amazon_error_status(exc)
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
            "status": _amazon_error_status(exc),
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


def _reset_amazon_client_state(session: requests.Session) -> None:
    for attr_name in (
        "_hermes_amazon_curl_session",
        "_hermes_amazon_response_cache",
        "_hermes_amazon_primed",
        "_hermes_amazon_seeded",
    ):
        if hasattr(session, attr_name):
            delattr(session, attr_name)
    for domain in (".amazon.com.tr", "www.amazon.com.tr"):
        try:
            session.cookies.clear(domain=domain)
        except Exception:  # noqa: BLE001
            pass


def _seed_amazon_session(session: requests.Session) -> None:
    if getattr(session, "_hermes_amazon_seeded", False):
        return
    session.cookies.set("i18n-prefs", "TRY", domain=".amazon.com.tr")
    session.cookies.set("lc-acbtr", "tr_TR", domain=".amazon.com.tr")
    setattr(session, "_hermes_amazon_seeded", True)


def _prime_amazon_session(session: requests.Session, timeout: int) -> None:
    if getattr(session, "_hermes_amazon_primed", False):
        return
    _seed_amazon_session(session)
    response = session.get(
        "https://www.amazon.com.tr/",
        headers=amazon_headers("https://www.amazon.com.tr/"),
        timeout=timeout,
        allow_redirects=True,
    )
    response.raise_for_status()
    if _is_amazon_protection_page(decode_response_text(response)):
        raise HermesError("Amazon bot korumasi nedeniyle captcha/koruma sayfasi dondu.")
    setattr(session, "_hermes_amazon_primed", True)


def fetch_amazon_page(session: requests.Session, url: str, timeout: int, expect_search: bool = False):
    last_error: Optional[Exception] = None
    hard_blocked = False
    attempts: List[Dict[str, Any]] = []
    variants = amazon_url_variants(url)
    _seed_amazon_session(session)

    if curl_requests is not None:
        for candidate_index, candidate in enumerate(variants):
            try:
                return _get_amazon_response_with_curl(session, candidate, timeout, expect_search)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                _record_amazon_attempt(attempts, "curl", candidate, expect_search, exc)
                if _is_hard_amazon_block_error(exc):
                    hard_blocked = True
                    rescue_candidates = variants[
                        candidate_index : candidate_index + AMAZON_HARD_BLOCK_RESCUE_VARIANT_LIMIT
                    ]
                    for rescue_index, rescue_candidate in enumerate(rescue_candidates):
                        _reset_amazon_client_state(session)
                        _seed_amazon_session(session)
                        method = "curl_fresh" if rescue_index == 0 else "curl_variant_fresh"
                        try:
                            return _get_amazon_response_with_curl(
                                session,
                                rescue_candidate,
                                timeout,
                                expect_search,
                            )
                        except Exception as retry_exc:  # noqa: BLE001
                            last_error = retry_exc
                            _record_amazon_attempt(attempts, method, rescue_candidate, expect_search, retry_exc)
                    browser_candidates = variants[
                        candidate_index : candidate_index + AMAZON_BROWSER_RESCUE_VARIANT_LIMIT
                    ]
                    for browser_candidate in browser_candidates:
                        try:
                            response = _get_amazon_response_with_browser(
                                session,
                                browser_candidate,
                                timeout,
                                expect_search,
                            )
                            _reset_amazon_client_state(session)
                            _seed_amazon_session(session)
                            return response
                        except Exception as browser_exc:  # noqa: BLE001
                            last_error = browser_exc
                            _record_amazon_attempt(
                                attempts,
                                "browser_rescue",
                                browser_candidate,
                                expect_search,
                                browser_exc,
                            )
                    _reset_amazon_client_state(session)
                    _seed_amazon_session(session)
                    for requests_candidate in variants:
                        try:
                            return _get_amazon_response(
                                session,
                                requests_candidate,
                                timeout,
                                expect_search,
                            )
                        except Exception as requests_exc:  # noqa: BLE001
                            last_error = requests_exc
                            _record_amazon_attempt(
                                attempts,
                                "requests_after_rescue",
                                requests_candidate,
                                expect_search,
                                requests_exc,
                            )
                    break

    if not hard_blocked:
        for candidate in variants:
            try:
                return _get_amazon_response(session, candidate, timeout, expect_search)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                _record_amazon_attempt(attempts, "requests", candidate, expect_search, exc)
                if _is_hard_amazon_block_error(exc):
                    hard_blocked = True
                    break

    if expect_search and not hard_blocked:
        try:
            _prime_amazon_session(session, timeout)
            for candidate in variants:
                try:
                    return _get_amazon_response(session, candidate, timeout, expect_search)
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    _record_amazon_attempt(attempts, "requests_prime", candidate, expect_search, exc)
                    if _is_hard_amazon_block_error(exc):
                        hard_blocked = True
                        break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            _record_amazon_attempt(attempts, "prime", "https://www.amazon.com.tr/", expect_search, exc)
            if _is_hard_amazon_block_error(exc):
                hard_blocked = True

    if last_error:
        if hard_blocked:
            _reset_amazon_client_state(session)
        _log_amazon_diagnostics(url, expect_search, attempts)
        raise last_error
    _log_amazon_diagnostics(url, expect_search, attempts)
    raise HttpStatusHermesError(0, url)


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
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
        }
    )
    return headers


def fetch_hm_page(session: requests.Session, url: str, timeout: int) -> requests.Response:
    cache = getattr(session, "_hermes_hm_browser_cache", None)
    if cache is None:
        cache = {}
        setattr(session, "_hermes_hm_browser_cache", cache)
    if url in cache:
        return cache[url]

    browser_timeout = max(HM_BROWSER_MIN_TIMEOUT_SECONDS, int(timeout) + 10)
    with tempfile.TemporaryDirectory(prefix="hermes-hm-browser-") as profile_dir:
        command = [
            _chromium_binary("H&M"),
            "--headless=new",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-background-networking",
            "--disable-default-apps",
            "--disable-extensions",
            "--disable-sync",
            "--disable-setuid-sandbox",
            "--disable-software-rasterizer",
            "--disable-crash-reporter",
            "--disable-features=Translate,MediaRouter,OptimizationHints",
            "--hide-scrollbars",
            "--no-first-run",
            "--no-zygote",
            "--no-sandbox",
            "--window-size=1365,900",
            "--lang=tr-TR",
            f"--user-agent={AMAZON_CHROME_USER_AGENT}",
            f"--user-data-dir={profile_dir}",
            "--virtual-time-budget=5000",
            "--dump-dom",
            url,
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=browser_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise HermesError("H&M tarayici modu zaman asimina ugradi.") from exc
        except OSError as exc:
            raise HermesError(f"H&M tarayici modu baslatilamadi: {exc}") from exc

    html = completed.stdout or ""
    if not html:
        error_text = _diagnostic_snippet(completed.stderr or "")
        raise HermesError(f"H&M tarayici modu bos sayfa dondurdu: {error_text}")
    response = _HtmlResponse(url, repair_mojibake(html))
    cache[url] = response
    return response


def cleaned_html(response: requests.Response) -> str:
    return repair_mojibake(decode_response_text(response))
