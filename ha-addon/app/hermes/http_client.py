import time
from typing import Optional

import requests

from .constants import RETRY_DELAYS_SECONDS, RETRY_STATUS_CODES
from .errors import HttpStatusHermesError
from .logging_utils import log
from .utils import build_headers, repair_mojibake

try:
    from curl_cffi import requests as curl_requests
except Exception:
    curl_requests = None


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
            log(f"Site gecici hata verdi ({response.status_code}); {delay} saniye sonra tekrar denenecek.")
            time.sleep(delay)
    raise HttpStatusHermesError(last_status or 0, url)


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


def hepsiburada_url_variants(url: str):
    variants = []

    def add(candidate: str) -> None:
        if candidate and candidate not in variants:
            variants.append(candidate)

    add(url)
    if "?" in url:
        add(url.split("?", 1)[0])
    clean_url = variants[-1]
    if "-pm-" in clean_url:
        add(clean_url.replace("-pm-", "-p-", 1))
    if "-p-" in clean_url:
        add(clean_url.replace("-p-", "-pm-", 1))
    return variants


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
            response = session.get(
                candidate,
                headers=hepsiburada_headers(candidate),
                timeout=timeout,
                allow_redirects=True,
            )
            if response.status_code == 403:
                last_error = HttpStatusHermesError(403, candidate)
                continue
            response.raise_for_status()
            return response
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
                        last_error = HttpStatusHermesError(403, candidate)
                        continue
                    response.raise_for_status()
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
