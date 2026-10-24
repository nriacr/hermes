"""Amazon cart operations through the owner's Pi browser. No checkout actions."""
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

import requests

from ...utils import extract_asin_from_url
from . import parser
from .client import protection_reason
from .common import has_explicit_used_offer_evidence, normalize_offer_text

BRIDGE = Path('/share/hermes-cart/bridge.json')
CART_URL = 'https://www.amazon.com.tr/gp/cart/view.html'


class CartError(Exception):
    def __init__(self, message):
        self.public_message = message
        super().__init__(message)


def payload(watch, offer, url):
    asin = extract_asin_from_url(url)
    if not asin or offer.price > watch.target_price:
        return None
    return {'asin': asin, 'price': str(offer.price), 'warehouse': bool(offer.is_warehouse),
            'seller': offer.seller or '', 'url': 'https://www.amazon.com.tr/dp/' + asin}


class BrowserTransport:
    def __call__(self, method, url, body=''):
        meta = json.loads(BRIDGE.read_text())
        if meta.get('url') != 'http://local-hermes-browser:8098/fetch':
            raise CartError('Alfred tarayıcı bağlantısı geçersiz')
        try:
            response = requests.post(meta['url'], json={'method': method, 'url': url, 'body': body},
                                     headers={'Authorization': 'Bearer ' + meta['token']}, timeout=(2, 12))
            response.raise_for_status()
            result = response.json()
        except Exception:
            raise CartError('Alfred tarayıcısına ulaşılamadı; otomatik tekrar yok') from None
        html = result.get('html', '')
        if result.get('status') != 200 or not html:
            raise CartError('Amazon isteği doğrulanamadı')
        if protection_reason(html):
            raise CartError('Amazon doğrulama istiyor; tarayıcıda kontrol et')
        return html


def cart_row(html, asin):
    soup = parser.parse_product_page(html)
    nav = soup.select_one('#nav-link-accountList-nav-line-1') or soup.select_one('#nav-link-accountList .nav-line-1')
    if not nav or any(word in normalize_offer_text(nav.get_text(' ', strip=True)) for word in ('giris', 'sign in')):
        raise CartError('Amazon hesabı bağlı değil; Alfred tarayıcısında giriş yap')
    rows = soup.select('#sc-active-cart [data-asin], #sc-active-cart-pane [data-asin]')
    return next((row for row in rows if row.get('data-asin') == asin), None)


def verify_row(row, job):
    try:
        price = Decimal(str(row.get('data-price') or row.get('data-item-price') or ''))
        quantity = int(row.get('data-quantity') or '')
    except (InvalidOperation, ValueError):
        raise CartError('Sepetteki birim fiyat/adet doğrulanamadı') from None
    if price != Decimal(job['price']):
        raise CartError('Sepetteki fiyat fırsat fiyatıyla uyuşmuyor')
    if quantity != 1:
        raise CartError('Sepetteki adet 1 değil; mevcut sepete dokunulmadı')
    text = normalize_offer_text(row.get_text(' ', strip=True))
    used = has_explicit_used_offer_evidence(text)
    if used != job['warehouse']:
        raise CartError('Sepetteki ürünün yeni/ikinci el durumu uyuşmuyor')
    if job['warehouse'] and 'amazon depo' not in text:
        raise CartError('Sepette Amazon Depo satıcısı doğrulanamadı')
    if job.get('seller') and normalize_offer_text(job['seller']) not in text:
        raise CartError('Sepetteki satıcı fırsat satıcısıyla uyuşmuyor')


def cart_form(html, job):
    soup = parser.parse_product_page(html)
    if job['warehouse']:
        containers = soup.select('#aod-offer, #aod-pinned-offer, #usedBuySection, #usedAccordionRow, .olpOffer')
        matches = [c for c in containers if 'amazon depo' in normalize_offer_text(c.get_text(' ', strip=True))
                   and has_explicit_used_offer_evidence(c.get_text(' ', strip=True))
                   and parser._offer_container_price(c) == Decimal(job['price'])]
    else:
        offer = parser.extract_offer(html, source_url=job['url'])
        if offer.is_warehouse or offer.price != Decimal(job['price']):
            raise CartError('Amazon teklif fiyatı veya yeni/ikinci el durumu değişti')
        if job.get('seller') and normalize_offer_text(offer.seller or '') != normalize_offer_text(job['seller']):
            raise CartError('Amazon satıcısı değişti')
        matches = [soup]
    forms = {}
    allowed = {'ASIN', 'ASIN.1', 'asin', 'offeringID.1', 'offerListingID', 'quantity', 'quantity.1',
               'submit.addToCart', 'add-to-cart', 'anti-csrftoken-a2z', 'session-id', 'storeID',
               'sellerID', 'smid', 'currencyCode'}
    for container in matches:
        for form in container.select('form'):
            action = urljoin(job['url'], str(form.get('action') or ''))
            parsed = urlsplit(action)
            if parsed.netloc != 'www.amazon.com.tr' or not parsed.path.startswith('/gp/add-to-cart'):
                continue
            fields = {i.get('name'): str(i.get('value') or '') for i in form.select('input[name]')
                      if i.get('name') in allowed and not i.has_attr('disabled')}
            offering = fields.get('offeringID.1') or fields.get('offerListingID')
            if not offering:
                continue
            explicit_asin = fields.get('ASIN') or fields.get('ASIN.1') or fields.get('asin')
            if explicit_asin and explicit_asin != job['asin']:
                continue
            fields['quantity'] = '1'
            if 'quantity.1' in fields:
                fields['quantity.1'] = '1'
            fields['submit.addToCart'] = '1'
            forms[offering] = (action, fields)
    if len(forms) != 1:
        raise CartError('Tek ve kesin Amazon teklifi doğrulanamadı; sepet değişmedi')
    return next(iter(forms.values()))


def save_all_for_later(transport):
    html = transport('GET', CART_URL)
    for _ in range(100):
        # cart_row also verifies that this is the logged-in cart, not a sign-in page.
        cart_row(html, '')
        soup = parser.parse_product_page(html)
        rows = soup.select('#sc-active-cart [data-asin], #sc-active-cart-pane [data-asin]')
        if not rows:
            return
        row = rows[0]
        asin = row.get('data-asin')
        button = row.select_one('input[name^="submit.save-for-later."], button[name^="submit.save-for-later."]')
        form = row.find_parent('form') or soup.select_one('#activeCartViewForm')
        if not button or not form:
            raise CartError('Sonra al işleminin kesin formu bulunamadı; sepet değiştirilmedi')
        fields = {i.get('name'): str(i.get('value') or '') for i in form.select('input[name]')
                  if i.get('name') in {'anti-csrftoken-a2z', 'csrfToken', 'hasMoreItems'}}
        if not any(k in fields for k in ('anti-csrftoken-a2z', 'csrfToken')):
            raise CartError('Sonra al güvenlik alanı bulunamadı')
        fields[button.get('name')] = str(button.get('value') or '1')
        action = urljoin(CART_URL, str(form.get('action') or ''))
        parsed = urlsplit(action)
        if parsed.netloc != 'www.amazon.com.tr' or not (parsed.path == '/cart' or parsed.path.startswith('/cart/ref=')
                or parsed.path == '/gp/cart/view.html'):
            raise CartError('Sonra al adresi doğrulanamadı')
        transport('POST', action, urlencode(fields))
        html = transport('GET', CART_URL)
        check = parser.parse_product_page(html)
        saved = check.select('#sc-saved-cart [data-asin], #sc-saved-cart-items [data-asin]')
        if not any(i.get('data-asin') == asin for i in saved):
            raise CartError('Sonra al taşıması doğrulanamadı; otomatik tekrar yok')
        next_rows = check.select('#sc-active-cart [data-asin], #sc-active-cart-pane [data-asin]')
        if len(next_rows) >= len(rows):
            raise CartError('Sepetteki ürün sayısı azalmadı; işlem durdu')
    raise CartError('Sepette çok fazla ürün var; işlem sınırında durdu')


def add_to_cart(job, transport=None):
    transport = transport or BrowserTransport()
    save_all_for_later(transport)
    url = ('https://www.amazon.com.tr/gp/offer-listing/' + job['asin'] + '?condition=used'
           if job['warehouse'] else job['url'])
    action, fields = cart_form(transport('GET', url), job)
    transport('POST', action, urlencode(fields))
    row = cart_row(transport('GET', CART_URL), job['asin'])
    if row is None:
        raise CartError('Sepete ekleme doğrulanamadı; otomatik tekrar yok')
    verify_row(row, job)
    return 'Sepete 1 adet eklendi; fırsat fiyatı ve teklif doğrulandı'
