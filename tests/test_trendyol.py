import json
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import support  # noqa: F401 - puts the add-on on the import path

from hermes.errors import OutOfStockHermesError, PriceUnavailableHermesError
from hermes.models import WatchRule
from hermes.providers.base import WatchRead
from hermes.providers.trendyol import TrendyolProvider, extract_offers

URL = "https://www.trendyol.com/h-m/urun-p-1"


def price(discounted, original=None, plus=None, coupon_base=None):
    value = lambda amount: {"value": amount, "text": f"{amount} TL"}  # noqa: E731
    return {"currency": "TRY", "discountedPrice": value(discounted), "sellingPrice": value(original or discounted),
            "originalPrice": value(original or discounted), "couponApplicablePrice": value(coupon_base or discounted),
            "tyPlusCouponApplicablePrice": value(plus or discounted)}


def page(prices, sizes=(), plus_eligible=False, color="Beyaz", unit_info=""):
    product = {"id": 1, "name": "5'li Kolsuz Üst", "brand": {"name": "H&M"}, "inStock": True,
               "slicingAttributes": {"DsmColor": color},
               "merchantListing": {"winnerVariant": {"price": prices, "isTyPlusEligible": plus_eligible,
                                                     "unitInfo": unit_info}},
               "variants": [{"value": name, "inStock": stock, "price": prices["discountedPrice"]} for name, stock in sizes]}
    return ('<html><head><script type="application/ld+json">{"@type":"ProductGroup","name":"x"}</script></head>'
            f'<body><script>window["__envoy__SHARED_PROPS"]={json.dumps({"product": product})}</script></body></html>')


def coupon(discount, kind="AMOUNT", lower=0, cap=None, plus=False):
    return {"discount": discount, "discountType": kind, "lowerLimit": lower, "maxDiscountAmount": cap, "isTyPlusCoupon": plus}


class TrendyolPriceTests(unittest.TestCase):
    def test_plain_price_with_sizes(self):
        html = page(price(949.99), [("2-4 Yaş", True), ("4-6 Yaş", True), ("6-8 Yaş", True), ("8-10 Yaş", True)])
        offer = extract_offers(html, URL, coupons=[])[0]
        self.assertEqual(offer.price, Decimal("949.99"))
        self.assertEqual(offer.title, "H&M 5'li Kolsuz Üst / Beyaz")

    def test_discounted_price_not_list_price(self):
        offer = extract_offers(page(price(999.99, original=1199)), URL)[0]
        self.assertEqual(offer.price, Decimal("999.99"))

    def test_unit_price_is_ignored(self):
        offer = extract_offers(page(price(649.99, original=799.99), unit_info="325 TL / Adet"), URL)[0]
        self.assertEqual(offer.price, Decimal("649.99"))

    def test_percentage_coupon_respects_cap(self):
        offer = extract_offers(page(price(1799)), URL, coupons=[coupon(15, "PERCENTAGE", cap=150)])[0]
        self.assertEqual(offer.price, Decimal("1649"))
        self.assertIn("en fazla 150 TL", offer.price_note)

    def test_percentage_coupon_without_cap(self):
        offer = extract_offers(page(price(1799)), URL, coupons=[coupon(15, "PERCENTAGE")])[0]
        self.assertEqual(offer.price, Decimal("1529.15"))

    def test_plus_price_and_best_coupon_meeting_lower_limit(self):
        html = page(price(1729, original=1799, plus=1642.55), plus_eligible=True)
        offer = extract_offers(html, URL, coupons=[coupon(150, lower=1650, plus=True), coupon(40, lower=730)])[0]
        self.assertEqual(offer.price, Decimal("1602.55"))
        self.assertIn("Plus'a özel 1.642,55 TL", offer.price_note)

    def test_plus_price_without_coupons(self):
        html = page(price(1729, original=1799, plus=1642.55), plus_eligible=True)
        self.assertEqual(extract_offers(html, URL)[0].price, Decimal("1642.55"))

    def test_coupon_below_lower_limit_is_skipped(self):
        offer = extract_offers(page(price(500)), URL, coupons=[coupon(50, lower=650)])[0]
        self.assertEqual(offer.price, Decimal("500"))

    def test_requested_size_stock(self):
        html = page(price(999.99), [("XS", False), ("M", False), ("L", True), ("XL", True)])
        self.assertEqual(extract_offers(html, URL, size="L")[0].title, "H&M 5'li Kolsuz Üst / Beyaz / L")
        with self.assertRaises(OutOfStockHermesError):
            extract_offers(html, URL, size="M")
        with self.assertRaises(OutOfStockHermesError):
            extract_offers(html, URL, size="3XL")

    def test_all_sizes_out_of_stock(self):
        with self.assertRaises(OutOfStockHermesError):
            extract_offers(page(price(10), [("S", False)]), URL)

    def test_page_without_product_data(self):
        with self.assertRaises(PriceUnavailableHermesError):
            extract_offers("<html><body>Trendyol</body></html>", URL)


class TrendyolProviderTests(unittest.TestCase):
    def ctx(self, html, coupon_response):
        session = Mock()
        page_response = SimpleNamespace(status_code=200, headers={"content-type": "text/html; charset=utf-8"},
                                        text=html, content=html.encode(), encoding="utf-8", raise_for_status=lambda: None)
        session.get.side_effect = [page_response, coupon_response]
        return SimpleNamespace(session=session, timeout=5)

    def test_read_applies_coupon_from_endpoint(self):
        body = {"isSuccess": True, "result": [coupon(15, "PERCENTAGE", cap=150)]}
        response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)
        ctx = self.ctx(page(price(1799)), response)
        watch = WatchRule(name="Nevresim", site="trendyol", url=URL, target_price=Decimal(2000))
        offers = TrendyolProvider().read(watch, ctx, WatchRead())
        self.assertEqual(offers[0].price, Decimal("1649"))
        self.assertIn("/api/coupons/1/search", ctx.session.get.call_args_list[1].args[0])

    def test_blocked_coupon_endpoint_keeps_page_price(self):
        def blocked():
            raise RuntimeError("403")
        ctx = self.ctx(page(price(1799)), SimpleNamespace(raise_for_status=blocked))
        watch = WatchRule(name="Nevresim", site="trendyol", url=URL, target_price=Decimal(2000))
        self.assertEqual(TrendyolProvider().read(watch, ctx, WatchRead())[0].price, Decimal("1799"))


if __name__ == "__main__":
    unittest.main()
