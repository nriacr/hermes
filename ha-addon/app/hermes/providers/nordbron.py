from ..constants import SITE_NORDBRON
from ..errors import HermesError
from ..models import OfferResult
from .base import (
    Provider,
    extract_jsonld_product,
    extract_price_from_meta,
    extract_price_from_scripts,
    extract_price_from_selectors,
    extract_title,
    soup_from_html,
)
from .http import fetch_with_retries, has_generic_challenge, read_site_html

NORDBRON_SELECTORS = [
    "[class*='product-detail_price']",
    "[class*='price']",
    "[itemprop='price']",
]


def extract_offer(html: str) -> OfferResult:
    soup = soup_from_html(html)
    jsonld_title, jsonld_price = extract_jsonld_product(soup)
    title = jsonld_title or extract_title(soup) or "Nordbron ürünü"

    for price in (
        jsonld_price,
        extract_price_from_meta(soup),
        extract_price_from_selectors(soup, NORDBRON_SELECTORS),
        extract_price_from_scripts(html),
    ):
        if price is not None:
            return OfferResult(title=title, price=price, seller=None)

    raise HermesError("Nordbron sayfasından fiyat bulunamadı.")


def is_challenge_page(html: str) -> bool:
    # Nordbron product pages can mention captcha scripts next to a real price.
    return has_generic_challenge(html) and "product-detail_price" not in html.lower()


class NordbronProvider(Provider):
    site = SITE_NORDBRON

    def read(self, watch, ctx, outcome):
        response = fetch_with_retries(ctx.session, watch.url, ctx.timeout)
        return [extract_offer(read_site_html(response, "Nordbron", is_challenge_page))]
