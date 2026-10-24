"""Amazon cart operations through the owner's Pi browser. No checkout actions.

The deal is added first (Amazon Depo stock goes fast), then every other active
cart item is moved to "Daha sonra al", so the deal is the only item left.
"""
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlencode

import requests

from ...utils import extract_asin_from_url
from . import parser
from .client import protection_reason
from .common import normalize_offer_text

BRIDGE = Path('/share/hermes-cart/bridge.json')
BRIDGE_URL = 'http://local-hermes-browser:8098/fetch'
CART_URL = 'https://www.amazon.com.tr/gp/cart/view.html'
ADD_URL = 'https://www.amazon.com.tr/cart/add-to-cart/ref=dp_start-bbf_1_glance'
SAVE_URL = 'https://www.amazon.com.tr/cart/ref=ord_cart_shr'
ACTIVE_ROWS = '#sc-active-cart [data-itemtype="active"][data-asin]'
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

    def __call__(self, method, url, body=''):
        try:
            meta = json.loads(BRIDGE.read_text())
        except (OSError, ValueError):
            raise CartError("Hermes Tarayıcı çalışmıyor; Alfred'de uygulamayı başlat") from None
        if meta.get('url') != BRIDGE_URL:
            raise CartError('Hermes Tarayıcı bağlantısı geçersiz')
        try:
            response = requests.post(meta['url'], json={'method': method, 'url': url, 'body': body},
                                     headers={'Authorization': 'Bearer ' + meta['token']}, timeout=(2, 15))
            result = response.json()
        except Exception:
            raise CartError('Hermes Tarayıcı yanıt vermedi') from None
        if result.get('error'):
            raise CartError('Hermes Tarayıcı isteği yapamadı (' + str(result['error'])[:40] + ')')
        html = result.get('html', '')
        status = result.get('status')
        # A POST is not followed (status 0 = redirect); the caller re-reads the cart.
        if method == 'GET' and (status != 200 or not html):
            raise CartError('Amazon sayfası okunamadı')
        if method == 'POST' and status not in (0, 200, 302, 303):
            raise CartError('Amazon sepet isteğini kabul etmedi')
        if html and protection_reason(html):
            raise CartError("Amazon doğrulama istiyor; Hermes Tarayıcı'da kontrol et")
        return html


class Cart:
    """One parsed, logged-in cart page."""

    def __init__(self, html):
        self.soup = parser.parse_product_page(html)
        nav = self.soup.select_one('#nav-link-accountList-nav-line-1')
        if nav is None or any(word in normalize_offer_text(nav.get_text(' ', strip=True)) for word in ('giris', 'sign in')):
            raise CartError("Amazon oturumu kapalı; Hermes Tarayıcı'dan giriş yap")
        if self.soup.select_one('#sc-active-cart') is None:
            raise CartError('Amazon sepet sayfası tanınamadı')
        self.rows = self.soup.select(ACTIVE_ROWS)

    def token(self, selector):
        field = self.soup.select_one(selector + ' input[name="anti-csrftoken-a2z"]')
        return str(field['value']) if field is not None and field.get('value') else ''

    def row(self, asin):
        return next((row for row in self.rows if row.get('data-asin') == asin), None)

    def others(self, asin):
        return [row for row in self.rows if row.get('data-asin') != asin]


def after_post(transport, html):
    """Amazon answers a cart POST with the updated cart; a GET is needed only when it did not."""
    try:
        return Cart(html)
    except CartError:
        return Cart(transport('GET', CART_URL))


def save_for_later(transport, cart, row):
    button = row.select_one('input[name^="submit.save-for-later."]')
    token = cart.token('#activeCartViewForm')
    if button is None or not token or not re.fullmatch(r'submit\.save-for-later\.[A-Za-z0-9_-]{1,120}', button['name']):
        raise CartError('Sonra al düğmesi bulunamadı')
    fields = {'anti-csrftoken-a2z': token, button['name']: str(button.get('value') or 'Daha sonra kaydet')}
    return after_post(transport, transport('POST', SAVE_URL, urlencode(fields)))


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
    return after_post(transport, transport('POST', ADD_URL, urlencode(fields)))


def row_warning(row, job):
    """Why the cart row differs from the deal; empty when it matches.

    Amazon's cart row shows neither seller nor condition, so the Depo offer is
    recognised by its own price.
    """
    try:
        quantity = int(row.get('data-quantity') or '')
        price = Decimal(str(row.get('data-price') or ''))
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
    cart = Cart(transport('GET', CART_URL))
    if len(cart.rows) > MAX_ACTIVE_ITEMS:
        raise CartError('Sepette çok fazla ürün var; sepet değişmedi')
    if cart.row(job['asin']) is not None:
        # The same product already sits in the cart: adding it again would make two.
        cart = save_for_later(transport, cart, cart.row(job['asin']))
    # Fast path: the offer id Hermes just read and the cart page's own add-to-cart token.
    offer_id, token = job.get('offer_id'), cart.token('form.add-to-cart-data')
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
