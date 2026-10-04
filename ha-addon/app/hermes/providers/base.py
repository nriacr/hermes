import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional

import requests
from bs4 import BeautifulSoup

from ..errors import HermesError
from ..utils import normalize_offer_text, parse_decimal, repair_mojibake

PRODUCT_TITLE_SELECTORS = [
    "#productTitle",
    "#title",
    "h1[data-test-id='title']",
    "h1.pr-new-br",
    "h1.product-name",
    "h1",
    "meta[property='og:title']",
]

PRICE_META_SELECTORS = [
    ("meta", {"property": "product:price:amount"}, "content"),
    ("meta", {"property": "og:price:amount"}, "content"),
    ("meta", {"itemprop": "price"}, "content"),
    ("meta", {"name": "twitter:data1"}, "content"),
]

SCRIPT_PRICE_PATTERNS = [
    re.compile(
        r'"(?:price|sellingPrice|discountedPrice|currentPrice|amount)"\s*:\s*"?(?P<price>\d+(?:[.,]\d{1,2})?)"?',
        re.IGNORECASE,
    ),
    re.compile(r"(?P<price>\d{1,3}(?:\.\d{3})*,\d{2})\s*TL", re.IGNORECASE),
]


def soup_from_html(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def extract_title(soup: BeautifulSoup) -> Optional[str]:
    for selector in PRODUCT_TITLE_SELECTORS:
        element = soup.select_one(selector)
        if not element:
            continue
        if element.name == "meta":
            content = str(element.get("content", "")).strip()
            if content:
                return repair_mojibake(content)
        text = element.get_text(" ", strip=True)
        if text:
            return repair_mojibake(text)
    return None


def extract_price_from_meta(soup: BeautifulSoup):
    for tag_name, attrs, attr_name in PRICE_META_SELECTORS:
        element = soup.find(tag_name, attrs=attrs)
        if element and element.get(attr_name):
            try:
                return parse_decimal(str(element[attr_name]))
            except HermesError:
                continue
    return None


def extract_price_from_selectors(soup: BeautifulSoup, selectors: list[str]):
    for selector in selectors:
        element = soup.select_one(selector)
        if not element:
            continue
        raw_value = str(element.get("content") or element.get("value") or "").strip()
        text = raw_value or element.get_text(" ", strip=True)
        if text:
            try:
                return parse_decimal(text)
            except HermesError:
                continue
    return None


def extract_price_from_scripts(html: str):
    for pattern in SCRIPT_PRICE_PATTERNS:
        candidates = []
        for match in pattern.finditer(html):
            try:
                candidates.append(parse_decimal(match.group("price")))
            except HermesError:
                continue
        if candidates:
            return min(candidates)
    return None


def iter_json_objects(value: Any) -> Iterable[dict]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_json_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_json_objects(child)


def as_type_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).casefold() for item in value]
    if value is None:
        return []
    return [str(value).casefold()]


def price_from_offer(offer: Any):
    if isinstance(offer, list):
        candidates = [price_from_offer(item) for item in offer]
        candidates = [item for item in candidates if item is not None]
        return min(candidates) if candidates else None
    if not isinstance(offer, dict):
        return None
    for key in ("price", "lowPrice", "highPrice", "priceAmount"):
        raw = offer.get(key)
        if raw not in (None, ""):
            try:
                return parse_decimal(str(raw))
            except HermesError:
                continue
    nested_offer = offer.get("offers")
    if nested_offer is not None:
        return price_from_offer(nested_offer)
    return None


def extract_jsonld_product(soup: BeautifulSoup):
    found_title = None
    found_price = None
    for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw = script.string or script.get_text(" ", strip=True)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for item in iter_json_objects(payload):
            if "product" not in as_type_list(item.get("@type")):
                continue
            title = str(item.get("name") or "").strip()
            if title and not found_title:
                found_title = repair_mojibake(title)
            price = price_from_offer(item.get("offers"))
            if price is not None:
                found_price = price if found_price is None else min(found_price, price)
    return found_title, found_price


def excluded_term_in_title(watch, title: str) -> str:
    """The first configured exclusion found in a title (comma-separated OR filter)."""
    normalized_title = normalize_offer_text(title)
    return next((term for term in watch.excluded_terms if normalize_offer_text(term) in normalized_title), "")


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------


class RequestSpacing:
    """A minimum time between request starts to one site.

    Applied on top of the normal random delay; the first request and pages
    served from a cycle cache never wait.
    """

    def __init__(self, min_gap_seconds: float, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.min_gap_seconds = float(min_gap_seconds)
        self.sleep = sleep
        self.clock = clock
        self.last_start: Optional[float] = None
        self._lock = threading.Lock()

    def wait(self) -> float:
        """Wait until the gap has passed, then mark a request start; returns the wait."""
        with self._lock:
            waited = 0.0
            if self.last_start is not None and self.min_gap_seconds > 0:
                waited = max(0.0, self.last_start + self.min_gap_seconds - self.clock())
                if waited:
                    self.sleep(waited)
            self.last_start = self.clock()
            return waited


# The ReadContext lane of a provider's quick main-page reads (see Provider.has_depo_lane).
DEPO_LANE = "depo"


@dataclass
class ReadContext:
    """What a provider may use while reading one watch in a monitoring cycle."""

    timeout: int
    session: requests.Session
    # Waits the configured random delay before an additional page request.
    pace: Callable[[str], None] = lambda _label: None
    # Configured card names per site; search pages use them for specificity.
    watch_names: Dict[str, List[str]] = field(default_factory=dict)
    # Reports one network request: (method, kind, outcome, duration_ms).
    measure: Callable[[str, str, str, int], None] = lambda _method, _kind, _outcome, _ms: None
    # "" for the ordinary queue, DEPO_LANE for the quick main-page reads of a site with a Depo lane.
    lane: str = ""


@dataclass
class WatchRead:
    """Side results a provider reports while its offers are being consumed."""

    # Positively unavailable variants: {"product_title", "product_url", "reason"}.
    unavailable: List[dict] = field(default_factory=list)
    # An access block that stopped further requests after partial results.
    blocked: Optional[BaseException] = None
    # ISO time before which a new read cannot change the result.
    retry_after: Optional[str] = None


class Provider:
    """One commerce site. Fetching and parsing stay inside the site's module."""

    site = ""
    # Notify when a previously out-of-stock product returns.
    notifies_stock_return = False
    # Include the offer's seller in opportunity notifications.
    alert_shows_seller = False
    # Pause a watch with growing back-off after a protection page.
    backs_off_on_protection = False
    # The provider spaces every network request itself (Amazon's client);
    # otherwise the monitor spaces the start of each watch read.
    spaces_own_requests = False

    def begin_cycle(self) -> None:
        """Forget per-cycle caches; prices are always read again next cycle."""

    # The provider reads quick main-page reads on their own thread beside its long sweeps
    # (the monitor then runs two lanes for the site).
    has_depo_lane = False

    def restore_requests(self, requests) -> None:
        """Network requests of the last hour from the database, (finished at, ms, outcome); most sites ignore them."""

    def next_read_is_main(self, watch) -> bool:
        """True when the Depo lane may read the watch's main page now (its family is remembered)."""
        return False

    def needs_sweep(self, watch) -> bool:
        """True when the watch's variant family is due for a full sweep (the sweep queue reads it)."""
        return True

    def is_watch_busy(self, watch) -> bool:
        """True while one lane is reading the watch; the other lane waits."""
        return False

    def absorb_block(self, watch) -> bool:
        """Called once per read of `watch` that ended in a block.

        True when the provider dealt with it itself (one stubborn page rests alone);
        False when it is a site-wide block and the monitor pauses the whole site.
        """
        return False

    def read_rank(self, watch) -> int:
        """Order inside a priority tier: lower ranks are read first (quick reads before long ones)."""
        return 0

    def read_due(self, watch) -> bool:
        """False while the provider's own rhythm says this watch need not be read yet."""
        return True

    def close(self) -> None:
        """Release process-lived resources such as sessions or browsers."""

    def is_search_url(self, url: str) -> bool:
        return False

    def read(self, watch, ctx: ReadContext, outcome: WatchRead) -> Iterable:
        """Return (or yield) the watch's offers. Raise typed Hermes errors."""
        raise NotImplementedError

    def is_protection_error(self, exc: BaseException) -> bool:
        return False

    def keeps_offer(self, watch, offer) -> bool:
        """Site-specific seller filter; most sites have none."""
        return True

    def display_title(self, title: str) -> str:
        return title
