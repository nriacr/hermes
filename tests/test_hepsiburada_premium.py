import json
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from support import APP_PATH  # noqa: F401

from hermes.providers import hepsiburada
from hermes.providers.hepsiburada import HepsiburadaProvider
from hermes.providers.hepsiburada import cart
from hermes.providers.hepsiburada.parser import extract_embedded_variant_offer, extract_offer


SKU = "HBCV00009Z3Z8C"
URL = f"https://www.hepsiburada.com/iphone-p-{SKU}?magaza=Hepsiburada"
TAG = "iphone-17-pro-17-pro-max-serisinde-premium-a-ozel-500-tl-kupon-firsati"


def product(sku=SKU, price=111699, tags=TAG):
    return {
        "brand": "Apple", "sku": sku, "listingId": f"listing-{sku}",
        "merchantName": "Hepsiburada",
        "prices": [{"value": 114499}, {"value": price}],
        "paymentTag": tags,
        "tagList": [{"tagId": tag} for tag in tags.split(",")],
        "variants": [{"sku": sku, "variantListing": [
            {"listingId": f"listing-{sku}", "merchantName": "Hepsiburada",
             "finalPriceOnSale": 115199,
             "minimumPrices": [{"name": "non-segmented-price", "value": 115199}]},
            {"listingId": "other-seller", "merchantName": "Ceptemax", "finalPriceOnSale": 114936.58},
        ]}],
    }


def page(mapping, visible="", *, escaped=False):
    data = json.dumps(mapping, ensure_ascii=False)
    if escaped:
        data = json.dumps(data, ensure_ascii=False)
    return f"<h1>iPhone 17 Pro Max 256 GB Kozmik Turuncu</h1>{visible}<script>window.product = {data};</script>"


class HepsiburadaPremiumTests(unittest.TestCase):
    def test_coupon_tags_alone_never_reduce_the_price(self):
        for escaped in (False, True):
            with self.subTest(escaped=escaped):
                html = page(product(), escaped=escaped)
                for read in (extract_offer, extract_embedded_variant_offer):
                    offer = read(html, URL)
                    self.assertEqual(offer.price, Decimal("111699"))
                    self.assertEqual(offer.seller, "Hepsiburada")

    def test_other_variant_does_not_inherit_main_campaign(self):
        mapping = product()
        mapping["variants"].append({"sku": "HBCOTHER", "variantListing": [
            {"listingId": "sibling", "merchantName": "Hepsiburada", "finalPriceOnSale": 113499},
        ]})
        self.assertEqual(extract_offer(page(mapping), URL.replace(SKU, "HBCOTHER")).price, Decimal("113499"))

    def test_main_campaign_does_not_discount_other_seller(self):
        mapping = product(price=130000)
        mapping["prices"] = [{"value": 130000}]
        offer = extract_offer(page(mapping), URL)
        self.assertEqual(offer.price, Decimal("114936.58"))
        self.assertEqual(offer.seller, "Ceptemax")

    def test_unrelated_campaign_tags_do_not_reduce_price(self):
        for tag in (
            "premium-a-ozel-secili-iphone-modellerinde-2000-tl-ye-varan-kupon-firsati",
            "secili-cep-telefonlarinda-premiuma-ozel-1-000-tl-ye-varan-sepet-indirimi",
            "visa-ya-ozel-secili-cep-telefonlarinda-2-500-tl-indirim",
            "secili-cep-telefonlarinda-500-tl-indirim-firsati",
            "",
        ):
            with self.subTest(tag=tag):
                self.assertEqual(extract_offer(page(product(tags=tag)), URL).price, Decimal("111699"))

    def test_explicit_premium_price_is_not_discounted_again(self):
        mapping = product()
        mapping["minimumPrices"] = [{"name": "Premium ile", "value": 111199}]
        self.assertEqual(extract_offer(page(mapping), URL).price, Decimal("111199"))

    def test_visible_premium_price_is_not_discounted_again(self):
        visible = "<span>Satıcı: Hepsiburada</span><div>Premium'a özel fiyat 111.199 TL</div>"
        for price in (111699, 111199):
            for read in (extract_offer, extract_embedded_variant_offer):
                with self.subTest(price=price, read=read.__name__):
                    self.assertEqual(read(page(product(price=price), visible), URL).price, Decimal("111199"))

    def test_main_price_replaces_stale_lower_price_for_the_same_listing(self):
        mapping = product(price=120000)
        mapping["prices"] = [{"value": 120000}]
        mapping["variants"][0]["variantListing"] = mapping["variants"][0]["variantListing"][:1]
        self.assertEqual(extract_offer(page(mapping), URL).price, Decimal("120000"))

    def test_invalid_discount_cannot_make_nonpositive_price(self):
        mapping = product(price=100, tags=TAG)
        mapping["prices"] = [{"value": 100}]
        self.assertEqual(extract_offer(page(mapping), URL).price, Decimal("100"))

    def test_recommendation_campaign_is_not_applied_to_selected_product(self):
        mapping = {"product": product(tags=""), "recommendation": product(sku="HBCOTHER")}
        self.assertEqual(extract_offer(page(mapping), URL).price, Decimal("111699"))


def live_payload(*listings):
    return {"statusCode": 200, "data": {"listings": list(listings)}}


def live_listing(seller, price, **extra):
    return {"merchantName": seller, "price": {"currency": 0, "value": price}, "isSalable": True,
            "prices": [{"currency": 0, "discountRate": 0, "formattedPrice": "-", "value": price}], **extra}


class FakeLiveResponse:
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200

    def raise_for_status(self):
        if isinstance(self.payload, Exception):
            raise self.payload

    def json(self):
        return self.payload


class HepsiburadaLiveSellerTests(unittest.TestCase):
    """A seller in the page's compact list counts only if the live list still has it."""

    def page_with_ghost(self):
        mapping = product(price=111049)
        mapping["variants"][0]["variantListing"] = [
            {"listingId": f"listing-{SKU}", "merchantName": "Hepsiburada", "finalPriceOnSale": 111049},
            {"listingId": "ghost", "merchantName": "MARTEKNOLOJİ", "finalPriceOnSale": 110231.08,
             "minimumPrices": [{"name": "30", "value": 106708.83}, {"name": "non-segmented-price", "value": 110231.08}]},
            {"listingId": "real", "merchantName": "Troy Apple", "finalPriceOnSale": 110400},
        ]
        return page(mapping)

    def read(self, payload):
        provider = HepsiburadaProvider()
        html = self.page_with_ghost()
        session = SimpleNamespace(get=lambda *args, **kwargs: FakeLiveResponse(payload))
        ctx = SimpleNamespace(timeout=5)
        with patch.object(hepsiburada, "curl_requests", SimpleNamespace(Session=lambda: session)), \
             patch.object(hepsiburada, "count_curl", lambda response: response):
            offer, _page_html = provider._variant_offer(ctx, SimpleNamespace(url=URL), html, URL)
        return offer

    def test_shared_parse_keeps_verified_offer_and_does_not_survive_a_read(self):
        from hermes.providers.hepsiburada.common import product_parse_scope

        payload = live_payload(live_listing("Hepsiburada", 111049), live_listing("Troy Apple", 110400))
        expected = self.read(payload)
        for _ in range(2):
            with product_parse_scope() as stats:
                self.assertEqual(self.read(payload), expected)
            self.assertEqual(stats["pages"], 1)
            self.assertGreater(stats["hits"], 0)

    def test_page_alone_still_prefers_the_ghost(self):
        self.assertEqual(extract_offer(self.page_with_ghost(), URL).seller, "MARTEKNOLOJİ")

    def test_seller_missing_from_live_list_is_dropped(self):
        offer = self.read(live_payload(live_listing("Hepsiburada", 111049), live_listing("Troy Apple", 114824.08)))
        self.assertEqual((offer.seller, offer.price), ("Hepsiburada", Decimal("111049")))

    def test_unsalable_seller_is_dropped(self):
        offer = self.read(live_payload(live_listing("Hepsiburada", 111049), live_listing("MARTEKNOLOJİ", 110231.08, isSalable=False)))
        self.assertEqual(offer.seller, "Hepsiburada")

    def test_real_cheaper_seller_is_kept_with_its_live_price(self):
        offer = self.read(live_payload(live_listing("Hepsiburada", 111049), live_listing("Troy Apple", 110300)))
        self.assertEqual((offer.seller, offer.price), ("Troy Apple", Decimal("110300")))

    def test_seller_whose_live_price_rose_loses_to_the_shown_seller(self):
        offer = self.read(live_payload(live_listing("Hepsiburada", 111049), live_listing("MARTEKNOLOJİ", 112000)))
        self.assertEqual((offer.seller, offer.price), ("Hepsiburada", Decimal("111049")))

    def test_unreadable_or_empty_live_list_keeps_only_the_shown_seller(self):
        for payload in (RuntimeError("403"), live_payload(), {"data": None}):
            with self.subTest(payload=payload):
                offer = self.read(payload)
                self.assertEqual((offer.seller, offer.price), ("Hepsiburada", Decimal("111049")))


PAGE_FIELDS = (
    '<script>window.meta = {"productId":"HBC00009Z3SG3","definitionId":"60","definitionName":"Cep Telefonu",'
    '"product_category_ids":["2147483642","371965","60005202"]};</script>'
)


def listing(price=6699, tags="kampanya-premium-a-ozel-kupon", **extra):
    return {"merchantName": "Hepsiburada", "merchantId": "merchant", "listingId": "listing", "isSalable": True,
            "price": {"currency": 0, "value": price}, "prices": [{"value": price}], "paymentTag": tags, **extra}


def evaluation(coupons=(), premium=None, campaign=None):
    evaluated = {}
    if premium is not None:
        evaluated["evaluateAsPremiumResult"] = {"discountedPrice": premium}
    if campaign is not None:
        evaluated["evaluateResult"] = {"discountedPrice": campaign}
    return {"data": {"result": {"product": {"priceData": {"status": "NODATA"}, "promoData": {"data": {
        "campaignEvaluateResult": evaluated, "coupons": list(coupons)}}}}}}


def coupon(limit, amount, premium=True, end="2099-01-01T00:00:00+03:00"):
    return {"conditionAmount": limit, "discount": amount, "isPremium": premium, "endDateTime": end,
            "label": f"Ek {amount} TL Kupon"}


TIERS = [coupon(1000, 100), coupon(2000, 200), coupon(3000, 300)]


class HepsiburadaCartPriceTests(unittest.TestCase):
    """The site's own evaluation decides which coupon applies, as long as it is valid."""

    def price(self, response, price=6699):
        return cart.cart_price(response, listing(price))[0]

    def test_highest_reached_coupon_limit_applies(self):
        for price, expected in ((6699, 6399), (3000, 2700), (2999, 2799), (2000, 1800), (1500, 1400), (999, 999)):
            with self.subTest(price=price):
                self.assertEqual(self.price(evaluation(TIERS), price), Decimal(expected))

    def test_ended_or_non_premium_coupons_do_not_count(self):
        coupons = [coupon(3000, 300, end="2026-11-01T23:59:00+03:00"), coupon(1000, 150, premium=False)]
        self.assertEqual(self.price(evaluation(coupons)), Decimal("6699"))

    def test_no_coupon_in_the_answer_keeps_the_list_price(self):
        price, note = cart.cart_price(evaluation(), listing(113349))
        self.assertEqual(price, Decimal("113349"))
        self.assertIn("liste 113.349 TL", note)

    def test_cart_campaign_comes_before_the_coupon_limit(self):
        price, note = cart.cart_price(evaluation([coupon(3000, 300)], campaign=2900), listing(3100))
        self.assertEqual(price, Decimal("2900"))
        self.assertIn("sepet kampanyası", note)

    def test_site_premium_price_is_used_as_is(self):
        price, note = cart.cart_price(evaluation(TIERS, premium=110549), listing(111049))
        self.assertEqual(price, Decimal("110549"))
        self.assertIn("site Premium hesabı", note)

    def test_unreadable_answer_is_unknown(self):
        self.assertIsNone(cart.cart_price({"data": {"result": {"product": None}}}, listing())[0])

    def test_payload_carries_the_listing_and_page_fields(self):
        product_fields = cart.page_product(PAGE_FIELDS)
        payload = cart.evaluation_payload(listing(), product_fields, SKU)["product"]
        self.assertEqual(payload["rootCategoryList"], [2147483642, 371965, 60005202])
        self.assertEqual((payload["listingId"], payload["merchantId"], payload["finalPrice"]), ("listing", "merchant", 6699.0))
        self.assertEqual(payload["productTags"], ["kampanya-premium-a-ozel-kupon"])


class FakeSession:
    def __init__(self, listings, answer):
        self.listings, self.answer, self.posts = listings, answer, []

    def get(self, *args, **kwargs):
        return FakeLiveResponse(live_payload(*self.listings))

    def post(self, url, **kwargs):
        self.posts.append(json.loads(kwargs["data"]))
        return FakeLiveResponse(self.answer)


class HepsiburadaCartReadTests(unittest.TestCase):
    def read(self, listings, answer, tags=TAG):
        html = page(product(price=6699, tags=tags)) + PAGE_FIELDS
        session = FakeSession(listings, answer)
        with patch.object(hepsiburada, "curl_requests", SimpleNamespace(Session=lambda: session)), \
             patch.object(hepsiburada, "count_curl", lambda response: response):
            offer, _page_html = HepsiburadaProvider()._variant_offer(SimpleNamespace(timeout=5), SimpleNamespace(url=URL), html, URL)
        return offer, session

    def test_valid_coupon_gives_the_cart_price_and_its_calculation(self):
        offer, session = self.read([listing(6699)], evaluation(TIERS))
        self.assertEqual(offer.price, Decimal("6399"))
        self.assertIn("alt limit 3.000 TL", offer.price_note)
        self.assertEqual(session.posts[0]["product"]["sku"], SKU)

    def test_ended_coupon_leaves_the_list_price(self):
        offer, _session = self.read([listing(6699)], evaluation())
        self.assertEqual(offer.price, Decimal("6699"))

    def test_failed_evaluation_keeps_the_page_price(self):
        offer, _session = self.read([listing(6699)], RuntimeError("403"))
        self.assertEqual(offer.price, Decimal("6699"))
        self.assertIn("kampanya sorgusu başarısız", offer.price_note)

    def test_listing_without_campaign_tags_is_not_evaluated(self):
        offer, session = self.read([listing(6699, tags="kargo-bedava")], evaluation(TIERS), tags="")
        self.assertEqual((offer.price, session.posts), (Decimal("6699"), []))
