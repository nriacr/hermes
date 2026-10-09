"""Nordbron: selected-product prices with explicit provenance."""

from ..constants import SITE_NORDBRON
from ..errors import PriceUnavailableHermesError, OutOfStockHermesError
from ..models import OfferResult
from .base import Provider, extract_title, soup_from_html
from .http import fetch_with_retries, read_site_html, has_generic_challenge
from .product_page import explicit_price, product_json, visible_price, unavailable

SELECTORS = ("[class*=product-detail_price]", "head meta[property=\"product:price:amount\"]", "head meta[property=\"og:price:amount\"]")

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
        raise PriceUnavailableHermesError("Nordbron ürününe ait fiyat doğrulanamadı.")
    return OfferResult(title=title or product["name"], price=price, url=url or None, source=source)


def is_challenge_page(html: str) -> bool:
    return has_generic_challenge(html) and "product-detail_price" not in html.lower()


class NordbronProvider(Provider):
    site = SITE_NORDBRON

    def read(self, watch, ctx, outcome):
        response = fetch_with_retries(ctx.session, watch.url, ctx.timeout)
        return [extract_offer(read_site_html(response, "Nordbron", is_challenge_page), watch.url)]
