"""MediaMarkt product identity, stock and observed basket-discount markup."""

import json
import unittest
from decimal import Decimal
from unittest.mock import Mock, patch

from hermes.errors import HermesError, OutOfStockHermesError, PriceUnavailableHermesError
from hermes.providers.base import ReadContext, WatchRead
from hermes.providers.mediamarkt import MediaMarktProvider, read_offer
from hermes.providers.registry import ProviderSet
from hermes.utils import detect_site_from_url
from hermes.web.settings import check_link
from support import watch

DYSON = "https://www.mediamarkt.com.tr/tr/product/_dyson-1252146.html"
MOVA = "https://www.mediamarkt.com.tr/tr/product/_mova-1256199.html"
BADGE = '<ul data-test="cofr-pdp-badges"><li>2000 TL üzeri alışverişte sepette indirim</li></ul>'
BUTTON = '<button data-test="cofr-add-to-basket-button a2c-Button">Sepete Ekle</button>'


def promo(amount="3.499", cents="95"):
    return f'<div data-test="mms-promoflag"><span>-{amount},</span><span>{cents}</span> ₺ <p>Sepette indirim</p></div>'


def page(sku, price, body="", stock="InStock"):
    product = {"@type": "Product", "sku": sku, "name": "Robot süpürge",
               "offers": {"@type": "Offer", "price": price, "priceCurrency": "TRY",
                          "availability": f"https://schema.org/{stock}"}}
    return f'<script type="application/ld+json">{json.dumps(product)}</script><main>{body}</main>'


class MediaMarktTests(unittest.TestCase):
    def test_normal_product_ignores_general_campaign(self):
        html = page("1252146", "39999", f"<nav>Seçili ürünlerde Sepette İndirimler</nav>{BUTTON}{promo()}")
        self.assertEqual(read_offer(html, DYSON).price, Decimal("39999"))

    def test_product_discount_uses_visible_amount_and_ignores_later_products(self):
        html = page("1256199", "69999", f"{BADGE}{promo()}{BUTTON}{promo('2.000','00')}")
        offer = read_offer(html, MOVA)
        self.assertEqual(offer.price, Decimal("66499.05"))
        self.assertEqual(offer.source, "basket-discount")

    def test_missing_conflicting_and_impossible_discount_refuse_to_publish_base_price(self):
        for markup in ("", promo() + promo("2.000", "00"), promo("70.000")):
            with self.subTest(markup=markup), self.assertRaises(PriceUnavailableHermesError):
                read_offer(page("1256199", "69999", f"{BADGE}{markup}{BUTTON}"), MOVA)

    def test_only_positive_product_stock_report_is_unavailable(self):
        with self.assertRaises(OutOfStockHermesError):
            read_offer(page("1256199", None, stock="OutOfStock"), MOVA)
        with self.assertRaises(HermesError):
            read_offer(page("1256199", "69999", stock=""), MOVA)
        with self.assertRaises(HermesError):
            read_offer(page("1252146", "39999"), MOVA)

    def test_provider_and_panel_use_the_same_price(self):
        session = Mock()
        session.get.return_value.status_code = 200
        session.get.return_value.headers = {"content-type": "text/html; charset=utf-8"}
        session.get.return_value.text = page("1256199", "69999", f"{BADGE}{promo()}{BUTTON}")
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        provider = MediaMarktProvider()
        offers = provider.read(watch(site="mediamarkt", url=MOVA), ReadContext(10, session), WatchRead())
        self.assertEqual(offers[0].price, Decimal("66499.05"))
        with patch("hermes.web.settings.requests.Session", return_value=session):
            preview = check_link(MOVA, ProviderSet())
        self.assertEqual(preview["variant_prices"][0]["price"], "66499.05")
        self.assertTrue(preview["variant_prices"][0]["in_stock"])

    def test_exact_turkish_host_and_product_path(self):
        self.assertEqual(detect_site_from_url(MOVA), "mediamarkt")
        with self.assertRaises(HermesError):
            detect_site_from_url("https://www.mediamarkt.com.tr.example.org/tr/product/_mova-1256199.html")
        session = Mock()
        with self.assertRaises(HermesError):
            MediaMarktProvider().read(watch(site="mediamarkt", url="https://www.mediamarkt.com.tr/tr/search.html"),
                                      ReadContext(10, session), WatchRead())
        session.get.assert_not_called()
