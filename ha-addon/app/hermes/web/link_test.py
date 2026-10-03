"""Link test page: read any supported link now, without saving or notifying."""

import urllib.parse
from html import escape
from typing import List, Optional

from ..errors import EmptySearchResultsHermesError, OutOfStockHermesError
from ..models import OfferResult
from ..monitor.inspect import inspect_link
from ..utils import format_tl, site_label
from .dashboard import site_theme_class
from .pages import link, render_page

TRUE_VALUES = {"1", "true", "on", "yes"}


def _rows(site: str, offers: List[OfferResult], source_url: str) -> str:
    rows = []
    for offer in offers:
        seller = str(offer.seller or site_label(site) or "-").strip()
        title_html = escape(str(offer.title or "Ürün adı okunamadı").strip())
        url = str(offer.url or source_url or "").strip()
        if url:
            title_html = f"<a href='{escape(url, quote=True)}' target='_blank' rel='noopener noreferrer'>{title_html}</a>"
        rows.append(f"<tr class='{site_theme_class(site_label(site))}'><td data-label='Satıcı' class='seller-cell'>{escape(seller)}</td>"
                    f"<td data-label='Ürün' class='product-cell'>{title_html}</td>"
                    f"<td data-label='Fiyat' class='price-cell'>{escape(format_tl(offer.price, with_currency=True))}</td></tr>")
    return "".join(rows)


def render_link_test_page(base: str, url: str = "", name: str = "", size: str = "", exclude_terms: str = "",
                          include_variations: bool = False, amazon_browser: bool = False, site: str = "",
                          offers: Optional[List[OfferResult]] = None, error: str = "", unavailable: str = "") -> bytes:
    source_url = str(url or "").strip()
    result_html = ""
    if unavailable:
        result_html = f"<p class='notice'>{escape(unavailable)}</p>"
    elif error:
        result_html = f"<p class='notice notice-fail'>{escape(error)}</p>"
    elif offers is not None:
        result_html = (
            "<section class='summary-panel link-test-result'><div class='summary-head'><h2>Test sonuçları</h2>"
            f"<span>{len(offers)} ürün</span></div><div class='table-wrap link-test-table'><table><thead><tr>"
            f"<th>Satıcı</th><th>Ürün adı</th><th>Fiyat</th></tr></thead><tbody>{_rows(site, offers, source_url)}</tbody></table></div></section>"
        )

    def checked(enabled: bool) -> str:
        return " checked" if enabled else ""

    body = (
        "<section class='summary-panel link-test-panel'><div class='summary-head'><h2>Bağlantı testi</h2>"
        "<span>Geçici sonuçlar. Kayıt ve bildirim oluşturmaz.</span></div>"
        f"<form method='post' action='{escape(link(base, 'link-test'), quote=True)}' class='link-test-form'>"
        "<label class='link-test-url'>Ürün veya arama bağlantısı"
        f"<input type='url' name='url' value='{escape(source_url, quote=True)}' placeholder='https://...' required></label>"
        "<div class='link-test-options'>"
        f"<label>Ad / arama anahtar kelimesi<input type='text' name='name' value='{escape(name, quote=True)}' placeholder='Arama linklerinde isteğe bağlı'></label>"
        f"<label>Beden<input type='text' name='size' value='{escape(size, quote=True)}' placeholder='Örn. XL veya 44'></label>"
        f"<label>Hariç Tut<input type='text' name='exclude_terms' value='{escape(exclude_terms, quote=True)}' placeholder='Kılıf, koruyucu'></label>"
        f"<label class='link-test-checkbox'><input type='checkbox' name='include_variations' value='1'{checked(include_variations)}> Varyasyonları ekle</label>"
        "</div>"
        f"<label class='link-test-checkbox'><input type='checkbox' name='amazon_browser' value='1'{checked(amazon_browser)}> "
        "Amazon bağlantısını Pi’de gerçek tarayıcıyla test et</label>"
        "<button class='button primary' type='submit'>Şimdi Test Et</button></form></section>"
        f"{result_html}"
    )
    return render_page(base, "link-test", "Hermes Bağlantı Testi", body)


def render_link_test_result(base: str, body: bytes) -> bytes:
    form = urllib.parse.parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)

    def value(key: str) -> str:
        return str(form.get(key, [""])[0]).strip()

    fields = {
        "url": value("url"),
        "name": value("name"),
        "size": value("size"),
        "exclude_terms": value("exclude_terms"),
        "include_variations": value("include_variations") in TRUE_VALUES,
        "amazon_browser": value("amazon_browser") in TRUE_VALUES,
    }
    try:
        site, offers = inspect_link(
            fields["url"], name=fields["name"], size=fields["size"], include_variations=fields["include_variations"],
            excluded_terms=[item.strip() for item in fields["exclude_terms"].split(",") if item.strip()],
            amazon_browser=fields["amazon_browser"],
        )
        return render_link_test_page(base, site=site, offers=offers, **fields)
    except OutOfStockHermesError as exc:
        return render_link_test_page(base, unavailable=f"Stokta yok: {exc.product_title or fields['url']} — {exc}", **fields)
    except EmptySearchResultsHermesError as exc:
        return render_link_test_page(base, unavailable=f"Ürün bulunamadı: {exc}", **fields)
    except Exception as exc:  # noqa: BLE001
        return render_link_test_page(base, error=f"Bağlantı okunamadı: {exc}", **fields)
