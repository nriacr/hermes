import urllib.parse
from html import escape

from .service import inspect_link_now
from .utils import format_tl, site_label


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
            "<tr>"
            f"<td>{escape(seller)}</td>"
            f"<td>{title_html}</td>"
            f"<td>{escape(format_tl(offer.price, with_currency=True))}</td>"
            "</tr>"
        )
    return "".join(rows)


def render_link_test_page(css, action_path, back_path, url="", site="", offers=None, error="") -> bytes:
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
          <div class='table-wrap'><table>
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
        <div class='summary-head'><h2>Bağlantı testi</h2><span>Kayıt oluşturmaz, bildirim göndermez</span></div>
        <form method='post' action='{escape(action_path, quote=True)}' class='link-test-form'>
          <label>Ürün veya arama bağlantısı
            <input type='url' name='url' value='{escape(source_url, quote=True)}' placeholder='https://...' required>
          </label>
          <button class='button primary' type='submit'>Şimdi Test Et</button>
        </form>
      </section>
      {result_html}
    </div></main></body></html>"""
    return html.encode("utf-8")


def render_link_test_from_request(css, action_path, back_path, body) -> bytes:
    form = urllib.parse.parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
    url = str(form.get("url", [""])[0]).strip()
    try:
        site, offers = inspect_link_now(url)
        return render_link_test_page(css, action_path, back_path, url=url, site=site, offers=offers)
    except Exception as exc:  # noqa: BLE001
        return render_link_test_page(css, action_path, back_path, url=url, error=f"Bağlantı okunamadı: {exc}")
