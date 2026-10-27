"""IKEA: page price, stock from the online store or IKEA Bayrampaşa only."""

import json
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

from support import watch

from hermes.errors import HermesError, HttpStatusHermesError, OutOfStockHermesError
from hermes.providers.base import ReadContext, WatchRead
from hermes.providers.ikea import IkeaProvider, STORE_STOCKS_URL, extract_offer, is_challenge, product_code
from hermes.utils import detect_site_from_url

URL = "https://www.ikea.com.tr/urun/uppfylld-beyaz-turuncu-21-cm-kavanoz-acacagi-00529398"
TITLE = "UPPFYLLD kavanoz açacağı, beyaz-turuncu"
STATUS = {"full": "Stokta Var", "critical": "Kritik Stok", "over": "Stokta Yok"}


def page(price="149", availability="OutOfStock"):
    product = {"@context": "https://schema.org/", "@type": "Product", "name": TITLE, "sku": "00529398",
               "offers": {"@type": "Offer", "url": "urun/x-00529398", "priceCurrency": "TRY", "price": price,
                          "availability": f"https://schema.org/{availability}"}}
    return f'<html><head><script type="application/ld+json">{json.dumps(product)}</script></head><body></body></html>'


def stocks(online="over", bayrampasa="over", kartal="full"):
    stores = [("331", "İnternet Mağazası", online), ("194", "IKEA Bayrampaşa", bayrampasa),
              ("530", "IKEA Kartal", kartal)]
    return json.dumps({"d": {"StatusCode": 200, "Data": {"StatusList": [
        {"StoreCode": code, "StoreTitle": title, "StatusClass": cls, "StockText": STATUS[cls]}
        for code, title, cls in stores]}}})


class IkeaParserTests(unittest.TestCase):
    def test_site_detection_and_product_code(self):
        self.assertEqual(detect_site_from_url(URL), "ikea")
        self.assertEqual(product_code(URL), "00529398")

    def test_bayrampasa_in_stock_is_enough_even_when_online_is_out(self):
        offer = extract_offer(page(), stocks(bayrampasa="full"), URL)
        self.assertEqual((offer.title, offer.price, offer.url), (TITLE, Decimal("149"), URL))
        self.assertEqual(offer.seller, "Bayrampaşa: Stokta Var")

    def test_critical_stock_counts_and_both_stores_are_listed(self):
        offer = extract_offer(page(), stocks(online="critical", bayrampasa="full"), URL)
        self.assertEqual(offer.seller, "İnternet Mağazası: Kritik Stok, Bayrampaşa: Stokta Var")

    def test_other_stores_do_not_count(self):
        with self.assertRaises(OutOfStockHermesError) as caught:
            extract_offer(page(), stocks(kartal="full"), URL)
        self.assertEqual((caught.exception.product_title, caught.exception.product_url), (TITLE, URL))

    def test_chosen_stores_replace_the_defaults(self):
        offer = extract_offer(page(), stocks(kartal="full"), URL, ["IKEA Kartal"])
        self.assertEqual(offer.seller, "IKEA Kartal: Stokta Var")
        with self.assertRaises(OutOfStockHermesError):
            extract_offer(page(), stocks(online="full", kartal="over"), URL, ["IKEA Kartal"])

    def test_thousands_price(self):
        self.assertEqual(extract_offer(page(price="3.299"), stocks(online="full"), URL).price, Decimal("3299"))

    def test_unreadable_stock_or_missing_store_is_an_error_not_a_stock_state(self):
        missing = json.dumps({"d": {"Data": {"StatusList": [{"StoreTitle": "IKEA Kartal", "StatusClass": "full"}]}}})
        for body in ("<html>blocked</html>", "{}", missing):
            with self.subTest(body=body):
                with self.assertRaises(HermesError) as caught:
                    extract_offer(page(), body, URL)
                self.assertNotIsInstance(caught.exception, OutOfStockHermesError)

    def test_missing_price_is_an_error(self):
        with self.assertRaises(HermesError) as caught:
            extract_offer("<html><title>x</title></html>", stocks(online="full"), URL)
        self.assertNotIsInstance(caught.exception, OutOfStockHermesError)

    def test_recaptcha_script_on_a_product_page_is_not_a_challenge(self):
        scripts = '<script src="/grecaptcha.js"></script><meta name="robots" content="index">'
        self.assertFalse(is_challenge(page() + scripts))
        self.assertTrue(is_challenge("<html>captcha: are you a robot?</html>"))

    def test_non_product_link_is_rejected(self):
        with self.assertRaises(HermesError):
            product_code("https://www.ikea.com.tr/arama?q=kallax")


class IkeaProviderTests(unittest.TestCase):
    def ctx(self, post_response):
        session = Mock()
        session.get.return_value = SimpleNamespace(status_code=200, text=page(), content=page().encode(),
                                                   encoding="utf-8", apparent_encoding="utf-8",
                                                   headers={"Content-Type": "text/html; charset=utf-8"},
                                                   raise_for_status=lambda: None)
        session.post.return_value = post_response
        return ReadContext(timeout=5, session=session)

    def test_read_posts_the_product_code_to_the_store_stock_service(self):
        ctx = self.ctx(SimpleNamespace(status_code=200, text=stocks(bayrampasa="critical")))
        offers = IkeaProvider().read(watch("", URL), ctx, WatchRead())
        self.assertEqual(offers[0].seller, "Bayrampaşa: Kritik Stok")
        args, kwargs = ctx.session.post.call_args
        self.assertEqual(args[0], STORE_STOCKS_URL)
        self.assertEqual(json.loads(kwargs["data"]), {"sprCode": "00529398"})

    def test_stock_service_http_error_is_an_error(self):
        with self.assertRaises(HttpStatusHermesError):
            IkeaProvider().read(watch("", URL), self.ctx(SimpleNamespace(status_code=503, text="")), WatchRead())

    def test_stock_return_alerts_and_seller_is_shown(self):
        self.assertTrue(IkeaProvider.notifies_stock_return)
        self.assertTrue(IkeaProvider.alert_shows_seller)


if __name__ == "__main__":
    unittest.main()
