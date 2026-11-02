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
from urllib.parse import urlsplit

from ..constants import SITE_TRENDYOL
from ..errors import HermesError, OutOfStockHermesError, PriceUnavailableHermesError
from ..logging_utils import log
from ..models import OfferResult, ProductPreview, VariantPreview
from ..traffic import count_curl
from ..utils import build_headers, format_tl as whole_tl, parse_decimal
from .base import Provider, outside_variant_filter
from .http import curl_requests, fetch_with_retries, read_site_html
from .size_availability import size_matches

# Nuri is a Trendyol Plus member: Plus prices and Plus coupons are his payable prices.
PLUS_MEMBER = True
# The coupon endpoint is behind Cloudflare bot protection and refuses Hermes (403).
# Nuri 2026-11-02: prices stay without coupon and no coupon request is sent.
COUPONS_ENABLED = False
# Explicit product inspection probes coupon sources; automated watches stay unchanged.
COUPON_DIAGNOSTICS = True
SHARED_PROPS = re.compile(r'window\["__envoy__SHARED_PROPS"\]\s*=\s*')
COUPON_URL = ("https://apigw.trendyol.com/discovery-storefront-trproductgw-service/api/coupons/{}/search"
              "?storefrontId=1&culture=tr-TR&channelId=1")
CENT = Decimal("0.01")


def format_tl(value: Decimal, with_currency: bool = True) -> str:
    """1.642,55 TL; whole amounts without kuruş (1.799 TL)."""
    value = Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)
    cents = int(value * 100) % 100
    return whole_tl(value, with_currency=False) + (f",{cents:02d}" if cents else "") + (" TL" if with_currency else "")


def page_payload(html: str, name: str = "__envoy__SHARED_PROPS") -> Any:
    pattern = SHARED_PROPS if name == "__envoy__SHARED_PROPS" else re.compile(r'window\["' + re.escape(name) + r'"\]\s*=\s*')
    match = pattern.search(html)
    if not match:
        return {}
    try:
        payload, _ = json.JSONDecoder().raw_decode(html[match.end():])
    except json.JSONDecodeError:
        return {}
    allowed = (dict,) if name == "__envoy__SHARED_PROPS" else (dict, list)
    return payload if isinstance(payload, allowed) else {}


def embedded_coupon_fields(value, path="", depth=0):
    """Locate coupon/promotion branches for source inspection without logging account data."""
    if depth > 12:
        return {}
    found = {}
    if isinstance(value, dict):
        for key, child in value.items():
            lower = key.lower()
            if any(term in lower for term in ("token", "cookie", "session", "user", "customer", "header", "secret", "authorization")):
                continue
            child_path = f"{path}.{key}" if path else key
            if any(term in lower for term in ("coupon", "collectable", "promotion")):
                # Recursively keep only public scalar fields; strip credentials at every depth.
                found[child_path] = public_coupon_fields(child)
            else:
                found.update(embedded_coupon_fields(child, child_path, depth + 1))
    elif isinstance(value, list):
        for index, child in enumerate(value[:60]):
            found.update(embedded_coupon_fields(child, f"{path}[{index}]", depth + 1))
    return found


def public_coupon_fields(value, depth=0):
    if depth > 8:
        return "…"
    if isinstance(value, dict):
        return {key: public_coupon_fields(child, depth + 1) for key, child in value.items()
                if not any(term in key.lower() for term in ("token", "cookie", "session", "user", "customer", "header", "secret", "authorization"))}
    if isinstance(value, list):
        return [public_coupon_fields(child, depth + 1) for child in value[:60]]
    return value[:1000] if isinstance(value, str) else value


def page_product(html: str) -> dict:
    product = page_payload(html).get("product")
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


def size_price(product: dict, variant: dict) -> tuple[Decimal, Decimal, str]:
    """Use the size's own price; the selected size may also carry a Plus offer."""
    value = _amount(variant.get("price"))
    if value is None:
        raise PriceUnavailableHermesError(f"Trendyol beden fiyatı doğrulanamadı: {variant.get('value', '')}")
    if variant.get("isSelected"):
        return page_prices(product)
    return value, value, f"beden fiyatı {format_tl(value)}"


def family_urls(html: str, url: str) -> list[str]:
    """Candidate product links with the same slug; membership must be verified on each page."""
    path = urlsplit(url).path
    match = re.fullmatch(r"(.+)-p-\d+", path)
    if not match:
        return []
    paths = re.findall(re.escape(match[1]) + r"-p-\d+(?!\d)", html.replace('\\/', '/'))
    return list(dict.fromkeys("https://www.trendyol.com" + path for path in paths))


def extract_offers(html: str, url: str, size: str = "", coupons: Optional[list] = None) -> list[OfferResult]:
    product = page_product(html)
    if not product:
        raise PriceUnavailableHermesError("Trendyol ürününe ait fiyat doğrulanamadı.")
    title = _title(product)
    sizes = page_sizes(product)
    requested = str(size or "").strip()
    if requested:
        matching = [item for item in product.get("variants") or []
                    if isinstance(item, dict) and size_matches(item.get("value"), requested)]
        if not matching:
            raise OutOfStockHermesError(f"Trendyol beden bulunamadı: {requested}", title, url)
        available = [item for item in matching if item.get("inStock")]
        if not available:
            raise OutOfStockHermesError(f"Trendyol beden stokta değil: {requested}", title, url)
        variant = available[0]
        title = f"{title} / {variant['value']}"
        price, base, note = size_price(product, variant)
    elif product.get("inStock") is False or (sizes and not any(stock for _, stock in sizes)):
        raise OutOfStockHermesError("Ürün stokta yok.", title, url)
    if not requested:
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

    card_options = ("size", "variant_filter")

    def coupons(self, product: dict, url: str, ctx, diagnostic: bool = False) -> Optional[list]:
        """The product's coupons, or None when they cannot be read (the price then stays without coupon)."""
        content_id = product.get("id")
        if not (COUPONS_ENABLED or diagnostic) or not content_id:
            return None
        try:
            response = ctx.session.get(COUPON_URL.format(content_id), headers=coupon_headers(url), timeout=ctx.timeout)
            if diagnostic:
                log(f"Trendyol kupon kaynağı: ürün={content_id} | istemci=requests | HTTP={response.status_code}")
            if diagnostic and response.status_code == 403 and curl_requests is not None:
                # The same native HTTP library already used by Hermes's other providers.
                ctx.pace("Trendyol kupon kaynağı")
                response = count_curl(curl_requests.get(
                    COUPON_URL.format(content_id), headers=coupon_headers(url), timeout=ctx.timeout,
                    impersonate="chrome124", allow_redirects=False,
                ))
                log(f"Trendyol kupon kaynağı: ürün={content_id} | istemci=curl | HTTP={response.status_code}")
            response.raise_for_status()
            data = response.json()
        except Exception as exc:  # noqa: BLE001 - a missing coupon must not drop the price
            log(f"Trendyol kuponu okunamadı, kuponsuz fiyat kullanılıyor: {url} | {exc}")
            return None
        result = data.get("result") if isinstance(data, dict) and data.get("isSuccess") else None
        if not isinstance(result, list):
            log(f"Trendyol kuponu okunamadı, kuponsuz fiyat kullanılıyor: {url} | beklenmeyen cevap")
            return None
        if diagnostic:
            # The unauthenticated endpoint's public coupon rules, not headers or sessions.
            log(f"Trendyol kupon kuralları: ürün={content_id} | " + json.dumps(public_coupon_fields(result), ensure_ascii=False, default=str)[:12000])
        return [item for item in result if isinstance(item, dict)]

    def inspect_coupon_sources(self, html, product, url, ctx):
        listing = product.get("merchantListing") or {}
        winner = listing.get("winnerVariant") or {}
        merchant = listing.get("merchant") or winner.get("merchant") or {}
        merchant_id = merchant.get("id") if isinstance(merchant, dict) else None
        log("Trendyol fiyat kaynağı: " + json.dumps({"id": product.get("id"),
            "price": winner.get("price"), "isTyPlusEligible": winner.get("isTyPlusEligible"),
            "merchant_id": merchant_id or listing.get("merchantId") or winner.get("merchantId"),
            "listing_keys": list(listing), "winner_keys": list(winner)}, ensure_ascii=False, default=str))
        modules = list(dict.fromkeys(re.findall(r'window\["(__envoy__[^"\\]+)"\]\s*=', html)))
        log(f"Trendyol sayfa modülleri: ürün={product.get('id')} | " + json.dumps(modules))
        # Product-scoped embedded coupon/promotion fields only; never dump the page's user/session data.
        fields = {key: value for key, value in product.items()
                  if any(term in key.lower() for term in ("coupon", "campaign", "promotion"))}
        fields["listing"] = {key: value for key, value in listing.items()
                            if any(term in key.lower() for term in ("coupon", "campaign", "promotion"))}
        fields["winner"] = {key: value for key, value in winner.items()
                            if any(term in key.lower() for term in ("coupon", "campaign", "promotion", "collectable"))}
        log(f"Trendyol gömülü kupon verisi: ürün={product.get('id')} | " + json.dumps(fields, ensure_ascii=False, default=str)[:12000])
        props = page_payload(html, "__envoy__PROPS")
        log(f"Trendyol ek veri bloğu: ürün={product.get('id')} | " + json.dumps({
            "type": type(props).__name__, "keys": list(props) if isinstance(props, dict) else [],
            "coupon_fields": embedded_coupon_fields(props)}, ensure_ascii=False, default=str)[:20000])
        # Both clients returned 403 on 2026-11-03; do not repeat blocked requests on each inspection.

    def read_page(self, url, ctx) -> tuple[str, dict]:
        html = read_site_html(fetch_with_retries(ctx.session, url, ctx.timeout), "Trendyol")
        return html, page_product(html)

    def family_pages(self, url, ctx, include_colors=True):
        html, product = self.read_page(url, ctx)
        if not product:
            raise PriceUnavailableHermesError("Trendyol ürününe ait fiyat doğrulanamadı.")
        yield url, html, product
        group = product.get("productGroupId")
        if not include_colors or not group:
            return
        seen = {str(product.get("id"))}
        candidates = family_urls(html, url)
        log(f"Trendyol renk bağlantıları: ürün={product.get('id')} | aile={group} | " + json.dumps(candidates))
        if len(candidates) > 60:
            raise PriceUnavailableHermesError("Trendyol renk bağlantıları güvenli tarama sınırını aşıyor.")
        for candidate in candidates:
            content_id = urlsplit(candidate).path.rsplit('-p-', 1)[-1]
            if content_id in seen:
                continue
            seen.add(content_id)
            ctx.pace("Trendyol renk sayfası")
            color_html, color = self.read_page(candidate, ctx)
            if not color:
                raise PriceUnavailableHermesError("Trendyol renk sayfası doğrulanamadı.")
            if str(color.get("id")) != content_id or color.get("productGroupId") != group:
                continue
            yield candidate, color_html, color

    def inspect(self, url, ctx):
        variants, colors, sizes, title = [], [], [], ""
        selected_color = ""
        for color_url, html, product in self.family_pages(url, ctx):
            color = str((product.get("slicingAttributes") or {}).get("DsmColor") or "").strip()
            if not title:
                title, selected_color = _title(product), color
                if COUPON_DIAGNOSTICS:
                    self.inspect_coupon_sources(html, product, color_url, ctx)
            if color and color not in colors:
                colors.append(color)
            entries = [item for item in product.get("variants") or [] if isinstance(item, dict) and item.get("value")]
            if not entries:
                entries = [{"value": "", "inStock": product.get("inStock", True), "isSelected": True}]
            for item in entries:
                size = str(item["value"])
                available = bool(item.get("inStock")) and product.get("inStock") is not False
                if size and size not in sizes:
                    sizes.append(size)
                value = None
                if available or item.get("price") is not None:
                    value = size_price(product, item)[0] if size else page_prices(product)[0]
                attributes = {key: value for key, value in (("Renk", color), ("Beden", size)) if value}
                variants.append(VariantPreview(" / ".join(part for part in (color, size) if part) or title,
                                               value, available, attributes))
            log(f"Trendyol renk okundu: {product.get('id')} | {color} | " + json.dumps([
                {"beden": item["value"], "stok": item.get("inStock"), "fiyat": item.get("price")}
                for item in entries], ensure_ascii=False))
        dimensions = {key: values for key, values in (("Renk", colors), ("Beden", sizes)) if values}
        return ProductPreview(self.site, title, variants, dimensions,
                              {"Renk": [selected_color]} if selected_color else {})

    def keeps_offer(self, watch, offer):
        return not outside_variant_filter(watch, offer.title, whole_parts=True)

    def read(self, watch, ctx, outcome):
        offers = []
        for color_url, html, product in self.family_pages(watch.url, ctx, watch.include_variations or bool(watch.variant_filter)):
            coupons = self.coupons(product, color_url, ctx)
            try:
                current = extract_offers(html, color_url, size=watch.size, coupons=coupons)
            except OutOfStockHermesError as exc:
                if color_url == watch.url and not (watch.include_variations or watch.variant_filter):
                    raise
                outcome.unavailable.append({"product_title": exc.product_title or _title(product),
                                            "product_url": color_url, "reason": str(exc)})
                continue
            for offer in current:
                if self.keeps_offer(watch, offer):
                    log(f"Trendyol fiyat hesabı: {offer.title} | {offer.price_note}")
                    offers.append(offer)
        return offers
