"""Cart intents are opt-in, durable and never run on the notification consumer."""
import re
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from support import TempData, config, monitor, notifier

from hermes.cart import CartQueue
from hermes.database import Database
from hermes.models import OfferResult, WatchRule
from hermes.providers.amazon import cart as amazon_cart
from hermes.providers.amazon.cart import Cart, CartError, add_to_cart, payload
from hermes.providers.amazon.parser import extract_verified_warehouse_offers_from_listing
from hermes.providers.amazon import AmazonProvider
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


    def test_success_sends_nothing_and_limit_reports(self):
        provider = Mock()
        provider.add_to_cart.return_value = 'Sepette yalnız bu ürün var'
        delivery = Mock()
        queue = CartQueue(self.db, {'amazon': provider}, [self.watch], delivery)
        for price in ('90', '89', '88', '87', '86', '85'):
            job = payload(self.watch, OfferResult('x', Decimal(price), is_warehouse=True), self.watch.url)
            self.db.transaction([queue.statement('amazon', 'card', {**job, 'title': 'Kulaklık'})])
        while queue.run_one():
            pass
        self.assertEqual(provider.add_to_cart.call_count, 5)
        delivery.send.assert_called_once()
        self.assertEqual(delivery.send.call_args.args[0], 'Sepete eklenemedi')

    def test_session_problem_is_announced_once_until_it_clears(self):
        provider = Mock()
        provider.check_cart_session.side_effect = [CartError('Amazon oturumu kapalı'), CartError('Amazon oturumu kapalı'), None,
                                                   CartError('Amazon oturumu kapalı')]
        delivery = Mock()
        queue = CartQueue(self.db, {'amazon': provider}, [self.watch], delivery)
        self.assertFalse(queue.check_session(now=queue.next_session_check - 1))
        for _ in range(4):
            self.assertTrue(queue.check_session(now=queue.next_session_check))
        self.assertEqual(provider.check_cart_session.call_count, 4)
        self.assertEqual([c.args[0] for c in delivery.send.call_args_list], ['Amazon oturumu sorunlu'] * 2)

    def test_no_session_check_without_an_enabled_card(self):
        queue = CartQueue(self.db, {'amazon': Mock()}, [])
        queue.start()
        self.assertIsNone(queue.thread)

ASIN = 'B012345678'
CSRF = 'csrf-token-0123456789'
ADD_CSRF = 'add-csrf-token-0123456789'
PAGE_CSRF = 'page-csrf-token-0123456789'


class FakeAmazon:
    """The logged-in cart as the browser bridge sees it; records each request."""

    def __init__(self, active=(), logged_in=True, accept_add=True, save_works=True, listing=''):
        self.active = [dict(item) for item in active]
        self.saved = []
        self.logged_in = logged_in
        self.accept_add = accept_add
        self.save_works = save_works
        self.listing = listing
        self.requests = []
        self.add_token = ADD_CSRF

    def page(self):
        """What the browser bridge returns for view='cart'."""
        return {'nav': 'Nuri' if self.logged_in else 'Merhaba, giriş yapın', 'cart_token': CSRF, 'add_token': self.add_token,
                'rows': [{'asin': i['asin'], 'itemid': i['id'], 'price': i['price'], 'quantity': str(i.get('quantity', 1)),
                          'save_value': 'Daha sonra kaydet'} for i in self.active]}

    def __call__(self, method, url, body='', view=''):
        self.requests.append((method, url, body))
        if method == 'GET':
            return self.page() if view == 'cart' else self.listing if view == 'offer' else None
        fields = dict(part.split('=', 1) for part in body.split('&'))
        if '/cart/add-to-cart/' in url:
            assert fields['anti-csrftoken-a2z'] in (ADD_CSRF, PAGE_CSRF)
            assert fields['items%5B0.base%5D%5Bquantity%5D'] == '1'
            if self.accept_add:
                self.active.append({'asin': fields['items%5B0.base%5D%5Basin%5D'], 'id': 'new', 'price': '90',
                                    'offer': fields['items%5B0.base%5D%5BofferListingId%5D']})
            return self.page()
        assert fields['anti-csrftoken-a2z'] == CSRF
        item_id = re.search(r'submit\.save-for-later\.([^=]+)=', body).group(1)
        assert url == 'https://www.amazon.com.tr/cart/ref=ord_cart_shr' and len(fields) == 2
        if self.save_works:
            self.saved += [i for i in self.active if i['id'] == item_id]
            self.active = [i for i in self.active if i['id'] != item_id]
        return self.page()


DEPO_OFFER = {'captcha': False, 'new': {'offer_id': 'new-offer-id-0123456789', 'price': '120.0', 'token': PAGE_CSRF},
              'depo': [{'offer_id': 'logged-in-offer-id-0123456789', 'price': '90.0', 'token': PAGE_CSRF}]}
DEPO_LISTING = ('<div id="usedAccordionRow"><div data-csa-c-slot-id="usedAccordionRow" role="button">İkinci El - Çok İyi '
                '<span class="a-price"><span class="a-offscreen">90,00 TL</span></span> Satıcı: Amazon Depo</div>'
                '<form action="/gp/product/handle-buy-box/ref=dp_start-bbf_1_glance">'
                f'<input type="hidden" name="anti-csrftoken-a2z" value="{PAGE_CSRF}">'
                '<input type="hidden" name="items[0.base][offerListingId]" value="logged-in-offer-id-0123456789">'
                '<input type="submit" name="submit.buy-now"></form></div>')


class AmazonCartFlowTests(unittest.TestCase):
    job = {'asin': ASIN, 'price': '90', 'target': '100', 'warehouse': True, 'seller': 'Amazon Depo',
           'offer_id': 'search-offer-id-0123456789', 'url': 'https://www.amazon.com.tr/dp/' + ASIN}

    def setUp(self):
        amazon_cart._last_add_token = ''

    def test_deal_is_added_first_then_every_other_item_saved_for_later(self):
        amazon = FakeAmazon([{'asin': 'B0OTHER001', 'id': 'a1', 'price': '4199', 'quantity': 4},
                             {'asin': 'B0OTHER002', 'id': 'a2', 'price': '10'}])
        result = add_to_cart(self.job, amazon)
        self.assertEqual(result, 'Sepette yalnız bu ürün var')
        self.assertEqual([i['asin'] for i in amazon.active], [ASIN])
        self.assertEqual([i['id'] for i in amazon.saved], ['a1', 'a2'])
        posts = [url for method, url, _ in amazon.requests if method == 'POST']
        self.assertEqual(['add-to-cart' in url for url in posts], [True, False, False])
        # The provider's offer id is used directly: no product page read before the add.
        self.assertFalse([url for method, url, _ in amazon.requests if method == 'GET' and 'cart' not in url])
        # Amazon answers each POST with the updated cart, so no extra cart read follows.
        self.assertEqual(len(amazon.requests), 4)
        self.assertTrue(all('checkout' not in url and 'buy' not in url for _, url, _ in amazon.requests))

    def test_cart_without_an_add_token_uses_the_listing_form_token(self):
        amazon = FakeAmazon(listing=DEPO_OFFER)
        amazon.add_token = ''
        add_to_cart(self.job, amazon)
        self.assertIn('/dp/' + ASIN, amazon.requests[1][1])
        self.assertIn(PAGE_CSRF, amazon.requests[2][2])

    def test_new_offer_uses_the_buy_box_offer_not_the_depo_one(self):
        amazon = FakeAmazon(listing=DEPO_OFFER)
        add_to_cart({**self.job, 'warehouse': False, 'price': '120', 'target': '130', 'offer_id': ''}, amazon)
        self.assertEqual(amazon.active[0]['offer'], 'new-offer-id-0123456789')

    def test_captcha_offer_page_changes_nothing(self):
        amazon = FakeAmazon(listing={'captcha': True, 'new': None, 'depo': []})
        with self.assertRaises(CartError):
            add_to_cart({**self.job, 'offer_id': ''}, amazon)
        self.assertFalse([r for r in amazon.requests if r[0] == 'POST'])

    def test_logged_out_cart_changes_nothing(self):
        amazon = FakeAmazon([{'asin': 'B0OTHER001', 'id': 'a1', 'price': '10'}], logged_in=False)
        with self.assertRaises(CartError) as error:
            add_to_cart(self.job, amazon)
        self.assertIn('oturumu kapalı', error.exception.public_message)
        self.assertFalse([r for r in amazon.requests if r[0] == 'POST'])

    def test_missing_offer_id_is_read_from_the_logged_in_listing(self):
        amazon = FakeAmazon(listing=DEPO_OFFER)
        add_to_cart({**self.job, 'offer_id': ''}, amazon)
        self.assertEqual(amazon.active[0]['offer'], 'logged-in-offer-id-0123456789')

    def test_listing_above_target_is_not_added(self):
        amazon = FakeAmazon(listing={**DEPO_OFFER, 'depo': [{**DEPO_OFFER['depo'][0], 'price': '150.0'}]})
        with self.assertRaises(CartError):
            add_to_cart({**self.job, 'offer_id': ''}, amazon)
        self.assertFalse([r for r in amazon.requests if r[0] == 'POST'])

    def test_same_product_already_in_cart_is_saved_first_so_quantity_stays_one(self):
        amazon = FakeAmazon([{'asin': ASIN, 'id': 'old', 'price': '120'}])
        add_to_cart(self.job, amazon)
        self.assertEqual([(i['id'], i['asin']) for i in amazon.active], [('new', ASIN)])
        self.assertEqual([i['id'] for i in amazon.saved], ['old'])

    def test_refused_add_retries_once_with_the_logged_in_id_then_reports(self):
        amazon = FakeAmazon(accept_add=False, listing=DEPO_OFFER)
        with self.assertRaises(CartError) as error:
            add_to_cart(self.job, amazon)
        self.assertFalse(error.exception.added)
        self.assertEqual(sum(1 for m, url, _ in amazon.requests if m == 'POST' and 'add-to-cart' in url), 2)

    def test_failed_save_for_later_reports_the_deal_as_added(self):
        amazon = FakeAmazon([{'asin': 'B0OTHER001', 'id': 'a1', 'price': '10'}], save_works=False)
        with self.assertRaises(CartError) as error:
            add_to_cart(self.job, amazon)
        self.assertTrue(error.exception.added)

    def test_empty_cart_without_an_add_form_uses_the_last_seen_token(self):
        add_to_cart(self.job, FakeAmazon())
        amazon = FakeAmazon()
        amazon.add_token = ''
        add_to_cart(self.job, amazon)
        self.assertIn(ADD_CSRF, amazon.requests[1][2])
        self.assertEqual(len(amazon.requests), 2)


class CartSummaryTests(unittest.TestCase):
    def test_missing_summary_or_signed_out_nav_is_an_error(self):
        for summary in (None, {'nav': 'Merhaba, Giriş yapın', 'rows': []}, {'rows': []}):
            with self.subTest(summary=summary), self.assertRaises(CartError):
                Cart(summary)
        cart = Cart({'nav': 'Nuri&#8203;', 'rows': [{'asin': ASIN, 'itemid': 'i1'}, {'asin': '', 'itemid': 'x'}],
                     'cart_token': 'c', 'add_token': 'a'})
        self.assertEqual([r['itemid'] for r in cart.rows], ['i1'])


class OfferIdParserTests(unittest.TestCase):
    def test_depo_offer_takes_the_id_of_its_own_used_block(self):
        page = ('<div id="addToCart"><input name="items[0.base][offerListingId]" value="new-offer-id-0123456789">'
                '<span class="a-price"><span class="a-offscreen">120,00 TL</span></span></div>' + DEPO_LISTING)
        offers = extract_verified_warehouse_offers_from_listing(page, 'https://www.amazon.com.tr/dp/' + ASIN)
        self.assertEqual([o.offer_id for o in offers], ['logged-in-offer-id-0123456789'])

    def test_offer_without_a_form_has_no_id(self):
        page = DEPO_LISTING.replace('<input type="hidden" name="items[0.base][offerListingId]" value="logged-in-offer-id-0123456789">', '')
        offers = extract_verified_warehouse_offers_from_listing(page, 'https://www.amazon.com.tr/dp/' + ASIN)
        self.assertEqual([o.offer_id for o in offers], [''])


class CartIntentWiringTests(unittest.TestCase):
    def setUp(self):
        self.data = TempData()
        self.addCleanup(self.data.cleanup)

    def jobs(self, rule, offer, cycles=1):
        hermes = monitor(config([rule]), self.data, notifier())
        try:
            with patch.object(AmazonProvider, 'read', return_value=[offer]):
                for _ in range(cycles):
                    hermes.run_cycle()
            with hermes.delivery.db.lock:
                return [row[0] for row in hermes.delivery.db.connect().execute('SELECT payload FROM cart_jobs')]
        finally:
            hermes.close()

    def offer(self, price='90'):
        return OfferResult('Depo ürünü', Decimal(price), seller='Amazon Depo', url='https://www.amazon.com.tr/dp/' + ASIN,
                           is_warehouse=True, offer_id='search-offer-id-0123456789')

    def test_alerted_deal_queues_one_cart_job_with_its_offer_id(self):
        rule = WatchRule('Depo', 'amazon', 'https://www.amazon.com.tr/s?k=x', Decimal('100'), auto_add_to_cart=True)
        jobs = self.jobs(rule, self.offer(), cycles=2)
        self.assertEqual(len(jobs), 1)
        self.assertIn('search-offer-id-0123456789', jobs[0])

    def test_card_without_the_box_queues_nothing(self):
        rule = WatchRule('Depo', 'amazon', 'https://www.amazon.com.tr/s?k=x', Decimal('100'))
        self.assertEqual(self.jobs(rule, self.offer()), [])

    def test_price_above_target_queues_nothing(self):
        rule = WatchRule('Depo', 'amazon', 'https://www.amazon.com.tr/s?k=x', Decimal('100'), auto_add_to_cart=True)
        self.assertEqual(self.jobs(rule, self.offer('150')), [])
