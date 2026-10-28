"""Amazon cart operations through the owner's Pi browser. No checkout actions.

The cart is always read; the deal is added first (fastest: Depo stock goes in
seconds), then every other item moves to "Daha sonra kaydet", so the deal is the
only item left. The owner chose the faster order.
"""
import json
import re
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlencode

import requests

from ...logging_utils import log
from ...utils import extract_asin_from_url
from .client import protection_reason
from .common import normalize_offer_text

BRIDGE = Path('/share/hermes-cart/bridge.json')
BRIDGE_URL = 'http://local-hermes-browser:8098/fetch'
CART_URL = 'https://www.amazon.com.tr/gp/cart/view.html'
ADD_URL = 'https://www.amazon.com.tr/cart/add-to-cart/ref=dp_start-bbf_1_glance'
SAVE_URL = 'https://www.amazon.com.tr/cart/ref=ord_cart_shr'
MAX_ACTIVE_ITEMS = 30


class CartError(Exception):
    def __init__(self, message, added=False):
        self.public_message = message
        # True when the deal is already in the cart although a later step failed.
        self.added = added
        super().__init__(message)


def payload(watch, offer, url):
    asin = extract_asin_from_url(url)
    # Only Depo deals go to the cart; a new offer is notified as before.
    if not asin or not offer.is_warehouse or offer.price > watch.target_price:
        return None
    return {'asin': asin, 'price': str(offer.price), 'target': str(watch.target_price),
            'warehouse': bool(offer.is_warehouse), 'seller': offer.seller or '',
            'offer_id': offer.offer_id or '', 'url': 'https://www.amazon.com.tr/dp/' + asin}


class BrowserTransport:
    """Sends one allow-listed request through the logged-in Chromium; cookies never leave it."""

    def __init__(self):
        self.started = time.monotonic()
        self.steps = []

    def __call__(self, method, url, body='', view=''):
        begun = time.monotonic()
        try:
            return self._send(method, url, body, view)
        finally:
            kind = 'ekle' if 'add-to-cart' in url else 'sonra_al' if method == 'POST' else 'teklif' if 'cart' not in url else 'sepet'
            self.steps.append(f'{kind} {time.monotonic() - begun:.1f}')

    def _send(self, method, url, body, view):
        try:
            meta = json.loads(BRIDGE.read_text())
        except (OSError, ValueError):
            raise CartError("Hermes Tarayıcı çalışmıyor; Alfred'de uygulamayı başlat") from None
        if meta.get('url') != BRIDGE_URL:
            raise CartError('Hermes Tarayıcı bağlantısı geçersiz')
        try:
            response = requests.post(meta['url'], json={'method': method, 'url': url, 'body': body, 'view': view},
                                     headers={'Authorization': 'Bearer ' + meta['token']}, timeout=(2, 15))
            result = response.json()
        except Exception:
            raise CartError('Hermes Tarayıcı yanıt vermedi') from None
        if result.get('error'):
            raise CartError('Hermes Tarayıcı isteği yapamadı (' + str(result['error'])[:40] + ')')
        status = result.get('status')
        if view:
            # The browser already reduced the page to a summary (None: not that kind of page).
            if status not in (0, 200, 302, 303):
                raise CartError('Amazon sepet isteğini kabul etmedi')
            return result.get(view)
        html = result.get('html', '')
        # A POST is not followed (status 0 = redirect); the caller re-reads the cart.
        if method == 'GET' and (status != 200 or not html):
            raise CartError('Amazon sayfası okunamadı')
        if method == 'POST' and status not in (0, 200, 302, 303):
            raise CartError('Amazon sepet isteğini kabul etmedi')
        if html and protection_reason(html):
            raise CartError("Amazon doğrulama istiyor; Hermes Tarayıcı'da kontrol et")
        return html


class Cart:
    """The browser's summary of one logged-in cart page.

    Chromium reduces Amazon's ~1 MB cart to rows and form tokens (view='cart'),
    so the page never crosses to Hermes and nothing heavy is parsed on the Pi.
    """

    def __init__(self, summary):
        if not isinstance(summary, dict):
            raise CartError('Amazon sepeti okunamadı (doğrulama sayfası olabilir)')
        if any(word in normalize_offer_text(str(summary.get('nav') or 'giris')) for word in ('giris', 'sign in')):
            raise CartError("Amazon oturumu kapalı; Hermes Tarayıcı'dan giriş yap")
        self.rows = [row for row in summary.get('rows') or [] if row.get('asin') and row.get('itemid')]
        self.tokens = {'cart': str(summary.get('cart_token') or ''), 'add': str(summary.get('add_token') or '')}

    def row(self, asin):
        return next((row for row in self.rows if row['asin'] == asin), None)

    def others(self, asin):
        return [row for row in self.rows if row['asin'] != asin]


def read_cart(transport):
    return Cart(transport('GET', CART_URL, view='cart'))


def after_post(transport, summary):
    """Amazon answers a cart POST with the updated cart; a read is needed only when it did not."""
    return Cart(summary) if isinstance(summary, dict) else read_cart(transport)


def save_for_later(transport, cart, row):
    name = 'submit.save-for-later.' + row['itemid']
    if not cart.tokens['cart'] or not re.fullmatch(r'submit\.save-for-later\.[A-Za-z0-9_-]{1,120}', name):
        raise CartError('Sonra al düğmesi bulunamadı')
    fields = {'anti-csrftoken-a2z': cart.tokens['cart'], name: row.get('save_value') or 'Daha sonra kaydet'}
    return after_post(transport, transport('POST', SAVE_URL, urlencode(fields), view='cart'))


def offer_from_page(transport, job):
    """The deal's offer id and form token from a logged-in product page, re-checking the price.

    The browser reduces the page to its add-to-cart offers (view='offer'); the
    ~1.6 MB page is never parsed on the Pi.
    """
    summary = transport('GET', job['url'], view='offer')
    if not isinstance(summary, dict) or summary.get('captcha'):
        raise CartError("Amazon ürün sayfası okunamadı (doğrulama sayfası olabilir)")
    offers = (summary.get('depo') or []) if job['warehouse'] else [summary.get('new')]
    target = Decimal(job['target'])
    usable = []
    for offer in offers:
        try:
            if offer and offer.get('offer_id') and offer.get('token') and Decimal(offer.get('price') or 'NaN') <= target:
                usable.append(offer)
        except InvalidOperation:
            continue
    if not usable:
        raise CartError('Teklif artık hedef fiyatta değil veya tükendi; sepet değişmedi')
    best = min(usable, key=lambda offer: Decimal(offer['price']))
    return best['offer_id'], best['token']


def add_offer(transport, token, job, offer_id):
    if not token:
        raise CartError('Amazon sepete ekleme güvenlik alanı bulunamadı; sepet değişmedi')
    fields = {'anti-csrftoken-a2z': token, 'clientName': 'OffersX_OfferDisplay_DetailPage',
              'items[0.base][asin]': job['asin'], 'items[0.base][offerListingId]': offer_id,
              'items[0.base][quantity]': '1'}
    return after_post(transport, transport('POST', ADD_URL, urlencode(fields), view='cart'))


def row_warning(row, job):
    """Why the cart row differs from the deal; empty when it matches.

    Amazon's cart row shows neither seller nor condition, so the Depo offer is
    recognised by its own price.
    """
    try:
        quantity = int(row.get('quantity') or '')
        price = Decimal(str(row.get('price') or ''))
    except (InvalidOperation, ValueError):
        return 'sepetteki fiyat/adet okunamadı'
    if quantity != 1:
        return f'sepette adet {quantity}'
    if price > Decimal(job['target']):
        return 'sepetteki fiyat hedefin üstünde'
    if price != Decimal(job['price']):
        return 'sepetteki fiyat bildirimdekinden farklı'
    return ''


# Amazon's add-to-cart token is session wide; the last one seen covers a cart page
# that happens to show no add-to-cart form (an empty cart can lack them).
_last_add_token = ''


def _remember(cart):
    global _last_add_token
    _last_add_token = cart.tokens['add'] or _last_add_token
    return cart


def check_session(transport=None):
    """One cart read: raises CartError when the session is closed or Amazon asks for verification."""
    _remember(read_cart(transport or BrowserTransport()))


def add_to_cart(job, transport=None):
    transport = transport or BrowserTransport()
    try:
        return _add_to_cart(job, transport)
    finally:
        if isinstance(transport, BrowserTransport):
            log('Sepet adımları (sn): ' + ', '.join(transport.steps))


def _add_to_cart(job, transport):
    cart = _remember(read_cart(transport))
    if len(cart.rows) > MAX_ACTIVE_ITEMS:
        raise CartError('Sepette çok fazla ürün var; sepet değişmedi')
    if cart.row(job['asin']) is not None:
        # The same product already sits in the cart: adding it again would make two.
        cart = _remember(save_for_later(transport, cart, cart.row(job['asin'])))
    # Fast path: the offer id Hermes just read and a token from the cart page.
    offer_id, token = job.get('offer_id'), cart.tokens['add'] or _last_add_token
    if not offer_id or not token:
        offer_id, token = offer_from_page(transport, job)
    cart = add_offer(transport, token, job, offer_id)
    if cart.row(job['asin']) is None and job.get('offer_id'):
        # A stale token or an id read without the session may be refused; retry once from the logged-in page.
        offer_id, token = offer_from_page(transport, job)
        cart = add_offer(transport, token, job, offer_id)
    if cart.row(job['asin']) is None:
        raise CartError('Amazon ürünü sepete eklemedi')
    for _ in range(MAX_ACTIVE_ITEMS):
        others = cart.others(job['asin'])
        if not others:
            break
        cart = _remember(save_for_later(transport, cart, others[0]))
        if len(cart.others(job['asin'])) >= len(others):
            break
    if cart.others(job['asin']):
        raise CartError("Ürün sepete eklendi ama diğerleri Daha sonra kaydet'e taşınamadı", added=True)
    row = cart.row(job['asin'])
    warning = row_warning(row, job) if row is not None else 'ürün sepette görünmüyor'
    return 'Sepette yalnız bu ürün var' + (f' (dikkat: {warning})' if warning else '')
