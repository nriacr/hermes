import json
from decimal import Decimal
from typing import Any, Dict, List

from ..constants import SITE_TOGG
from ..errors import HermesError, HttpStatusHermesError, OutOfStockHermesError
from ..models import OfferResult
from ..utils import normalize_offer_text
from .base import Provider

# The configurator page loads its model list from this public endpoint. A model that can be
# configured appears in the list; a model that is not on sale yet is simply absent.
TOGG_PRODUCTS_URL = "https://bff.dfs.togg.cloud/smart-device-products/TUR/getSmartDeviceProducts/v1?collectionHandle=t10x-configurator"
TOGG_ORIGIN = "https://configurator.togg.com.tr"


def togg_headers() -> Dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Origin": TOGG_ORIGIN,
        "Referer": TOGG_ORIGIN + "/",
    }


def parse_products(payload: str) -> List[dict]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise HermesError("Togg ürün listesi okunamadı.") from exc
    products = data.get("products") if isinstance(data, dict) else None
    if not isinstance(products, list):
        raise HermesError("Togg ürün listesi beklenen biçimde gelmedi.")
    return [product for product in products if isinstance(product, dict) and str(product.get("name") or "").strip()]


def _decimal(raw: Any) -> Decimal | None:
    """The list carries plain JSON numbers (dot decimals), unlike the Turkish prices of a page."""
    try:
        return Decimal(str(raw))
    except (ArithmeticError, ValueError):
        return None


def product_price(product: dict) -> Decimal:
    """The tax-inclusive price in the model's own ÖTV bracket (as the page lists it); 0 when none is shown."""
    price = product.get("price")
    price = price if isinstance(price, dict) else {}
    variants = product.get("variants")
    first = variants[0] if isinstance(variants, list) and variants and isinstance(variants[0], dict) else {}
    brackets = {key: value.get("with_all_taxes") for key, value in price.items()
                if key.startswith("sct_") and isinstance(value, dict)}
    own = brackets.get(f"sct_{first.get('sct_rate')}")
    candidates = [own] if own not in (None, "") else list(brackets.values())
    prices = [value for value in map(_decimal, candidates) if value is not None]
    return min(prices) if prices else Decimal(0)


def extract_offers(payload: str, model: str, source_url: str = "") -> List[OfferResult]:
    """The model's offer when the list shows it; a model missing from the list is a stock state."""
    wanted = normalize_offer_text(model)
    if not wanted:
        raise HermesError("Togg kartında model adı (name alanı) zorunlu, örneğin: T10X V2 RWD Uzun Menzil.")
    products = parse_products(payload)
    matches = [product for product in products if wanted in normalize_offer_text(product["name"])]
    if not matches:
        raise OutOfStockHermesError(f"Togg modeli listede yok: {model}", model, source_url)
    return [OfferResult(title=str(product["name"]).strip(), price=product_price(product), seller="Togg", url=source_url or None)
            for product in matches]


class ToggProvider(Provider):
    site = SITE_TOGG
    notifies_stock_return = True

    def __init__(self) -> None:
        self.payload: str | None = None

    def begin_cycle(self) -> None:
        self.payload = None

    def _products_payload(self, ctx: Any) -> str:
        """One request per cycle serves every Togg card."""
        if self.payload is None:
            response = ctx.session.get(TOGG_PRODUCTS_URL, headers=togg_headers(), timeout=ctx.timeout)
            if response.status_code >= 400:
                raise HttpStatusHermesError(response.status_code, TOGG_PRODUCTS_URL)
            self.payload = response.text
        return self.payload

    def read(self, watch, ctx, outcome):
        return extract_offers(self._products_payload(ctx), watch.name, source_url=watch.url)
