import time
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from .constants import RETRY_DELAYS_SECONDS, RETRY_STATUS_CODES
from .errors import HermesError, HttpStatusHermesError
from .logging_utils import log
from .utils import build_headers, normalize_offer_text, repair_mojibake

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
    headers = build_headers(url)
    headers.update(
        {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Linux"',
        }
    )
    return headers


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


def _get_amazon_response_with_curl(candidate: str, timeout: int, expect_search: bool):
    if curl_requests is None:
        raise HermesError("Amazon icin tarayici-benzeri alternatif istek kullanilamiyor.")
    curl_session = curl_requests.Session()
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
    _seed_amazon_session(session)
    for candidate in amazon_url_variants(url):
        try:
            return _get_amazon_response(session, candidate, timeout, expect_search)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if _is_hard_amazon_block_error(exc):
                hard_blocked = True
                break

    if expect_search and not hard_blocked:
        try:
            _prime_amazon_session(session, timeout)
            for candidate in amazon_url_variants(url):
                try:
                    return _get_amazon_response(session, candidate, timeout, expect_search)
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    if _is_hard_amazon_block_error(exc):
                        hard_blocked = True
                        break
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if _is_hard_amazon_block_error(exc):
                hard_blocked = True

    if curl_requests is not None and not hard_blocked:
        for candidate in amazon_url_variants(url):
            try:
                return _get_amazon_response_with_curl(candidate, timeout, expect_search)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if _is_hard_amazon_block_error(exc):
                    break

    if last_error:
        raise last_error
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


def hepsiburada_url_variants(url: str):
    variants = []

    def add(candidate: str) -> None:
        if candidate and candidate not in variants:
            variants.append(candidate)

    add(url)
    if _is_hepsiburada_search_url(url):
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
    for candidate in hepsiburada_url_variants(url):
        try:
            return _get_hepsiburada_response(session, candidate, timeout)
        except Exception as exc:
            last_error = exc

    if curl_requests is not None:
        try:
            curl_session = curl_requests.Session()
            for candidate in hepsiburada_url_variants(url):
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
        except Exception as exc:
            last_error = exc

    if last_error:
        raise last_error
    raise HttpStatusHermesError(0, url)


def cleaned_html(response: requests.Response) -> str:
    return repair_mojibake(decode_response_text(response))
