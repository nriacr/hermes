"""MediaMarkt Türkiye product pages and their payable online price."""

import re
from decimal import Decimal
from urllib.parse import urlsplit

from ..constants import MEDIAMARKT_HOSTS, SITE_MEDIAMARKT
from ..errors import BotProtectionHermesError, HermesError, HttpStatusHermesError, OutOfStockHermesError, PriceUnavailableHermesError
from ..models import OfferResult
from ..utils import build_headers, normalize_offer_text, parse_decimal
from .base import Provider, soup_from_html
from .http import cleaned_html, has_generic_challenge
from .product_page import explicit_price, is_product_scope, product_json, unavailable

PRODUCT_PATH = re.compile(r"/tr/product/[^/]+-(\d+)\.html")


DISCOUNT_TEXT = re.compile(r"[-−]\s*(\d{1,3}(?:\.\d{3})+|\d+),\s*(\d{2})\s*₺\s*sepette indirim", re.I)
BADGE = '[data-test="cofr-pdp-badges"]'
PROMO_TEST = "mms-promoflag"
BASKET_BUTTON_TEST = "cofr-add-to-basket-button"


def basket_discount(soup) -> Decimal:
    badge = next((node for node in soup.select(BADGE) if is_product_scope(node)
                  and "sepette indirim" in normalize_offer_text(node.get_text(" ", strip=True))), None)
    if badge is None:
        return Decimal(0)
    discounts = set()
    for node in badge.find_all_next(attrs={"data-test": re.compile(r"^(mms-promoflag|cofr-add-to-basket-button\b)")}):
        marker = node["data-test"]
        if not is_product_scope(node):
            continue
        if marker.startswith(BASKET_BUTTON_TEST):
            break
        if marker != PROMO_TEST:
            continue
        match = DISCOUNT_TEXT.fullmatch(node.get_text(" ", strip=True))
        if match:
            discounts.add(parse_decimal(f"{match[1]},{match[2]}"))
    if len(discounts) != 1 or min(discounts) <= 0:
        raise PriceUnavailableHermesError("MediaMarkt sepette indirim tutarı doğrulanamadı.")
    return discounts.pop()


def product_code(url):
    parsed = urlsplit(url)
    match = PRODUCT_PATH.fullmatch(parsed.path)
    if parsed.scheme != "https" or parsed.netloc not in MEDIAMARKT_HOSTS or not match:
        raise HermesError("MediaMarkt Türkiye ürün sayfası linki gerekiyor.")
    return match[1]


def read_offer(html, url):
    sku = product_code(url)
    soup = soup_from_html(html)
    heading = soup.select_one("h1")
    title = heading.get_text(" ", strip=True) if heading else ""
    product = product_json(soup, title, url) or product_json(soup, "", url)
    if not product or str(product.get("sku")) != sku:
        if has_generic_challenge(html):
            raise BotProtectionHermesError("MediaMarkt doğrulama sayfası döndü.")
        raise HermesError("MediaMarkt ürün bilgisi doğrulanamadı.")
    title = str(product.get("name") or "").strip()
    if not title:
        raise HermesError("MediaMarkt ürün adı okunamadı.")
    if unavailable(product):
        raise OutOfStockHermesError("MediaMarkt ürünü stokta yok.", title, url)
    offer = product.get("offers")
    if not isinstance(offer, dict) or str(offer.get("availability", "")).rsplit("/", 1)[-1].casefold() != "instock":
        raise HermesError("MediaMarkt stok durumu doğrulanamadı.")
    price = explicit_price(product)
    if price is None or price <= 0:
        raise PriceUnavailableHermesError("MediaMarkt ürün fiyatı doğrulanamadı.")
    discount = basket_discount(soup)
    if discount >= price:
        raise PriceUnavailableHermesError("MediaMarkt sepet fiyatı doğrulanamadı.")
    return OfferResult(
        title=title, price=price - discount, seller="MediaMarkt", url=url,
        source="basket-discount" if discount else "product-jsonld",
        price_note=f"{price} TL − {discount} TL (Sepette indirim)" if discount else "",
    )


class MediaMarktProvider(Provider):
    site = SITE_MEDIAMARKT
    notifies_stock_return = True

    def read(self, watch, ctx, outcome):
        url = watch.url
        product_code(url)
        response = ctx.session.get(url, headers=build_headers(url), timeout=ctx.timeout, allow_redirects=False)
        if response.status_code in {404, 410}:
            raise OutOfStockHermesError("MediaMarkt ürün sayfası kaldırılmış.", watch.name, url)
        if response.status_code != 200:
            raise HttpStatusHermesError(response.status_code, url)
        return [read_offer(cleaned_html(response), url)]
