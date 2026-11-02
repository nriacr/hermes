"""Hepsiburada cart price: the site's own campaign and coupon evaluation for one listing.

Coupon tags stay on a listing after the coupon ends, so they cannot say whether a
coupon applies. The product page asks the site which campaigns and coupons are
valid now; the same answer gives the price Nuri pays in the cart as a Premium member.
"""

import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from ...utils import format_tl
from .common import _embedded_text
from ..base import soup_from_html

EVALUATE_PATH = "api/v1/withoutAffordability"
EVALUATE_HEADERS = {
    "Content-Type": "application/json",
    "x-gotham_is_include_premium_clubs": "true",
    "x-gotham_is_include_payment_campaigns": "true",
    "x-gotham_is_enabled_next_eligible_campaign": "true",
    "x-gotham_is_enabled_evaluate_coupon": "true",
    "x-gotham_App-Key": "All",
}
# A listing without any of these tags has no coupon or cart campaign to evaluate.
CAMPAIGN_TAG_MARKERS = ("kupon", "premium", "sepet", "indirim")


def _first(pattern: str, text: str) -> str:
    match = re.search(pattern, text)
    return match.group(1) if match else ""


def page_product(html: str) -> Optional[dict]:
    """The page fields the evaluation needs; None when the page lacks them."""
    text = _embedded_text(soup_from_html(html))
    product_id = _first(r'"productId"\s*:\s*"([0-9A-Za-z]+)"', text)
    definition_id = _first(r'"definitionId"\s*:\s*"?(\d+)', text)
    definition_name = _first(r'"definitionName"\s*:\s*"([^"]+)"', text)
    categories = _first(r'"product_category_ids"\s*:\s*\[([^\]]*)\]', text)
    category_ids = [int(item) for item in re.findall(r"\d+", categories)]
    if not (product_id and definition_id and definition_name and category_ids):
        return None
    return {
        "productId": product_id,
        "definitionId": definition_id,
        "definitionName": definition_name,
        "rootCategoryList": category_ids,
    }


def listing_tags(listing: dict) -> list[str]:
    return [tag.strip() for tag in str(listing.get("paymentTag") or "").split(",") if tag.strip()]


def needs_evaluation(listing: dict) -> bool:
    return any(marker in tag.lower() for tag in listing_tags(listing) for marker in CAMPAIGN_TAG_MARKERS)


def _listing_price(listing: dict) -> Optional[Decimal]:
    price = listing.get("price")
    value = price.get("value") if isinstance(price, dict) else price
    try:
        return Decimal(str(value)) if value not in (None, "") else None
    except Exception:  # noqa: BLE001
        return None


def evaluation_payload(listing: dict, product: dict, sku: str) -> Optional[dict]:
    price = _listing_price(listing)
    if price is None or not listing.get("listingId") or not listing.get("merchantId"):
        return None
    return {
        "product": {
            "productTags": listing_tags(listing),
            "finalPrice": float(price),
            "finalPriceOnSale": float(price),
            "sku": sku,
            "listingId": listing["listingId"],
            "merchantId": listing["merchantId"],
            "campaignIds": [],
            **product,
        }
    }


def _decimal(value: Any) -> Optional[Decimal]:
    try:
        number = Decimal(str(value))
    except Exception:  # noqa: BLE001
        return None
    return number if number > 0 else None


def _active(coupon: dict, now: datetime) -> bool:
    end = str(coupon.get("endDateTime") or "")
    if not end:
        return True
    try:
        return datetime.fromisoformat(end) > now
    except ValueError:
        return True


def cart_price(response: Any, listing: dict, now: Optional[datetime] = None) -> tuple[Optional[Decimal], str]:
    """(price in the cart for a Premium member, how it was reached); (None, reason) when unknown."""
    base = _listing_price(listing)
    try:
        product = response["data"]["result"]["product"]
        promo = (product.get("promoData") or {}).get("data") or {}
    except (KeyError, TypeError, AttributeError):
        return None, "site kampanya cevabı okunamadı"
    if base is None:
        return None, "liste fiyatı yok"
    steps = [f"liste {format_tl(base, with_currency=True)}"]
    evaluated = promo.get("campaignEvaluateResult") or {}
    premium = _decimal((evaluated.get("evaluateAsPremiumResult") or {}).get("discountedPrice"))
    if premium is not None and premium < base:
        steps.append(f"site Premium hesabı {format_tl(premium, with_currency=True)}")
        return premium, " → ".join(steps)
    price = base
    campaign = _decimal((evaluated.get("evaluateResult") or {}).get("discountedPrice"))
    if campaign is None:
        campaign = _decimal((product.get("priceData") or {}).get("discountedPrice"))
    if campaign is not None and campaign < price:
        price = campaign
        steps.append(f"sepet kampanyası {format_tl(price, with_currency=True)}")
    now = now or datetime.now(timezone.utc)
    usable = []
    for coupon in promo.get("coupons") or []:
        if not isinstance(coupon, dict) or not coupon.get("isPremium") or not _active(coupon, now):
            continue
        discount = _decimal(coupon.get("discount"))
        limit = _decimal(coupon.get("conditionAmount")) or Decimal("0")
        if discount is not None and price >= limit:
            usable.append((limit, discount, str(coupon.get("label") or coupon.get("name") or "kupon")))
    if usable:
        limit, discount, label = max(usable, key=lambda item: (item[0], item[1]))
        price -= discount
        steps.append(f"Premium kuponu −{format_tl(discount, with_currency=True)} (alt limit {format_tl(limit, with_currency=True)}, {label})")
    steps.append(f"sepette {format_tl(price, with_currency=True)}")
    return price, " → ".join(steps)


def payload_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)
