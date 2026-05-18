from typing import Optional

from ..errors import HermesError
from ..models import OfferResult
from .amazon_common import extract_secondary_offer_price
from .base import (
    extract_jsonld_product,
    extract_price_from_meta,
    extract_price_from_selectors,
    extract_title,
    soup_from_html,
)

AMAZON_PRODUCT_SELECTORS = [
    "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
    "#corePrice_feature_div .a-price .a-offscreen",
    "#tp_price_block_total_price_ww .a-offscreen",
    ".apexPriceToPay .a-offscreen",
]


def extract_offer(html: str) -> OfferResult:
    soup = soup_from_html(html)
    jsonld_title, jsonld_price = extract_jsonld_product(soup)
    title: Optional[str] = jsonld_title or extract_title(soup) or "Amazon ürünü"

    for price in (
        extract_secondary_offer_price(soup),
        jsonld_price,
        extract_price_from_meta(soup),
        extract_price_from_selectors(soup, AMAZON_PRODUCT_SELECTORS),
    ):
        if price is not None:
            return OfferResult(title=title, price=price, seller=None)

    raise HermesError("Amazon sayfasından fiyat bulunamadı.")
