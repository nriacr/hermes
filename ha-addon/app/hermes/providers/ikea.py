"""IKEA Türkiye: product price from the page, stock from the store-stock service the page itself calls.

A product counts as in stock when one of the card's chosen stores (by default the
online store and IKEA Bayrampaşa) reports "Stokta Var" or "Kritik Stok"; other stores are ignored.
"""

import json
import re
from typing import Dict, List, Optional
from urllib.parse import urlparse

from ..constants import SITE_IKEA
from ..errors import HermesError, HttpStatusHermesError, OutOfStockHermesError, PriceUnavailableHermesError
from ..models import OfferResult, ProductPreview, VariantPreview
from ..utils import normalize_offer_text
from .base import Provider, soup_from_html
from .http import build_headers, fetch_with_retries, has_generic_challenge, read_site_html
from .product_page import explicit_price, product_json

STORE_STOCKS_URL = "https://www.ikea.com.tr/_ws/general.aspx/CheckStoreStocks"
# Watched stores (normalized title fragment, label shown in alerts).
WATCHED_STORES = (("internet magazasi", "İnternet Mağazası"), ("bayrampasa", "Bayrampaşa"))
IN_STOCK_CLASSES = {"full", "critical"}
PRODUCT_CODE = re.compile(r"-(\d{8})/?$")
PRODUCT_DATA = re.compile(r'"@type"\s*:\s*"Product"')


def is_challenge(html: str) -> bool:
    """Every product page loads reCAPTCHA for its forms; only a page without product data is a challenge."""
    return has_generic_challenge(html) and not PRODUCT_DATA.search(html)


def product_code(url: str) -> str:
    match = PRODUCT_CODE.search(urlparse(url).path)
    if not match:
        raise HermesError("IKEA ürün kodu linkten okunamadı; ürün sayfası linki (/urun/...) gerekli.")
    return match.group(1)


STORE_DIMENSION = "Mağaza"


def all_stores(payload: str) -> List[Dict[str, str]]:
    """Every store in the stock answer: (label, StockText, StatusClass) in IKEA's order."""
    try:
        stores = json.loads(payload)["d"]["Data"]["StatusList"]
    except (ValueError, KeyError, TypeError) as exc:
        raise HermesError("IKEA mağaza stok yanıtı okunamadı.") from exc
    if not isinstance(stores, list):
        raise HermesError("IKEA mağaza stok yanıtı beklenen biçimde gelmedi.")
    return [{"label": str(item.get("StoreTitle") or "").strip(), "text": str(item.get("StockText") or "").strip(),
             "class": str(item.get("StatusClass") or "").strip().lower()}
            for item in stores if isinstance(item, dict) and str(item.get("StoreTitle") or "").strip()]


def store_statuses(payload: str, chosen: Optional[List[str]] = None) -> List[Dict[str, str]]:
    """The watched stores; each must be present. Chosen store titles replace the defaults."""
    stores = all_stores(payload)
    wanted = [(normalize_offer_text(name), name) for name in chosen] if chosen else WATCHED_STORES
    found = []
    for fragment, label in wanted:
        store = next((item for item in stores if fragment in normalize_offer_text(item["label"])), None)
        if store is None:
            raise HermesError(f"IKEA stok listesinde {label} yok.")
        found.append(dict(store, label=label))
    return found


def extract_offer(html: str, stock_payload: str, url: str = "", chosen: Optional[List[str]] = None) -> OfferResult:
    product = product_json(soup_from_html(html), "", url)
    title = str((product or {}).get("name") or "").strip()
    price = explicit_price(product) if product else None
    if price is None or not title:
        raise PriceUnavailableHermesError("IKEA ürününe ait fiyat doğrulanamadı.")
    stores = store_statuses(stock_payload, chosen)
    available = [store for store in stores if store["class"] in IN_STOCK_CLASSES]
    if not available:
        raise OutOfStockHermesError(f"Ürün {' ve '.join(store['label'] for store in stores)} stoklarında yok.", title, url)
    seller = ", ".join(f"{store['label']}: {store['text']}" for store in available)
    return OfferResult(title=title, price=price, seller=seller, url=url or None, source="product-jsonld")


class IkeaProvider(Provider):
    site = SITE_IKEA
    notifies_stock_return = True
    alert_shows_seller = True

    card_options = ("variant_filter",)

    @staticmethod
    def _page_and_stocks(url: str, ctx):
        code = product_code(url)
        html = read_site_html(fetch_with_retries(ctx.session, url, ctx.timeout), "IKEA", is_challenge)
        response = ctx.session.post(STORE_STOCKS_URL, data=json.dumps({"sprCode": code}), timeout=ctx.timeout,
                                    headers={**build_headers(url), "Content-Type": "application/json; charset=utf-8",
                                             "X-Requested-With": "XMLHttpRequest", "Referer": url})
        if response.status_code >= 400:
            raise HttpStatusHermesError(response.status_code, STORE_STOCKS_URL)
        return html, response.text

    def inspect(self, url, ctx):
        """Every store with its stock state; the card chooses which stores count."""
        html, stocks = self._page_and_stocks(url, ctx)
        product = product_json(soup_from_html(html), "", url) or {}
        title = str(product.get("name") or "").strip()
        price = explicit_price(product) if product else None
        # The online store first, then the physical stores in IKEA's order.
        stores = sorted(all_stores(stocks), key=lambda store: "internet" not in normalize_offer_text(store["label"]))
        variants = [VariantPreview(store["label"], price, store["class"] in IN_STOCK_CLASSES, {STORE_DIMENSION: store["label"]})
                    for store in stores]
        defaults = [store["label"] for store in stores
                    if any(fragment in normalize_offer_text(store["label"]) for fragment, _ in WATCHED_STORES)]
        return ProductPreview(self.site, title, variants, {STORE_DIMENSION: [store["label"] for store in stores]},
                              {STORE_DIMENSION: defaults})

    def read(self, watch, ctx, outcome):
        html, stocks = self._page_and_stocks(watch.url, ctx)
        return [extract_offer(html, stocks, watch.url, watch.variant_filter.get(STORE_DIMENSION))]
