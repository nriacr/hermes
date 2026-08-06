from ..errors import HermesError
from ..models import OfferResult
from .base import (
    extract_image,
    extract_jsonld_product,
    extract_price_from_meta,
    extract_price_from_scripts,
    extract_price_from_selectors,
    extract_title,
    soup_from_html,
)

NORDBRON_SELECTORS = [
    "[class*='product-detail_price']",
    "[class*='price']",
    "[itemprop='price']",
]


def extract_offer(html: str) -> OfferResult:
    soup = soup_from_html(html)
    jsonld_title, jsonld_price = extract_jsonld_product(soup)
    title = jsonld_title or extract_title(soup) or "Nordbron ürünü"
    image_url = extract_image(soup)

    for price in (
        jsonld_price,
        extract_price_from_meta(soup),
        extract_price_from_selectors(soup, NORDBRON_SELECTORS),
        extract_price_from_scripts(html),
    ):
        if price is not None:
            return OfferResult(title=title, price=price, seller=None, image_url=image_url)

    raise HermesError("Nordbron sayfasından fiyat bulunamadı.")
