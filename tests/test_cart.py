"""Cart intents are opt-in, durable and never run on the notification consumer."""
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

from hermes.cart import CartQueue
from hermes.database import Database
from hermes.models import OfferResult, WatchRule
from hermes.providers.amazon.cart import CartError, cart_form, payload, save_all_for_later, verify_row
from hermes.providers.amazon.parser import parse_product_page
from hermes.providers.base import Provider


class CartCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Database(Path(self.temp.name) / 'hermes.db')
        self.db.connect()
        self.addCleanup(self.db.close)
        self.watch = WatchRule('test', 'amazon', 'https://www.amazon.com.tr/dp/B012345678', Decimal('100'),
                               tracking_id='card', auto_add_to_cart=True)

    def test_cart_disabled_by_default_and_other_provider_has_no_intent(self):
        self.assertFalse(WatchRule('x', 'other', 'url', Decimal(1)).auto_add_to_cart)
        self.assertIsNone(Provider.cart_payload(None, self.watch, None, None))

    def test_same_intent_is_deduplicated(self):
        job = payload(self.watch, OfferResult('x', Decimal('90'), is_warehouse=True), self.watch.url)
        sql = CartQueue.statement('amazon', 'card', job)
        self.db.transaction([sql, sql])
        self.assertEqual(self.db.connect().execute('SELECT count(*) FROM cart_jobs').fetchone()[0], 1)

    def test_worker_runs_separately_and_failure_is_not_retried(self):
        provider = Mock()
        provider.add_to_cart.side_effect = CartError('Test hatası')
        queue = CartQueue(self.db, {'amazon': provider}, [self.watch])
        job = payload(self.watch, OfferResult('x', Decimal('90'), is_warehouse=True), self.watch.url)
        self.db.transaction([queue.statement('amazon', 'card', job)])
        provider.add_to_cart.assert_not_called()
        self.assertTrue(queue.run_one())
        self.assertFalse(queue.run_one())
        provider.add_to_cart.assert_called_once()
        self.assertEqual(self.db.connect().execute('SELECT state FROM cart_jobs').fetchone()[0], 'failed')

    def test_expired_and_disabled_jobs_do_not_touch_cart(self):
        provider = Mock()
        queue = CartQueue(self.db, {'amazon': provider}, [])
        job = payload(self.watch, OfferResult('x', Decimal('90')), self.watch.url)
        self.db.transaction([queue.statement('amazon', 'card', job)])
        queue.run_one()
        provider.add_to_cart.assert_not_called()
        self.assertEqual(self.db.connect().execute('SELECT state FROM cart_jobs').fetchone()[0], 'expired')

    def test_interrupted_post_is_not_automatically_repeated(self):
        queue = CartQueue(self.db, {}, [self.watch])
        job = payload(self.watch, OfferResult('x', Decimal('90')), self.watch.url)
        self.db.transaction([queue.statement('amazon', 'card', job),
                             ("UPDATE cart_jobs SET state='processing'", ())])
        queue.start()
        queue.close()
        self.assertEqual(self.db.connect().execute('SELECT state FROM cart_jobs').fetchone()[0], 'uncertain')

    def test_above_target_does_not_create_cart_intent(self):
        self.assertIsNone(payload(self.watch, OfferResult('x', Decimal('101')), self.watch.url))

    def test_wrong_price_is_not_reported_as_success(self):
        row = parse_product_page('<div data-price="91" data-quantity="1">Amazon Depo İkinci el</div>').select_one('div')
        with self.assertRaises(CartError):
            verify_row(row, {'price': '90', 'warehouse': True, 'seller': 'Amazon Depo'})

    def test_wrong_condition_or_seller_is_not_success(self):
        row = parse_product_page('<div data-price="90" data-quantity="1">Başka satıcı Yeni</div>').select_one('div')
        with self.assertRaises(CartError):
            verify_row(row, {'price': '90', 'warehouse': True, 'seller': 'Amazon Depo'})

    def test_buy_now_form_is_never_submitted(self):
        html = '<div id="aod-offer">Amazon Depo İkinci el <span class="a-price"><span class="a-offscreen">90,00 TL</span></span><form action="/checkout/entry/buynow"><input name="offeringID.1" value="offer"></form></div>'
        with self.assertRaises(CartError):
            cart_form(html, {'price': '90', 'warehouse': True, 'asin': 'B012345678', 'url': self.watch.url})

    def test_save_later_does_not_compare_existing_prices(self):
        empty = '<span id="nav-link-accountList-nav-line-1">Test hesabı</span><div id="sc-active-cart"></div>'
        transport = Mock(return_value=empty)
        save_all_for_later(transport)
        transport.assert_called_once()

    def test_save_later_requires_exact_safe_form(self):
        html = '<span id="nav-link-accountList-nav-line-1">Test hesabı</span><div id="sc-active-cart"><div data-asin="B012345678" data-price="999"></div></div>'
        transport = Mock(return_value=html)
        with self.assertRaises(CartError):
            save_all_for_later(transport)
        self.assertEqual(transport.call_count, 1)

    def test_ambiguous_warehouse_form_is_not_submitted(self):
        row = '<div id="aod-offer">Amazon Depo İkinci el <span class="a-price"><span class="a-offscreen">90,00 TL</span></span><form action="/gp/add-to-cart"><input name="offeringID.1" value="{offer}"></form></div>'
        with self.assertRaises(CartError):
            cart_form(row.format(offer='one') + row.format(offer='two'), {'price': '90', 'warehouse': True, 'asin': 'B012345678', 'url': self.watch.url})
