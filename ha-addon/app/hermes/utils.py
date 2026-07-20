import random
import re
import unicodedata
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from html import unescape
from typing import Any, Dict
from urllib.parse import parse_qs, urlparse, urlunparse

from .constants import (
    AMAZON_BASE_URL,
    DEFAULT_HEADERS,
    SITE_AMAZON,
    SITE_HEPSIBURADA,
    SITE_HM,
    SITE_LABELS,
    SITE_NETWORK,
    SITE_NORDBRON,
    SITE_TRENDYOL,
    SITE_ZARA,
    USER_AGENTS,
)
from .errors import HermesError

MOJIBAKE_MARKERS = ("Ã", "Ä", "Å", "Â", "�")
ASIN_URL_PATTERN = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})")
HEPSIBURADA_PRODUCT_URL_PATTERN = re.compile(
    r"/(?:[^\s'\"<>]+)-(?:p|pm)-[A-Z0-9]+",
    re.IGNORECASE,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_now() -> datetime:
    return datetime.now().astimezone()


def format_local_datetime(value: datetime) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def parse_iso_datetime(value: Any):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_decimal(raw_value: str) -> Decimal:
    cleaned = str(raw_value).strip()
    cleaned = cleaned.replace("TL", "").replace("TRY", "")
    cleaned = cleaned.replace("\xa0", "").replace(" ", "")
    cleaned = re.sub(r"[^\d,.-]", "", cleaned)
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "." in cleaned:
        groups = cleaned.split(".")
        if len(groups) > 1 and all(len(group) == 3 for group in groups[1:]):
            cleaned = "".join(groups)
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise HermesError(f"Fiyat ayrıştırılamadı: {raw_value!r}") from exc


def parse_bool(raw_value: Any, default: bool = False) -> bool:
    if raw_value is None:
        return default
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, str):
        return raw_value.strip().casefold() in {"1", "true", "yes", "on", "evet"}
    return bool(raw_value)


def repair_mojibake(value: Any) -> str:
    text = unescape(str(value or ""))
    for _ in range(3):
        if not any(marker in text for marker in MOJIBAKE_MARKERS):
            return text
        try:
            fixed = text.encode("latin-1").decode("utf-8")
        except UnicodeError:
            return text
        before = sum(text.count(m) for m in MOJIBAKE_MARKERS)
        after = sum(fixed.count(m) for m in MOJIBAKE_MARKERS)
        if after >= before:
            return text
        text = fixed
    return text


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).casefold()).strip()


def normalize_offer_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value).casefold())
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.replace("ı", "i")
    return re.sub(r"\s+", " ", normalized).strip()


def normalize_key(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()


def normalize_item_key(*parts: str) -> str:
    return normalize_key("_".join(parts))


def format_tl(value: Decimal, with_currency: bool = False) -> str:
    amount = Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_DOWN)
    formatted = f"{amount:,}".replace(",", ".")
    return f"{formatted} TL" if with_currency else formatted


def format_signed_tl(value: Decimal, with_currency: bool = False) -> str:
    sign = "+" if value >= 0 else "-"
    return f"{sign}{format_tl(abs(value), with_currency=with_currency)}"


def shorten_log_text(value: str, max_length: int = 90) -> str:
    clean = re.sub(r"\s+", " ", repair_mojibake(value)).strip()
    if len(clean) <= max_length:
        return clean
    return clean[: max_length - 3].rstrip() + "..."


def log_cell(value: str, width: int, align: str = "left") -> str:
    text = shorten_log_text(value, width)
    return text.rjust(width) if align == "right" else text.ljust(width)


def make_amazon_absolute_url(raw_url: str) -> str:
    if raw_url.startswith("http://") or raw_url.startswith("https://"):
        return raw_url
    if raw_url.startswith("/"):
        return f"{AMAZON_BASE_URL}{raw_url}"
    return f"{AMAZON_BASE_URL}/{raw_url}"


def extract_asin_from_url(url: str):
    match = ASIN_URL_PATTERN.search(url)
    return match.group(1) if match else None


def is_amazon_search_url(url: str) -> bool:
    parsed = urlparse(str(url or ""))
    host = parsed.netloc.casefold()
    if "amazon." not in host:
        return False
    if parsed.path.rstrip("/") == "/s":
        return True
    query = parse_qs(parsed.query)
    return "k" in query and not any(part in parsed.path for part in ("/dp/", "/gp/product/"))


def is_hepsiburada_product_url(url: str) -> bool:
    parsed = urlparse(str(url or ""))
    if "hepsiburada" not in parsed.netloc.casefold():
        return False
    return HEPSIBURADA_PRODUCT_URL_PATTERN.search(parsed.path) is not None


def is_hepsiburada_search_url(url: str) -> bool:
    parsed = urlparse(str(url or ""))
    if "hepsiburada" not in parsed.netloc.casefold():
        return False
    return not is_hepsiburada_product_url(url)


def watch_name_required_for_url(url: str) -> bool:
    return is_amazon_search_url(url) or is_hepsiburada_search_url(url)


def canonical_amazon_product_url(raw_url: str, fallback_asin: str = "") -> str:
    absolute_url = make_amazon_absolute_url(raw_url)
    asin = extract_asin_from_url(absolute_url) or fallback_asin
    if asin:
        return f"{AMAZON_BASE_URL}/dp/{asin}"
    return absolute_url.split("?", 1)[0]


def canonical_tracking_url(raw_url: str) -> str:
    """Return a stable URL key while preserving non-Amazon variant queries."""
    url = str(raw_url or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if "amazon." in parsed.netloc.casefold():
        return canonical_amazon_product_url(url).casefold()
    if not parsed.scheme or not parsed.netloc:
        return url
    return urlunparse(
        (
            parsed.scheme.casefold(),
            parsed.netloc.casefold(),
            parsed.path.rstrip("/"),
            parsed.params,
            parsed.query,
            "",
        )
    )


def detect_site_from_url(url: str) -> str:
    host = urlparse(url).netloc.casefold()
    if "hepsiburada" in host:
        return SITE_HEPSIBURADA
    if "trendyol" in host:
        return SITE_TRENDYOL
    if "network" in host:
        return SITE_NETWORK
    if "nordbron" in host:
        return SITE_NORDBRON
    if "zara" in host:
        return SITE_ZARA
    if "hm.com" in host:
        return SITE_HM
    if "amazon" in host:
        return SITE_AMAZON
    raise HermesError(f"Desteklenmeyen site alan adı: {host or url}")


def site_label(site: str) -> str:
    return SITE_LABELS.get(site, site.title())


def referer_for_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}/"
    return AMAZON_BASE_URL + "/"


def build_headers(url: str) -> Dict[str, str]:
    headers = dict(DEFAULT_HEADERS)
    headers["User-Agent"] = random.choice(USER_AGENTS)
    headers["Referer"] = referer_for_url(url)
    return headers
