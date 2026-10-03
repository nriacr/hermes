"""One-off link test: the real provider read, without state, summary or notifications."""

import random
import time
from decimal import Decimal
from typing import List, Optional

import requests

from ..config import load_config
from ..constants import DEFAULT_REQUEST_DELAY_MAX_SECONDS, DEFAULT_REQUEST_DELAY_MIN_SECONDS, DEFAULT_REQUEST_TIMEOUT_SECONDS
from ..errors import HermesError
from ..logging_utils import log
from ..models import OfferResult, WatchRule
from ..providers.amazon import AmazonProvider
from ..providers.amazon.client import AmazonClient
from ..providers.base import ReadContext, WatchRead
from ..providers.registry import ProviderSet
from ..utils import detect_site_from_url
from .cycle import skipped_offer_reason


def _configured_context() -> tuple[int, int, dict]:
    """Request delays and card names of the saved settings, as the monitor uses them."""
    try:
        config = load_config()
    except Exception:  # noqa: BLE001 - a test should work while the settings are being fixed
        return DEFAULT_REQUEST_DELAY_MIN_SECONDS, DEFAULT_REQUEST_DELAY_MAX_SECONDS, {}
    names: dict = {}
    for configured in config.watches:
        if configured.name:
            names.setdefault(configured.site, []).append(configured.name)
    return config.request_delay_min_seconds, config.request_delay_max_seconds, names


def inspect_link(url: str, name: str = "", size: str = "", include_variations: bool = False,
                 excluded_terms: Optional[List[str]] = None, amazon_browser: bool = False) -> tuple[str, List[OfferResult]]:
    """Read one supported link exactly like the monitor would, and discard the result."""
    source_url = str(url or "").strip()
    if not source_url:
        raise HermesError("Test etmek için bir bağlantı girilmeli.")
    site = detect_site_from_url(source_url)
    watch = WatchRule(
        name=str(name or "").strip(), site=site, url=source_url, target_price=Decimal("0"),
        size=str(size or "").strip(), include_variations=bool(include_variations),
        excluded_terms=[str(term).strip() for term in (excluded_terms or []) if str(term).strip()],
    )
    delay_min, delay_max, watch_names = _configured_context()

    def pace(label: str) -> None:
        delay = random.randint(delay_min, delay_max)
        log(f"Bağlantı testi | {label} isteği öncesi {delay} saniye bekleniyor.")
        time.sleep(delay)

    amazon = AmazonProvider(AmazonClient(transport="browser" if amazon_browser else "http"))
    with ProviderSet({amazon.site: amazon}) as providers, requests.Session() as session:
        ctx = ReadContext(timeout=DEFAULT_REQUEST_TIMEOUT_SECONDS, session=session, pace=pace, watch_names=watch_names)
        offers = list(providers[site].read(watch, ctx, WatchRead()))
    offers = [offer for offer in offers
              if not skipped_offer_reason(watch, offer, offer.title or watch.name or source_url)]
    if not offers:
        raise HermesError("Bağlantıda seçilen filtrelerle okunabilir ürün veya fiyat bulunamadı.")
    return site, offers
