"""IKEA Türkiye: product price from the page, stock from the store-stock service the page itself calls.

A product counts as in stock when the online store or IKEA Bayrampaşa reports
"Stokta Var" or "Kritik Stok"; every other store is ignored.
"""

import json
import re
from typing import Dict, List
from urllib.parse import urlparse

from ..constants import SITE_IKEA
from ..errors import HermesError, HttpStatusHermesError, OutOfStockHermesError, PriceUnavailableHermesError
from ..models import OfferResult
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


def store_statuses(payload: str) -> List[Dict[str, str]]:
    """The watched stores' (label, StockText, StatusClass); both must be present."""
    try:
        stores = json.loads(payload)["d"]["Data"]["StatusList"]
    except (ValueError, KeyError, TypeError) as exc:
        raise HermesError("IKEA mağaza stok yanıtı okunamadı.") from exc
    if not isinstance(stores, list):
        raise HermesError("IKEA mağaza stok yanıtı beklenen biçimde gelmedi.")
    found = []
    for fragment, label in WATCHED_STORES:
        store = next((item for item in stores if isinstance(item, dict)
                      and fragment in normalize_offer_text(str(item.get("StoreTitle") or ""))), None)
        if store is None:
            raise HermesError(f"IKEA stok listesinde {label} yok.")
        found.append({"label": label, "text": str(store.get("StockText") or "").strip(),
                      "class": str(store.get("StatusClass") or "").strip().lower()})
    return found


def extract_offer(html: str, stock_payload: str, url: str = "") -> OfferResult:
    product = product_json(soup_from_html(html), "", url)
    title = str((product or {}).get("name") or "").strip()
    price = explicit_price(product) if product else None
    if price is None or not title:
        raise PriceUnavailableHermesError("IKEA ürününe ait fiyat doğrulanamadı.")
    stores = store_statuses(stock_payload)
    available = [store for store in stores if store["class"] in IN_STOCK_CLASSES]
    if not available:
        raise OutOfStockHermesError("Ürün İnternet Mağazası ve Bayrampaşa'da stokta yok.", title, url)
    seller = ", ".join(f"{store['label']}: {store['text']}" for store in available)
    return OfferResult(title=title, price=price, seller=seller, url=url or None, source="product-jsonld")


class IkeaProvider(Provider):
    site = SITE_IKEA
    notifies_stock_return = True
    alert_shows_seller = True

    def read(self, watch, ctx, outcome):
        code = product_code(watch.url)
        html = read_site_html(fetch_with_retries(ctx.session, watch.url, ctx.timeout), "IKEA", is_challenge)
        response = ctx.session.post(STORE_STOCKS_URL, data=json.dumps({"sprCode": code}), timeout=ctx.timeout,
                                    headers={**build_headers(watch.url), "Content-Type": "application/json; charset=utf-8",
                                             "X-Requested-With": "XMLHttpRequest", "Referer": watch.url})
        if response.status_code >= 400:
            raise HttpStatusHermesError(response.status_code, STORE_STOCKS_URL)
        return [extract_offer(html, response.text, watch.url)]
