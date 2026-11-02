import json
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

import support  # noqa: F401 - puts the add-on on the import path

from hermes.errors import OutOfStockHermesError, PriceUnavailableHermesError
from hermes.models import WatchRule
from hermes.providers.base import ReadContext, WatchRead
from hermes.providers import trendyol
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
    def test_inline_product_modules_are_found_before_global_libraries(self):
        html = '<script src="https://cdn.dsmcdn.com/react.js"></script>'
        html += '<script type="module">import("https://cdn.dsmcdn.com/pdp/coupon.mjs");</script>'
        html += '<script>let source="https://cdn.dsmcdn.com/pdp/product.js";</script>'
        urls = trendyol.client_source_urls(html, URL)
        self.assertEqual(urls[:2], ["https://cdn.dsmcdn.com/pdp/coupon.mjs", "https://cdn.dsmcdn.com/pdp/product.js"])

    def test_accessibility_telemetry_and_unresolved_templates_are_not_fetched(self):
        for url in ("https://cdn.dsmcdn.com/sfweb/eye-able/public/key/file.js",
                    "https://cdn.dsmcdn.com/ring/manager.js", "https://cdn.dsmcdn.com/${chunk}.js"):
            self.assertFalse(trendyol.allowed_source_asset(url))

    def test_contract_keeps_public_header_constants_but_not_authorization(self):
        code = 'api.get("/api/coupons/${contentId}/search");'
        code += 'const base="https://apigw.trendyol.com/discovery-example-service";'
        code += 'const h={"x-web-req-source":"ProductGateway","Authorization":"private"};'
        result = trendyol.coupon_client_contracts(code)
        self.assertEqual(result["candidate_methods"], ["get"])
        self.assertEqual(result["service_bases"], ["https://apigw.trendyol.com/discovery-example-service"])
        self.assertEqual(result["public_headers"], {"x-web-req-source": "ProductGateway"})
        self.assertNotIn("private", json.dumps(result))

    def test_service_function_names_and_boolean_flags_are_public_contract_data(self):
        self.assertEqual(trendyol.source_contract([["pdpCoupons", "fetchProduct", [True, False]]]),
                         [["pdpCoupons", "fetchProduct", [True, False]]])

    def test_client_source_allowlist_rejects_other_hosts_credentials_and_ports(self):
        self.assertTrue(trendyol.allowed_source_asset("https://cdn.dsmcdn.com/product.js?v=1"))
        for url in ("http://cdn.dsmcdn.com/a.js", "https://cdn.dsmcdn.com.evil/a.js",
                    "https://u:p@cdn.dsmcdn.com/a.js", "https://cdn.dsmcdn.com:8099/a.js",
                    "https://127.0.0.1/a.js", "https://cdn.dsmcdn.com/a.json"):
            with self.subTest(url=url):
                self.assertFalse(trendyol.allowed_source_asset(url))

    def test_client_contract_extracts_paths_and_names_without_executing_code(self):
        code = 'const url=`https://apigw.trendyol.com/api/coupons/${contentId}/search?merchantId=123`;'
        code += 'const h={"x-web-req-source":"ProductGateway","Authorization":"private-value"};globalThis.side_effect=true;'
        data = trendyol.coupon_client_contracts(code)
        self.assertEqual(data["paths"], ["https://apigw.trendyol.com/api/coupons/${contentId}/search"])
        self.assertEqual(data["candidate_parameters"], ["contentId", "merchantId"])
        self.assertEqual(data["header_names"], ["x-web-req-source", "Authorization"])
        self.assertNotIn("private-value", json.dumps(data))

    def test_service_contract_never_contains_query_or_account_values(self):
        data = {"get": {"url": "https://apigw.trendyol.com/coupons?contentId=123", "token": "private"},
                "user": {"name": "private"}, "headers": {"Cookie": "private"}}
        result = trendyol.source_contract(data)
        self.assertNotIn("private", json.dumps(result))
        self.assertEqual(result["get"]["url"]["query_keys"], ["contentId"])
        self.assertNotIn("123", json.dumps(result))

    def test_coupon_request_has_api_headers_not_navigation_headers(self):
        headers = trendyol.coupon_headers(URL)
        self.assertEqual(headers["Sec-Fetch-Mode"], "cors")
        self.assertEqual(headers["Sec-Fetch-Dest"], "empty")
        self.assertEqual(headers["Sec-Fetch-Site"], "same-site")
        self.assertEqual(headers["User-Agent"], trendyol.CHROME_USER_AGENT)
        self.assertNotIn("Upgrade-Insecure-Requests", headers)
        self.assertNotIn("Sec-Fetch-User", headers)

    def test_extra_page_props_share_the_json_parser(self):
        data = [{"coupon": {"discount": 15, "discountType": "PERCENTAGE"}}]
        html = 'window["__envoy__PROPS"] = ' + json.dumps(data)
        self.assertEqual(trendyol.page_payload(html, "__envoy__PROPS"), data)
        self.assertEqual(trendyol.page_product(html), {})

    def test_embedded_coupon_diagnostics_exclude_account_and_nested_credentials(self):
        data = {"user": {"coupon": "private"}, "module": {"coupons": [{"discount": 70,
                "headers": {"Authorization": "private"}, "token": "private", "lowerLimit": 1250}]}}
        found = trendyol.embedded_coupon_fields(data)
        self.assertEqual(found, {"module.coupons": [{"discount": 70, "lowerLimit": 1250}]})

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
    def test_source_diagnostic_failure_does_not_change_product_price(self):
        html = page(price(949.99), [("M", True)])
        provider = TrendyolProvider()
        provider.read_page = Mock(return_value=(html, trendyol.page_product(html)))
        provider.inspect_coupon_sources = Mock(side_effect=ValueError("invalid diagnostic data"))
        result = provider.inspect(URL, ReadContext(5, Mock()))
        self.assertEqual(result.variants[0].price, Decimal("949.99"))
        self.assertTrue(result.variants[0].in_stock)

    def test_static_source_discovery_caches_assets_and_closes_responses(self):
        source = "https://cdn.dsmcdn.com/product.js"
        child = "https://cdn.dsmcdn.com/coupon.chunk.js"
        code = ('const url="/api/coupons/${contentId}/search";const next="' + child + '";').encode()
        first = SimpleNamespace(status_code=200, iter_content=lambda _: [code], close=Mock())
        second = SimpleNamespace(status_code=200, iter_content=lambda _: [b'const path="/api/coupons/search";'], close=Mock())
        ctx = ReadContext(5, Mock(), pace=Mock())
        ctx.session.get.side_effect = [first, second]
        provider = TrendyolProvider()
        html = f'<script src="{source}"></script><script src="https://evil.example/price.js"></script>'
        provider.inspect_client_sources(html, URL, 1, ctx)
        provider.inspect_client_sources(html, URL, 1, ctx)
        self.assertEqual(ctx.session.get.call_count, 2)
        self.assertEqual([call.args[0] for call in ctx.session.get.call_args_list], [source, child])
        first.close.assert_called_once()
        second.close.assert_called_once()
        self.assertFalse(ctx.session.get.call_args.kwargs["allow_redirects"])

    def test_static_source_redirect_is_not_followed(self):
        response = SimpleNamespace(status_code=302, close=Mock())
        ctx = ReadContext(5, Mock())
        ctx.session.get.return_value = response
        TrendyolProvider().inspect_client_sources('<script src="https://cdn.dsmcdn.com/a.js"></script>', URL, 1, ctx)
        ctx.session.get.assert_called_once()
        response.close.assert_called_once()

    def ctx(self, html, coupon_response):
        session = Mock()
        page_response = SimpleNamespace(status_code=200, headers={"content-type": "text/html; charset=utf-8"},
                                        text=html, content=html.encode(), encoding="utf-8", raise_for_status=lambda: None)
        session.get.side_effect = [page_response, coupon_response]
        return SimpleNamespace(session=session, timeout=5)

    def test_coupons_disabled_sends_no_coupon_request(self):
        ctx = self.ctx(page(price(1799)), None)
        watch = WatchRule(name="Nevresim", site="trendyol", url=URL, target_price=Decimal(2000))
        offers = TrendyolProvider().read(watch, ctx, WatchRead())
        self.assertEqual(offers[0].price, Decimal("1799"))
        self.assertEqual(ctx.session.get.call_count, 1)

    def test_coupon_diagnostic_uses_existing_native_client_after_403(self):
        blocked = SimpleNamespace(status_code=403)
        body = {"isSuccess": True, "result": [coupon(70, lower=1250)]}
        native = SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: body)
        curl = SimpleNamespace(get=Mock(return_value=native))
        ctx = ReadContext(5, Mock(), pace=Mock())
        ctx.session.get.return_value = blocked
        ctx.session.cookies.get_dict.return_value = {"anonymous_site_cookie": "in-memory"}
        with patch.object(trendyol, "curl_requests", curl):
            result = TrendyolProvider().coupons({"id": 1}, URL, ctx, diagnostic=True)
        self.assertEqual(result, body["result"])
        self.assertEqual(curl.get.call_args.kwargs["impersonate"], "chrome124")
        self.assertEqual(curl.get.call_args.kwargs["cookies"], {"anonymous_site_cookie": "in-memory"})
        ctx.pace.assert_called_once()

    def test_coupon_diagnostic_does_not_retry_other_failures(self):
        response = SimpleNamespace(status_code=500, raise_for_status=Mock(side_effect=RuntimeError("HTTP 500")))
        ctx = ReadContext(5, Mock())
        ctx.session.get.return_value = response
        curl = SimpleNamespace(get=Mock())
        with patch.object(trendyol, "curl_requests", curl):
            self.assertIsNone(TrendyolProvider().coupons({"id": 1}, URL, ctx, diagnostic=True))
        curl.get.assert_not_called()

    def test_coupon_diagnostic_does_not_enable_automated_coupon_reads(self):
        ctx = ReadContext(5, Mock())
        ctx.session.get.return_value = SimpleNamespace(status_code=200, raise_for_status=lambda: None,
                                                       json=lambda: {"isSuccess": True, "result": []})
        provider = TrendyolProvider()
        self.assertEqual(provider.coupons({"id": 1}, URL, ctx, diagnostic=True), [])
        self.assertIsNone(provider.coupons({"id": 1}, URL, ctx))
        self.assertEqual(ctx.session.get.call_count, 1)

    def test_requested_size_has_its_own_price(self):
        data = trendyol.page_product(page(price(949.99), [("S", True), ("M", True)]))
        data["variants"][1]["price"] = {"value": 1099.99}
        html = 'window["__envoy__SHARED_PROPS"]=' + json.dumps({"product": data})
        self.assertEqual(extract_offers(html, URL, size="M")[0].price, Decimal("1099.99"))

    def test_missing_size_price_does_not_copy_the_main_price(self):
        data = trendyol.page_product(page(price(949.99), [("M", True)]))
        del data["variants"][0]["price"]
        html = 'window["__envoy__SHARED_PROPS"]=' + json.dumps({"product": data})
        with self.assertRaises(PriceUnavailableHermesError):
            extract_offers(html, URL, size="M")

    def test_plus_is_not_copied_to_another_size(self):
        data = trendyol.page_product(page(price(1000, plus=900), [("S", True), ("M", True)], plus_eligible=True))
        data["variants"][0]["isSelected"] = True
        html = 'window["__envoy__SHARED_PROPS"]=' + json.dumps({"product": data})
        self.assertEqual(extract_offers(html, URL, size="S")[0].price, Decimal("900"))
        self.assertEqual(extract_offers(html, URL, size="M")[0].price, Decimal("1000"))

    def test_inspect_reads_family_and_rejects_unrelated_product(self):
        provider = TrendyolProvider()
        root = trendyol.page_product(page(price(949.99), [("S", True), ("M", False)], color="Mavi"))
        root.update(id=1, productGroupId=50)
        sibling = trendyol.page_product(page(price(700), [("S", True), ("M", True)], color="Beyaz"))
        sibling.update(id=2, productGroupId=50)
        sibling["variants"][1]["price"] = {"value": 800}
        unrelated = dict(root, id=3, productGroupId=51)
        html = '<a href="/h-m/urun-p-1"></a><a href="/h-m/urun-p-2"></a><a href="/h-m/urun-p-3"></a>'
        provider.read_page = Mock(side_effect=[(html, root), ("", sibling), ("", unrelated)])
        pace = Mock()
        result = provider.inspect(URL, ReadContext(5, Mock(), pace=pace))
        self.assertEqual(result.dimensions, {"Renk": ["Mavi", "Beyaz"], "Beden": ["S", "M"]})
        self.assertEqual([v.price for v in result.variants], [Decimal("949.99"), Decimal("949.99"), Decimal(700), Decimal(800)])
        self.assertEqual([v.in_stock for v in result.variants], [True, False, True, True])
        self.assertEqual(pace.call_count, 2)
        self.assertEqual(result.defaults, {"Renk": ["Mavi"]})

    def test_inspect_keeps_all_out_of_stock_sizes(self):
        data = trendyol.page_product(page(price(949.99), [("M", False)]))
        provider = TrendyolProvider()
        provider.read_page = Mock(return_value=("", data))
        result = provider.inspect(URL, ReadContext(5, Mock()))
        self.assertFalse(result.variants[0].in_stock)

    def test_candidate_links_are_deduplicated_and_same_slug_only(self):
        html = '<a href="/h-m/urun-p-2?x=1"></a><a href="/h-m/urun-p-2"></a><a href="/other/urun-p-3"></a>'
        self.assertEqual(trendyol.family_urls(html, URL), ["https://www.trendyol.com/h-m/urun-p-2"])

    @patch.object(trendyol, "COUPONS_ENABLED", True)
    def test_read_applies_coupon_from_endpoint(self):
        body = {"isSuccess": True, "result": [coupon(15, "PERCENTAGE", cap=150)]}
        response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)
        ctx = self.ctx(page(price(1799)), response)
        watch = WatchRule(name="Nevresim", site="trendyol", url=URL, target_price=Decimal(2000))
        offers = TrendyolProvider().read(watch, ctx, WatchRead())
        self.assertEqual(offers[0].price, Decimal("1649"))
        self.assertIn("/api/coupons/1/search", ctx.session.get.call_args_list[1].args[0])

    @patch.object(trendyol, "COUPONS_ENABLED", True)
    def test_blocked_coupon_endpoint_keeps_page_price(self):
        def blocked():
            raise RuntimeError("403")
        ctx = self.ctx(page(price(1799)), SimpleNamespace(raise_for_status=blocked))
        watch = WatchRule(name="Nevresim", site="trendyol", url=URL, target_price=Decimal(2000))
        self.assertEqual(TrendyolProvider().read(watch, ctx, WatchRead())[0].price, Decimal("1799"))


if __name__ == "__main__":
    unittest.main()
