import urllib.parse
import json
from html import escape

from . import amazon_transport_trial
from .amazon_browser_check import run_browser_check
from .storage import load_json
from .errors import EmptySearchResultsHermesError, OutOfStockHermesError
from .service import inspect_link_now
from .utils import format_tl, site_label


SITE_THEME_CLASSES = {
    "amazon": "site-amazon",
    "hepsiburada": "site-hepsiburada",
    "trendyol": "site-trendyol",
    "network": "site-network",
    "beymenclub": "site-beymenclub",
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


def _render_transport_trial(action_path):
    active = amazon_transport_trial.active_trial()
    control = load_json(amazon_transport_trial.CONTROL_PATH, {})
    results = load_json(amazon_transport_trial.RESULTS_PATH, {})
    if not isinstance(control, dict):
        control = {}
    if not isinstance(results, dict) or results.get("id") != control.get("id"):
        results = {}
    report = amazon_transport_trial.summarize(results)
    validation = control.get("purpose") == "browser_validation"
    status = (("Çalışıyor · Pi tarayıcısı kapsam kontrolü" if validation else
               f"Çalışıyor · dönem {active['phase'] + 1}/24 · "
               + ("Pi Chromium" if active["transport"] == "browser" else "mevcut okuyucu")) if active else "Çalışmıyor")
    rows = []
    for key, label in (("http", "Mevcut okuyucu"), ("browser", "Pi Chromium")):
        mode = report["modes"][key]
        rows.append(f"<tr><td data-label='Yöntem'>{label}</td>"
                    f"<td data-label='Kontrol'>{mode['reads']}</td><td data-label='Ağ isteği'>{mode['network_attempts']}</td>"
                    f"<td data-label='CAPTCHA'>{mode['captcha']}</td><td data-label='503'>{mode['http_503']}</td>"
                    f"<td data-label='Fiyat okunan'>{mode['priced']}</td><td data-label='Tipik süre'>{mode['median_seconds']} sn</td></tr>")
    action = "stop" if active else "validate" if validation else "start"
    button = "Testi durdur" if active else "1 saatlik doğrulamayı başlat" if validation else "24 saatlik karşılaştırmayı başlat"
    data = {"control": control, "active": active, "summary": report,
            "updated_at": results.get("updated_at"), "cycles": results.get("cycles", [])[-8:]}
    card_rows = "".join(
        f"<tr><td data-label='Kart'>{escape(card['name'])}</td>"
        f"<td data-label='Yöntem'>{label}</td><td data-label='Kontrol'>{card['modes'][mode]['reads']}</td>"
        f"<td data-label='Tipik varyant'>{card['modes'][mode]['median_variants']}</td>"
        f"<td data-label='Depo bulunan kontrol'>{card['modes'][mode]['warehouse_reads']}</td></tr>"
        for card in report["cards"] for mode, label in (("http", "Mevcut okuyucu"), ("browser", "Pi Chromium")))
    validation_rows = "".join(f"<tr><td>{escape(card['name'])}</td><td>{card['reads']}</td><td>{card['priced']}</td>"
        f"<td>{card['audits']}</td><td>{card['late_data']}</td><td>{card['median_seconds']} sn</td></tr>"
        for card in report["browser_validation"])
    validation_html = ("<p>Bir saat boyunca sırası gelen normal taramalar Pi tarayıcısıyla okunur. "
        "İlk okumada ve her onuncu okumada aynı sayfanın erken ve tam yüklenmiş verileri karşılaştırılır; "
        "fark varsa tam veri kullanılır. Ek Amazon sorgusu yapılmaz. Süre bitince mevcut okuyucuya dönülür.</p>"
        "<div class='table-wrap'><table><thead><tr><th>Kart</th><th>Kontrol</th><th>Fiyat okunan</th>"
        "<th>Kapsam kontrolü</th><th>Geç veri</th><th>Tipik süre</th></tr></thead>"
        f"<tbody>{validation_rows}</tbody></table></div>") if validation else ""
    validation_button = (f"<form method='post' action='{escape(action_path, quote=True)}'>"
        "<input type='hidden' name='amazon_trial_action' value='validate'>"
        "<button class='button secondary' type='submit'>Pi tarayıcısını 1 saat doğrula</button></form>") if not active else ""
    if validation:
        return (f"<section class='summary-panel link-test-result'><div class='summary-head'>"
                f"<h2>Amazon tarayıcısı kapsam doğrulaması</h2><span>{escape(status)}</span></div>"
                f"{validation_html}<form method='post' action='{escape(action_path, quote=True)}'>"
                f"<input type='hidden' name='amazon_trial_action' value='{action}'>"
                f"<button class='button secondary' type='submit'>{button}</button></form>"
                f"<p>Koruma beklemesi örneği: {report['protection_waits']}. Bekleyen ve yalnızca önbellekten "
                "gelen okumalar kontrol sayısına dahil edilmez. Kapsam kontrolü aynı sayfanın iki zamanını karşılaştırır; "
                "tüm ürün ailesinin eksiksizliğini veya CAPTCHA'nın önleneceğini garanti etmez.</p>"
                "<script type='application/json' id='amazon-trial-data'>"
                f"{json.dumps(data, ensure_ascii=False).replace('<', chr(92) + 'u003c')}</script></section>")
    return f"""<section class='summary-panel link-test-result'>
      <div class='summary-head'><h2>Amazon okuyucu karşılaştırması</h2><span>{escape(status)}</span></div>
      <p>Normal taramalar ölçülür; ek ürün sorgusu yapılmaz. Öncelikler, filtreler ve koruma beklemeleri korunur.
      Fırsat bildirimleri devam eder. Saatlik dönemlerde iki yöntem karşılaştırılır; 24 saat sonunda mevcut okuyucuya dönülür.</p>
      <form method='post' action='{escape(action_path, quote=True)}'>
        <input type='hidden' name='amazon_trial_action' value='{action}'>
        <button class='button secondary' type='submit'>{button}</button>
      </form>
      {validation_button}
      {validation_html}
      <p>{report['matched_cards']} kart her iki yöntemle okundu. Koruma beklemesi kaydı: {report['protection_waits']}.
      İki yöntemle henüz okunmayan kontroller: {report['unmatched_reads']}.</p>
      <div class='table-wrap'><table><thead><tr><th>Yöntem</th><th>Kontrol</th><th>Ağ isteği</th>
      <th>CAPTCHA</th><th>503</th><th>Fiyat okunan</th><th>Tipik süre</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
      <p>Sayılar aynı ayarlarla her iki yöntemle kontrol edilen kartları kapsar. Önbellekten ve koruma beklemesinden gelen
      sonuçlar başarı sayılmaz. Kısa test ve az örnek kesin sonuç vermez; varyant ve depo kapsamı da değerlendirilir.</p>
      <details><summary>Kart ve depo kapsamı</summary><div class='table-wrap'><table><thead><tr><th>Kart</th><th>Yöntem</th>
      <th>Kontrol</th><th>Tipik varyant</th><th>Depo bulunan kontrol</th></tr></thead><tbody>{card_rows}</tbody></table></div></details>
      <script type='application/json' id='amazon-trial-data'>{json.dumps(data, ensure_ascii=False).replace('<', chr(92) + 'u003c')}</script>
    </section>"""


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
    amazon_browser=False,
    unavailable="",
    browser_check=None,
) -> bytes:
    """Render an on-demand provider test without persisting any result."""
    source_url = str(url or "").strip()
    result_html = ""
    check_html = ""
    if browser_check is not None:
        check_html = (f"<section class='summary-panel'><h2>Pi tarayıcısı kontrolü</h2>"
                      f"<p>{'Kontroller geçti.' if browser_check.get('passed') else 'Kontrol başarısız; ayrıntılar kaydedildi.'} "
                      "Amazon’a sorgu gönderilmedi. Bu kontrol gerçek Amazon çevrim süresini ölçmez.</p>"
                      "<script type='application/json' id='amazon-browser-check-data'>"
                      f"{json.dumps(browser_check, ensure_ascii=False).replace('<', chr(92) + 'u003c')}</script></section>")
    if unavailable:
        result_html = f"<p class='notice'>{escape(str(unavailable))}</p>"
    elif error:
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
            <label>Hariç Tut
              <input type='text' name='exclude_terms' value='{escape(_text_value(exclude_terms), quote=True)}' placeholder='Kılıf, koruyucu'>
            </label>
            <label class='link-test-checkbox'><input type='checkbox' name='include_variations' value='1'{_checked(bool(include_variations))}> Varyasyonları ekle</label>
          </div>
          <label class='link-test-checkbox'><input type='checkbox' name='amazon_browser' value='1'{_checked(bool(amazon_browser))}> Amazon bağlantısını Pi’de gerçek tarayıcıyla test et</label>
          <button class='button primary' type='submit'>Şimdi Test Et</button>
        </form>
      </section>
      {result_html}
      {check_html}
      {_render_transport_trial(action_path)}
    </div></main></body></html>"""
    return html.encode("utf-8")


def render_link_test_from_request(css, action_path, back_path, body) -> bytes:
    form = urllib.parse.parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
    if str(form.get("amazon_browser_check", [""])[0]) == "1":
        return render_link_test_page(css, action_path, back_path, browser_check=run_browser_check())
    trial_action = str(form.get("amazon_trial_action", [""])[0])
    if trial_action in {"start", "stop", "report", "validate"}:
        if trial_action in {"start", "validate"}:
            amazon_transport_trial.start_trial(purpose="browser_validation" if trial_action == "validate" else "reader_comparison")
        elif trial_action == "stop":
            amazon_transport_trial.stop_trial()
        return render_link_test_page(css, action_path, back_path)
    url = str(form.get("url", [""])[0]).strip()
    name = str(form.get("name", [""])[0]).strip()
    size = str(form.get("size", [""])[0]).strip()
    exclude_terms = str(form.get("exclude_terms", [""])[0]).strip()
    include_variations = str(form.get("include_variations", [""])[0]).strip() in {"1", "true", "on", "yes"}
    amazon_browser = str(form.get("amazon_browser", [""])[0]).strip() in {"1", "true", "on", "yes"}
    excluded_terms = [item.strip() for item in exclude_terms.split(",") if item.strip()]
    try:
        site, offers = inspect_link_now(
            url,
            name=name,
            size=size,
            include_variations=include_variations,
            excluded_terms=excluded_terms,
            **({"amazon_browser": True} if amazon_browser else {}),
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
            amazon_browser=amazon_browser,
        )
    except (OutOfStockHermesError, EmptySearchResultsHermesError) as exc:
        notice = (f"Stokta yok: {exc.product_title or url} — {exc}"
                  if isinstance(exc, OutOfStockHermesError) else f"Ürün bulunamadı: {exc}")
        return render_link_test_page(css, action_path, back_path, url=url, name=name, size=size,
                                     exclude_terms=exclude_terms, include_variations=include_variations,
                                     amazon_browser=amazon_browser, unavailable=notice)
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
            amazon_browser=amazon_browser,
        )
