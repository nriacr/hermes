import urllib.parse
from html import escape

from .service import inspect_link_now
from .utils import format_tl, site_label


SITE_THEME_CLASSES = {
    "amazon": "site-amazon",
    "hepsiburada": "site-hepsiburada",
    "trendyol": "site-trendyol",
    "network": "site-network",
    "nordbron": "site-nordbron",
    "zara": "site-zara",
    "hm": "site-hm",
}


def _render_offer_rows(site, offers, source_url):
    rows = []
    for offer in offers:
        seller = str(offer.seller or site_label(site) or "-").strip()
        title = str(offer.title or "Ürün adı okunamadı").strip()
        url = str(offer.url or source_url or "").strip()
        title_html = escape(title)
        if url:
            title_html = (
                f"<a href='{escape(url, quote=True)}' target='_blank' rel='noopener noreferrer'>"
                f"{title_html}</a>"
            )
        rows.append(
            f"<tr class='{SITE_THEME_CLASSES.get(site, 'site-other')}'>"
            f"<td data-label='Satıcı' class='seller-cell'>{escape(seller)}</td>"
            f"<td data-label='Ürün' class='product-cell'>{title_html}</td>"
            f"<td data-label='Fiyat' class='price-cell'>{escape(format_tl(offer.price, with_currency=True))}</td>"
            "</tr>"
        )
    return "".join(rows)


def _text_value(value) -> str:
    return str(value or "").strip()


def _checked(enabled: bool) -> str:
    return " checked" if enabled else ""


def render_link_test_page(
    css,
    action_path,
    back_path,
    url="",
    name="",
    size="",
    exclude_terms="",
    include_variations=False,
    site="",
    offers=None,
    error="",
) -> bytes:
    """Render an on-demand provider test without persisting any result."""
    source_url = str(url or "").strip()
    result_html = ""
    if error:
        result_html = f"<p class='notice notice-fail'>{escape(str(error))}</p>"
    elif offers is not None:
        rows = _render_offer_rows(site, offers, source_url)
        result_html = f"""
        <section class='summary-panel link-test-result'>
          <div class='summary-head'><h2>Test sonuçları</h2><span>{len(offers)} ürün</span></div>
          <div class='table-wrap link-test-table'><table>
            <thead><tr><th>Satıcı</th><th>Ürün adı</th><th>Fiyat</th></tr></thead>
            <tbody>{rows}</tbody>
          </table></div>
        </section>
        """

    html = f"""<!doctype html>
    <html lang='tr'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1, viewport-fit=cover'><meta name='theme-color' content='#111315'><title>Hermes Bağlantı Testi</title><style>{css}</style></head>
    <body><main><div class='hero'>
      <div class='badge'>Hermes</div>
      <div class='actions'><a class='button secondary' href='{escape(back_path, quote=True)}'>Ana ekran</a></div>
      <section class='summary-panel link-test-panel'>
        <div class='summary-head'><h2>Bağlantı testi</h2><span>Geçici sonuçlar. Kayıt ve bildirim oluşturmaz.</span></div>
        <form method='post' action='{escape(action_path, quote=True)}' class='link-test-form'>
          <label class='link-test-url'>Ürün veya arama bağlantısı
            <input type='url' name='url' value='{escape(source_url, quote=True)}' placeholder='https://...' required>
          </label>
          <div class='link-test-options'>
            <label>Ad / arama anahtar kelimesi
              <input type='text' name='name' value='{escape(_text_value(name), quote=True)}' placeholder='Arama linklerinde isteğe bağlı'>
            </label>
            <label>Beden
              <input type='text' name='size' value='{escape(_text_value(size), quote=True)}' placeholder='Örn. XL veya 44'>
            </label>
            <label>Hariç tut
              <input type='text' name='exclude_terms' value='{escape(_text_value(exclude_terms), quote=True)}' placeholder='Kılıf, koruyucu'>
            </label>
            <label class='link-test-checkbox'><input type='checkbox' name='include_variations' value='1'{_checked(bool(include_variations))}> Varyasyonları ekle</label>
          </div>
          <button class='button primary' type='submit'>Şimdi Test Et</button>
        </form>
      </section>
      {result_html}
    </div></main></body></html>"""
    return html.encode("utf-8")


def render_link_test_from_request(css, action_path, back_path, body) -> bytes:
    form = urllib.parse.parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
    url = str(form.get("url", [""])[0]).strip()
    name = str(form.get("name", [""])[0]).strip()
    size = str(form.get("size", [""])[0]).strip()
    exclude_terms = str(form.get("exclude_terms", [""])[0]).strip()
    include_variations = str(form.get("include_variations", [""])[0]).strip() in {"1", "true", "on", "yes"}
    excluded_terms = [item.strip() for item in exclude_terms.split(",") if item.strip()]
    try:
        site, offers = inspect_link_now(
            url,
            name=name,
            size=size,
            include_variations=include_variations,
            excluded_terms=excluded_terms,
        )
        return render_link_test_page(
            css,
            action_path,
            back_path,
            url=url,
            name=name,
            size=size,
            exclude_terms=exclude_terms,
            include_variations=include_variations,
            site=site,
            offers=offers,
        )
    except Exception as exc:  # noqa: BLE001
        return render_link_test_page(
            css,
            action_path,
            back_path,
            url=url,
            name=name,
            size=size,
            exclude_terms=exclude_terms,
            include_variations=include_variations,
            error=f"Bağlantı okunamadı: {exc}",
        )
