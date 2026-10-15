"""Amazon per-cycle page cache and short-lived absence/exclusion memory (mixin of AmazonProvider)."""

import math
import time
import zlib
from typing import Optional

from ...constants import AMAZON_EXCLUDED_PAGE_REFRESH_SECONDS
from ...logging_utils import log
from ...models import WatchRule

# Bound repeated reads of an absent/unreadable offer independently of card priority.
# Successful offers and access failures never enter this cache.
NO_OFFER_RECHECK_SECONDS = 5 * 60
NO_OFFER_CACHE_LIMIT = 512
PAGE_CACHE_LIMIT = 128
EXCLUDED_PAGE_CACHE_LIMIT = 512


class PageCacheMixin:
    def _remember_page(self, url: str, snapshot: dict) -> None:
        # A cycle can contain several large families; keep enough parsed pages
        # to dedupe repeated cards without growing without bound.
        if url not in self.pages and len(self.pages) >= PAGE_CACHE_LIMIT:
            self.pages.pop(next(iter(self.pages)))
        self.pages[url] = snapshot

    def _cached_absence(self, cache_key: str) -> Optional[dict]:
        absence = self.client.unavailable_product_pages.get(cache_key)
        if not absence:
            return None
        now = time.monotonic()
        if now >= absence["retry_at"]:
            self.client.unavailable_product_pages.pop(cache_key, None)
            return None
        if now - absence.get("last_skip_logged_at", 0) >= 60:
            log(f"Amazon aynı varyant için gereksiz istek atlandı: {cache_key} | "
                f"yeniden kontrol={math.ceil(absence['retry_at'] - now)} sn")
            absence["last_skip_logged_at"] = now
        return absence["snapshot"]

    def _remember_absence(self, cache_key: str, snapshot: dict) -> None:
        cache = self.client.unavailable_product_pages
        if cache_key not in cache and len(cache) >= NO_OFFER_CACHE_LIMIT:
            cache.pop(next(iter(cache)))
        now = time.monotonic()
        cache[cache_key] = {"snapshot": snapshot, "retry_at": now + NO_OFFER_RECHECK_SECONDS, "last_skip_logged_at": now}
        log(f"Amazon fiyat/teklif bulunmayan varyant: {cache_key} | yeniden kontrol={NO_OFFER_RECHECK_SECONDS} sn")

    def _remembered_exclusion(self, cache_key: str, watch: WatchRule) -> Optional[dict]:
        entry = self.client.excluded_pages.get(cache_key)
        if not entry:
            return None
        if entry["terms"] != tuple(watch.excluded_terms) or time.monotonic() >= entry["refresh_at"]:
            self.client.excluded_pages.pop(cache_key, None)
            return None
        return entry

    def _remember_exclusion(self, cache_key: str, watch: WatchRule, variations) -> None:
        cache = self.client.excluded_pages
        if cache_key not in cache and len(cache) >= EXCLUDED_PAGE_CACHE_LIMIT:
            cache.pop(next(iter(cache)))
        # Each page refreshes at its own time (15-45 minutes) so they never all expire in one sweep.
        spread = 0.5 + (zlib.crc32(cache_key.encode("utf-8")) % 100) / 100
        cache[cache_key] = {"variations": list(variations), "terms": tuple(watch.excluded_terms),
                            "refresh_at": time.monotonic() + AMAZON_EXCLUDED_PAGE_REFRESH_SECONDS * spread}
