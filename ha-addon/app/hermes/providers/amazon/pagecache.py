"""Amazon per-cycle page cache and short-lived absence/exclusion memory (mixin of AmazonProvider)."""

import math
import time
import zlib
from typing import Optional

from ...constants import AMAZON_EXCLUDED_PAGE_REFRESH_SECONDS, DATA_DIR
from ...logging_utils import log
from ...models import WatchRule
from ...storage import export_json, load_json
from .parser import AmazonProductVariation

# Bound repeated reads of an absent/unreadable offer independently of card priority.
# Successful offers and access failures never enter this cache.
NO_OFFER_RECHECK_SECONDS = 5 * 60
NO_OFFER_CACHE_LIMIT = 512
PAGE_CACHE_LIMIT = 128
EXCLUDED_PAGE_CACHE_LIMIT = 512
# Excluded pages are remembered across restarts, so a restart does not spend its first
# minutes re-reading every excluded variant (written at most once a minute).
EXCLUDED_PAGES_PATH = DATA_DIR / "amazon_excluded_pages.json"
EXCLUDED_PAGES_SAVE_SECONDS = 60
PERSIST_EXCLUDED_PAGES = True  # tests switch it off so cases do not share memory


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

    def _load_excluded_pages(self) -> None:
        client = self.client
        if vars(client).get("excluded_pages_loaded"):
            return
        client.excluded_pages_loaded = True
        if not PERSIST_EXCLUDED_PAGES:
            return
        try:
            stored = load_json(EXCLUDED_PAGES_PATH, {})
            offset = time.monotonic() - time.time()
            for key, item in (stored.items() if isinstance(stored, dict) else ()):
                if key in client.excluded_pages or not isinstance(item, dict):
                    continue
                client.excluded_pages[key] = {
                    "variations": [AmazonProductVariation(label=str(v[0]), url=str(v[1])) for v in item["variations"]],
                    "terms": tuple(item["terms"]), "refresh_at": float(item["refresh_at"]) + offset}
        except Exception as exc:  # noqa: BLE001 - the memory is only a saving; start empty
            log(f"Amazon hariç sayfa hafızası okunamadı: {type(exc).__name__}")

    def _save_excluded_pages(self) -> None:
        client = self.client
        now = time.monotonic()
        if not PERSIST_EXCLUDED_PAGES or now - vars(client).get("excluded_pages_saved_at", -math.inf) < EXCLUDED_PAGES_SAVE_SECONDS:
            return
        client.excluded_pages_saved_at = now
        offset = time.time() - now
        payload = {key: {"variations": [[v.label, v.url] for v in item["variations"]], "terms": list(item["terms"]),
                         "refresh_at": item["refresh_at"] + offset}
                   for key, item in client.excluded_pages.items()}
        try:
            export_json(EXCLUDED_PAGES_PATH, payload, now=True)
        except Exception as exc:  # noqa: BLE001
            log(f"Amazon hariç sayfa hafızası yazılamadı: {type(exc).__name__}")

    def _remembered_exclusion(self, cache_key: str, watch: WatchRule) -> Optional[dict]:
        self._load_excluded_pages()
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
        self._save_excluded_pages()
