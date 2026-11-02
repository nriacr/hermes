"""Trendyol: the product page's own price data, the Plus price and the product's coupons.

The page carries the product as ``window["__envoy__SHARED_PROPS"]``: the winner
listing's prices (``discountedPrice`` is payable, ``tyPlusCouponApplicablePrice``
the Plus member's price) and every size with its stock. Coupons are not on the
page; the site's coupon endpoint lists them with their lower limit and cap.
"""

import json
import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from ..constants import SITE_TRENDYOL
from ..errors import HermesError, OutOfStockHermesError, PriceUnavailableHermesError
from ..logging_utils import log
from ..models import OfferResult
from ..utils import build_headers, format_tl as whole_tl, parse_decimal
from .base import Provider, size_preview
from .http import fetch_with_retries, read_site_html
from .size_availability import size_matches

# Nuri is a Trendyol Plus member: Plus prices and Plus coupons are his payable prices.
PLUS_MEMBER = True
# The coupon endpoint is behind Cloudflare bot protection and refuses Hermes (403).
# Nuri 2026-11-02: prices stay without coupon and no coupon request is sent.
COUPONS_ENABLED = False
SHARED_PROPS = re.compile(r'window\["__envoy__SHARED_PROPS"\]\s*=\s*')
COUPON_URL = ("https://apigw.trendyol.com/discovery-storefront-trproductgw-service/api/coupons/{}/search"
              "?storefrontId=1&culture=tr-TR&channelId=1")
CENT = Decimal("0.01")


def format_tl(value: Decimal, with_currency: bool = True) -> str:
    """1.642,55 TL; whole amounts without kuruş (1.799 TL)."""
    value = Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)
    cents = int(value * 100) % 100
    return whole_tl(value, with_currency=False) + (f",{cents:02d}" if cents else "") + (" TL" if with_currency else "")


def page_product(html: str) -> dict:
    match = SHARED_PROPS.search(html)
    if not match:
        return {}
    try:
        payload, _ = json.JSONDecoder().raw_decode(html[match.end():])
    except json.JSONDecodeError:
        return {}
    product = payload.get("product") if isinstance(payload, dict) else None
    return product if isinstance(product, dict) else {}


def _amount(value: Any) -> Optional[Decimal]:
    if isinstance(value, dict):
        value = value.get("value")
    if value in (None, ""):
        return None
    try:
        return parse_decimal(str(value))
    except HermesError:
        return None


def _title(product: dict) -> str:
    brand = product.get("brand")
    brand = str((brand.get("name") if isinstance(brand, dict) else brand) or "").strip()
    name = str(product.get("name") or "").strip()
    color = str((product.get("slicingAttributes") or {}).get("DsmColor") or "").strip()
    title = name if not brand or name.lower().startswith(brand.lower()) else f"{brand} {name}"
    return " / ".join(part for part in (title, color) if part)


def page_prices(product: dict) -> tuple[Decimal, Decimal, str]:
    """(payable price, base the coupon applies to, how the price was reached)."""
    winner = (product.get("merchantListing") or {}).get("winnerVariant") or {}
    prices = winner.get("price") or {}
    if prices.get("currency", "TRY") != "TRY":
        raise PriceUnavailableHermesError("Trendyol fiyatı TL değil.")
    price = _amount(prices.get("discountedPrice")) or _amount(prices.get("sellingPrice"))
    if price is None:
        raise PriceUnavailableHermesError("Trendyol ürününe ait fiyat doğrulanamadı.")
    note = f"sayfa fiyatı {format_tl(price, with_currency=True)}"
    plus = _amount(prices.get("tyPlusCouponApplicablePrice"))
    if PLUS_MEMBER and winner.get("isTyPlusEligible") and plus is not None and plus < price:
        return plus, plus, f"{note}, Plus'a özel {format_tl(plus, with_currency=True)}"
    return price, _amount(prices.get("couponApplicablePrice")) or price, note


def coupon_discount(coupon: dict, base: Decimal) -> Decimal:
    """What one coupon takes off ``base``; 0 when its lower limit is not met or it is not usable."""
    if not isinstance(coupon, dict) or (coupon.get("isTyPlusCoupon") and not PLUS_MEMBER):
        return Decimal(0)
    discount = _amount(coupon.get("discount"))
    lower = _amount(coupon.get("lowerLimit")) or Decimal(0)
    if discount is None or discount <= 0 or base < lower:
        return Decimal(0)
    kind = str(coupon.get("discountType") or "").upper()
    if kind == "PERCENTAGE":
        value = (base * discount / 100).quantize(CENT, rounding=ROUND_HALF_UP)
    elif kind == "AMOUNT":
        value = discount
    else:
        return Decimal(0)
    cap = _amount(coupon.get("maxDiscountAmount"))
    return min(value, cap, base) if cap else min(value, base)


def coupon_text(coupon: dict) -> str:
    discount = _amount(coupon.get("discount")) or Decimal(0)
    text = f"%{discount.normalize():f}" if str(coupon.get("discountType")).upper() == "PERCENTAGE" \
        else format_tl(discount, with_currency=True)
    lower = _amount(coupon.get("lowerLimit")) or Decimal(0)
    cap = _amount(coupon.get("maxDiscountAmount"))
    return (f"{text} kupon (alt limit {format_tl(lower, with_currency=True)}"
            + (f", en fazla {format_tl(cap, with_currency=True)}" if cap else "") + ")")


def apply_best_coupon(price: Decimal, base: Decimal, coupons: list, note: str) -> tuple[Decimal, str]:
    """One coupon per order: the one taking the most off the coupon base."""
    best = max(coupons, key=lambda coupon: coupon_discount(coupon, base), default=None)
    discount = coupon_discount(best, base) if best else Decimal(0)
    if discount <= 0:
        return price, note + (", kupon alt limiti tutmuyor" if coupons else "")
    final = max(price - discount, Decimal(0))
    return final, f"{note}, {coupon_text(best)} -{format_tl(discount, with_currency=True)} = {format_tl(final, with_currency=True)}"


def page_sizes(product: dict) -> list[tuple[str, bool]]:
    return [(str(item.get("value") or "").strip(), bool(item.get("inStock")))
            for item in product.get("variants") or [] if isinstance(item, dict) and item.get("value")]


def extract_offers(html: str, url: str, size: str = "", coupons: Optional[list] = None) -> list[OfferResult]:
    product = page_product(html)
    if not product:
        raise PriceUnavailableHermesError("Trendyol ürününe ait fiyat doğrulanamadı.")
    title = _title(product)
    sizes = page_sizes(product)
    requested = str(size or "").strip()
    if requested:
        matching = [(name, stock) for name, stock in sizes if size_matches(name, requested)]
        if not matching:
            raise OutOfStockHermesError(f"Trendyol beden bulunamadı: {requested}", title, url)
        if not any(stock for _, stock in matching):
            raise OutOfStockHermesError(f"Trendyol beden stokta değil: {requested}", title, url)
        title = f"{title} / {matching[0][0]}"
    elif product.get("inStock") is False or (sizes and not any(stock for _, stock in sizes)):
        raise OutOfStockHermesError("Ürün stokta yok.", title, url)
    price, base, note = page_prices(product)
    if coupons is not None:
        price, note = apply_best_coupon(price, base, coupons, note)
    return [OfferResult(title=title, price=price, seller="Trendyol", url=url, source="trendyol-page", price_note=note)]


def coupon_headers(url: str) -> dict:
    headers = build_headers(url)
    headers.update({"Accept": "application/json, text/plain, */*", "Content-Type": "application/json",
                    "Origin": "https://www.trendyol.com", "Referer": url,
                    "x-web-req-source": "StorefrontProductGateway"})
    return headers


class TrendyolProvider(Provider):
    site = SITE_TRENDYOL

    card_options = ("size",)

    def coupons(self, product: dict, url: str, ctx) -> Optional[list]:
        """The product's coupons, or None when they cannot be read (the price then stays without coupon)."""
        content_id = product.get("id")
        if not COUPONS_ENABLED or not content_id:
            return None
        try:
            response = ctx.session.get(COUPON_URL.format(content_id), headers=coupon_headers(url), timeout=ctx.timeout)
            response.raise_for_status()
            data = response.json()
        except Exception as exc:  # noqa: BLE001 - a missing coupon must not drop the price
            log(f"Trendyol kuponu okunamadı, kuponsuz fiyat kullanılıyor: {url} | {exc}")
            return None
        result = data.get("result") if isinstance(data, dict) and data.get("isSuccess") else None
        if not isinstance(result, list):
            log(f"Trendyol kuponu okunamadı, kuponsuz fiyat kullanılıyor: {url} | beklenmeyen cevap")
            return None
        return [item for item in result if isinstance(item, dict)]

    def read_page(self, url, ctx) -> tuple[str, dict]:
        html = read_site_html(fetch_with_retries(ctx.session, url, ctx.timeout), "Trendyol")
        return html, page_product(html)

    def inspect(self, url, ctx):
        html, product = self.read_page(url, ctx)
        offer = extract_offers(html, url, coupons=self.coupons(product, url, ctx))[0]
        return size_preview(self.site, offer.title, offer.price, page_sizes(product))

    def read(self, watch, ctx, outcome):
        html, product = self.read_page(watch.url, ctx)
        coupons = self.coupons(product, watch.url, ctx) if product else None
        offers = extract_offers(html, watch.url, size=watch.size, coupons=coupons)
        log(f"Trendyol fiyat hesabı: {offers[0].title} | {offers[0].price_note}")
        return offers
