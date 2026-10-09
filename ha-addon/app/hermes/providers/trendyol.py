"""Trendyol: selected-product prices with explicit provenance."""

from ..constants import SITE_TRENDYOL
from ..errors import PriceUnavailableHermesError, OutOfStockHermesError
from ..models import OfferResult
from .base import Provider, extract_title, soup_from_html
from .http import fetch_with_retries, read_site_html
from .product_page import explicit_price, product_json, visible_price, unavailable

SELECTORS = (".product-price-container .prc-dsc", ".product-price-container .prc-slg", "[data-testid=price-current-price]", "head meta[property=\"product:price:amount\"]", "head meta[property=\"og:price:amount\"]")

def extract_offer(html: str, url: str = "") -> OfferResult:
    soup = soup_from_html(html)
    title = extract_title(soup) or ""
    product = product_json(soup, title, url)
    if product and unavailable(product):
        raise OutOfStockHermesError("Ürün stokta yok.", title or product.get("name", ""), url)
    price = explicit_price(product) if product else None
    source = "product-jsonld"
    if price is None:
        price = visible_price(soup, SELECTORS)
        source = "selected-product-price"
    if price is None or not (title or product):
        raise PriceUnavailableHermesError("Trendyol ürününe ait fiyat doğrulanamadı.")
    return OfferResult(title=title or product["name"], price=price, url=url or None, source=source)


class TrendyolProvider(Provider):
    site = SITE_TRENDYOL

    def read(self, watch, ctx, outcome):
        response = fetch_with_retries(ctx.session, watch.url, ctx.timeout)
        return [extract_offer(read_site_html(response, "Trendyol"), watch.url)]
