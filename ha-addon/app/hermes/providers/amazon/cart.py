"""Amazon cart operations through the owner's Pi browser. No checkout actions.

The deal is added first (Amazon Depo stock goes fast), then every other active
cart item is moved to "Daha sonra al", so the deal is the only item left.
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
from . import parser
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
    if not asin or offer.price > watch.target_price:
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
        if view == 'cart':
            # The browser already reduced the page to a cart summary (None: not a cart page).
            if status not in (0, 200, 302, 303):
                raise CartError('Amazon sepet isteğini kabul etmedi')
            return result.get('cart')
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
    """The deal's listing id and form token from a logged-in read, re-checking price and condition."""
    if job['warehouse']:
        html = transport('GET', f"https://www.amazon.com.tr/gp/offer-listing/{job['asin']}?condition=used")
        offers = parser.extract_verified_warehouse_offers_from_listing(html, source_url=job['url'])
    else:
        html = transport('GET', job['url'])
        offers = [parser.extract_offer(html, source_url=job['url'])]
    target = Decimal(job['target'])
    usable = [offer for offer in offers if offer.offer_id and offer.is_warehouse == job['warehouse'] and offer.price <= target]
    if not usable:
        raise CartError('Teklif artık hedef fiyatta değil veya tükendi; sepet değişmedi')
    offer_id = min(usable, key=lambda offer: offer.price).offer_id
    field = parser.parse_product_page(html).select_one(
        f'form:has(input[value="{offer_id}"]) input[name="anti-csrftoken-a2z"]')
    return offer_id, str(field.get('value') or '') if field is not None else ''


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


def add_to_cart(job, transport=None):
    transport = transport or BrowserTransport()
    try:
        return _add_to_cart(job, transport)
    finally:
        if isinstance(transport, BrowserTransport):
            log('Sepet adımları (sn): ' + ', '.join(transport.steps))


def _add_to_cart(job, transport):
    cart = read_cart(transport)
    if len(cart.rows) > MAX_ACTIVE_ITEMS:
        raise CartError('Sepette çok fazla ürün var; sepet değişmedi')
    if cart.row(job['asin']) is not None:
        # The same product already sits in the cart: adding it again would make two.
        cart = save_for_later(transport, cart, cart.row(job['asin']))
    # Fast path: the offer id Hermes just read and the cart page's own add-to-cart token.
    offer_id, token = job.get('offer_id'), cart.tokens['add']
    if not offer_id or not token:
        offer_id, token = offer_from_page(transport, job)
    cart = add_offer(transport, token, job, offer_id)
    if cart.row(job['asin']) is None and job.get('offer_id'):
        # An id read without the session may be refused; retry once with the logged-in page's id.
        offer_id, token = offer_from_page(transport, job)
        cart = add_offer(transport, token, job, offer_id)
    if cart.row(job['asin']) is None:
        raise CartError('Amazon ürünü sepete eklemedi')
    for _ in range(MAX_ACTIVE_ITEMS):
        others = cart.others(job['asin'])
        if not others:
            break
        cart = save_for_later(transport, cart, others[0])
        if len(cart.others(job['asin'])) >= len(others):
            break
    if cart.others(job['asin']):
        raise CartError("Ürün sepete eklendi ama diğerleri Sonra al'a taşınamadı", added=True)
    warning = row_warning(cart.row(job['asin']), job) if cart.row(job['asin']) is not None else 'ürün sepette görünmüyor'
    return 'Sepette yalnız bu ürün var' + (f' (dikkat: {warning})' if warning else '')
