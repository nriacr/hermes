import json
import unittest
from decimal import Decimal

from support import APP_PATH  # noqa: F401

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
    def test_current_product_campaign_uses_current_listing_price(self):
        for escaped in (False, True):
            with self.subTest(escaped=escaped):
                html = page(product(), escaped=escaped)
                for read in (extract_offer, extract_embedded_variant_offer):
                    offer = read(html, URL)
                    self.assertEqual(offer.price, Decimal("111199"))
                    self.assertEqual(offer.seller, "Hepsiburada")

    def test_repeated_tags_are_deducted_once(self):
        mapping = product(tags=TAG + "," + TAG)
        self.assertEqual(extract_offer(page(mapping), URL).price, Decimal("111199"))

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

    def test_tag_list_alone_supports_other_fixed_amounts(self):
        mapping = product(tags="")
        mapping["tagList"] = [{"tagId": "telefonlarda-premiuma-ozel-1-000-tl-kupon-firsati"}]
        self.assertEqual(extract_offer(page(mapping), URL).price, Decimal("110699"))

    def test_main_price_replaces_stale_lower_price_for_the_same_listing(self):
        mapping = product(price=120000)
        mapping["prices"] = [{"value": 120000}]
        mapping["variants"][0]["variantListing"] = mapping["variants"][0]["variantListing"][:1]
        self.assertEqual(extract_offer(page(mapping), URL).price, Decimal("119500"))

    def test_invalid_discount_cannot_make_nonpositive_price(self):
        mapping = product(price=100, tags=TAG)
        mapping["prices"] = [{"value": 100}]
        self.assertEqual(extract_offer(page(mapping), URL).price, Decimal("100"))

    def test_recommendation_campaign_is_not_applied_to_selected_product(self):
        mapping = {"product": product(tags=""), "recommendation": product(sku="HBCOTHER")}
        self.assertEqual(extract_offer(page(mapping), URL).price, Decimal("111699"))
