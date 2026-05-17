import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .constants import OPTIONS_PATH, PUSHOVER_URL, STATE_PATH, SUMMARY_PATH
from .storage import load_json
from .utils import parse_iso_datetime, repair_mojibake

WEB_PORT = 8099
ADDON_SLUG = "hermes"


def _parse_turkish_money(value):
    text = str(value or "").strip().replace("TL", "").replace(" ", "")
    text = text.replace("+", "").replace(".", "").replace(",", ".")
    if not text or text == "-":
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _is_target_hit(row):
    explicit = row.get("is_target_hit")
    if isinstance(explicit, bool):
        return explicit
    price = _parse_turkish_money(row.get("price"))
    target = _parse_turkish_money(row.get("target"))
    if price is not None and target is not None:
        return price <= target
    diff = _parse_turkish_money(row.get("difference"))
    return diff is not None and diff <= 0


def _current_addon_slug():
    hostname = os.getenv("HOSTNAME", "").strip()
    hyphen_slug = ADDON_SLUG.replace("_", "-")
    if hostname.endswith(f"-{hyphen_slug}"):
        repository_id = hostname[: -(len(hyphen_slug) + 1)]
        if repository_id:
            return f"{repository_id}_{ADDON_SLUG}"
    return hostname.replace("-", "_") if hostname else f"local_{ADDON_SLUG}"


def _addon_urls():
    slug = urllib.parse.quote(_current_addon_slug(), safe="")
    return f"/config/app/{slug}/logs", f"/config/app/{slug}/config"


def _collect_summary():
    options = load_json(OPTIONS_PATH, {})
    state = load_json(STATE_PATH, {})
    products = options.get("products") if isinstance(options.get("products"), list) else []
    pages = options.get("amazon_search_pages", options.get("search_pages", []))
    targets = options.get("amazon_search_targets", options.get("search_targets", []))
    pages = pages if isinstance(pages, list) else []
    targets = targets if isinstance(targets, list) else []

    error_cutoff = timedelta(hours=24)
    now = datetime.now().astimezone()
    last_checks = []
    error_count = 0
    if isinstance(state, dict):
        for key, value in state.items():
            if key == "_meta" or not isinstance(value, dict):
                continue
            checked_at = parse_iso_datetime(value.get("last_checked_at"))
            if checked_at:
                last_checks.append(checked_at.astimezone())
                if value.get("last_error") and now - checked_at.astimezone() <= error_cutoff:
                    error_count += 1
            nested = value.get("targets")
            if isinstance(nested, dict):
                for target_state in nested.values():
                    if not isinstance(target_state, dict):
                        continue
                    checked_at = parse_iso_datetime(target_state.get("last_checked_at"))
                    if checked_at:
                        last_checks.append(checked_at.astimezone())

    return {
        "interval": options.get("interval_minutes", "-"),
        "products": len(products),
        "amazon_pages": len(pages),
        "amazon_targets": len(targets),
        "last_check": max(last_checks).strftime("%Y-%m-%d %H:%M:%S") if last_checks else "-",
        "errors": error_count,
        "configured": bool(options),
    }


def _render_table():
    payload = load_json(SUMMARY_PATH, {})
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    if not rows:
        return """
        <section class="summary-panel">
          <div class="summary-head"><h2>Son Fiyat Ozeti</h2><span>Henuz tablo yok</span></div>
          <p class="empty-table">Ilk kontrol dongusu tamamlandiginda son fiyat tablosu burada gorunecek.</p>
        </section>
        """

    row_html = []
    for row in rows:
        seller = escape(repair_mojibake(row.get("seller") or "-"))
        product_title = escape(repair_mojibake(row.get("product_title") or "-"))
        product_url = str(row.get("product_url") or "").strip()
        if product_url:
            label = (
                f'<a href="{escape(product_url, quote=True)}" target="_blank" rel="noopener noreferrer">'
                f"<span>{product_title}</span></a>"
            )
        else:
            label = f"<span>{product_title}</span>"
        price = escape(str(row.get("price", "-")))
        target = escape(str(row.get("target", "-")))
        difference = escape(str(row.get("difference", "-")))
        row_class = ' class="deal-row"' if _is_target_hit(row) else ""
        row_html.append(
            f"<tr{row_class}><td>{seller}</td>"
            f'<td class="product-cell" title="{product_title}">{label}</td>'
            f"<td>{price}</td><td>{target}</td><td>{difference}</td></tr>"
        )

    checked_at = escape(str(payload.get("checked_at") or "-"))
    row_count = escape(str(payload.get("row_count") or len(rows)))
    return f"""
    <section class="summary-panel">
      <div class="summary-head"><h2>Son Fiyat Ozeti</h2><span>{checked_at} · {row_count} urun</span></div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Satici</th><th>Urun Adi</th><th>Fiyat</th><th>Hedef</th><th>Fark</th></tr></thead>
          <tbody>{''.join(row_html)}</tbody>
        </table>
      </div>
    </section>
    """


def _send_test_notification():
    options = load_json(OPTIONS_PATH, {})
    user_key = str(options.get("pushover_user_key", "")).strip()
    api_token = str(options.get("pushover_api_token", "")).strip()
    timeout = int(options.get("request_timeout_seconds", 20) or 20)
    if not user_key or not api_token:
        return False, "Pushover anahtarlari eksik. Config sekmesini kontrol et."
    payload = urllib.parse.urlencode(
        {
            "token": api_token,
            "user": user_key,
            "title": "Hermes test",
            "message": "Hermes test bildirimi. Ayarlar saglikli gorunuyor.",
            "sound": "pushover",
            "priority": "0",
        }
    ).encode("utf-8")
    try:
        with urllib.request.urlopen(
            urllib.request.Request(PUSHOVER_URL, data=payload, method="POST"), timeout=timeout
        ) as response:
            response.read()
        return True, "Pushover test bildirimi gonderildi."
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        return False, f"Pushover hata verdi: {exc.code} {detail[:180]}"
    except Exception as exc:
        return False, f"Pushover test bildirimi gonderilemedi: {exc}"


def _render_page(path: str = "/") -> bytes:
    summary = _collect_summary()
    log_url, app_url = _addon_urls()
    params = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
    test_status = params.get("test", [""])[0]
    test_message = params.get("msg", [""])[0]
    status = "Calisiyor" if summary["configured"] else "Ayar bekliyor"
    status_class = "status-ok" if summary["configured"] else "status-warn"
    error_class = "status-error" if int(summary["errors"]) > 0 else ""

    cards = [
        ("Durum", status, status_class),
        ("Kontrol araligi", f"{summary['interval']} dakika", ""),
        ("Urun linkleri", summary["products"], ""),
        ("Amazon arama sayfalari", summary["amazon_pages"], ""),
        ("Amazon arama hedefleri", summary["amazon_targets"], ""),
        ("Son kontrol", summary["last_check"], ""),
        ("Hata sayisi", summary["errors"], error_class),
    ]
    card_html = "".join(
        f"<section class='card {escape(str(css))}'><span>{escape(str(label))}</span><strong>{escape(str(value))}</strong></section>"
        for label, value, css in cards
    )
    notice_html = ""
    if test_status in {"ok", "fail"}:
        notice_class = "notice-ok" if test_status == "ok" else "notice-fail"
        notice_html = f"<p class='notice {notice_class}'>{escape(test_message)}</p>"

    html = f"""<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta http-equiv="refresh" content="60"><title>Hermes</title>
<style>
:root {{ color-scheme: dark; --bg:#111827; --panel:#172033; --card:#1e293b; --line:#334155; --text:#f8fafc; --muted:#94a3b8; --accent:#ff9900; --accent2:#00a8e1; --ok:#22c55e; --warn:#facc15; --bad:#fb7185; --blue:#38bdf8; --blue2:#2563eb; }}
* {{ box-sizing:border-box; }} body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; background:radial-gradient(circle at top left,#19324d,var(--bg) 48%); color:var(--text); }}
main {{ max-width:1060px; margin:0 auto; padding:28px 18px 44px; }} .hero {{ border:1px solid var(--line); border-radius:22px; padding:24px; background:rgba(23,32,51,.9); box-shadow:0 22px 60px rgba(0,0,0,.28); }}
p {{ margin:0; color:var(--muted); line-height:1.55; }}
.badge {{ display:inline-flex; margin-bottom:14px; color:#101827; background:linear-gradient(135deg,var(--accent),var(--accent2)); border-radius:18px; padding:10px 16px; font-size:clamp(26px,5vw,46px); line-height:1; letter-spacing:-.04em; font-weight:900; }}
.actions {{ display:flex; flex-wrap:wrap; gap:10px; margin-top:20px; align-items:center; }} .inline-form {{ margin:0; }} .button {{ display:inline-flex; align-items:center; justify-content:center; min-height:44px; padding:0 16px; border-radius:14px; border:1px solid transparent; text-decoration:none; font-weight:800; font:inherit; cursor:pointer; }}
.button.primary {{ color:#101827; background:linear-gradient(135deg,var(--accent),var(--accent2)); }} .button.secondary {{ color:var(--text); background:#233047; border-color:var(--line); }} .button.test {{ color:#fff; background:linear-gradient(135deg,var(--blue),var(--blue2)); }}
.notice {{ margin-top:16px; padding:12px 14px; border-radius:12px; font-weight:700; }} .notice-ok {{ color:#dcfce7; background:rgba(34,197,94,.16); border:1px solid rgba(74,222,128,.38); }} .notice-fail {{ color:#ffe4e6; background:rgba(244,63,94,.15); border:1px solid rgba(251,113,133,.42); }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin-top:18px; }} .card {{ border:1px solid var(--line); border-radius:16px; padding:16px; background:var(--card); min-height:92px; }} .card span {{ display:block; color:var(--muted); font-size:13px; margin-bottom:10px; }} .card strong {{ display:block; font-size:22px; line-height:1.2; overflow-wrap:anywhere; }}
.card.status-ok {{ border-color:rgba(34,197,94,.45); background:linear-gradient(135deg,rgba(34,197,94,.13),var(--card) 58%); }} .card.status-ok strong {{ color:var(--ok); }} .card.status-warn strong {{ color:var(--warn); }} .card.status-error strong {{ color:var(--bad); }}
.summary-panel {{ margin-top:18px; border:1px solid var(--line); border-radius:18px; padding:16px; background:var(--card); }} .summary-head {{ display:flex; align-items:flex-end; justify-content:space-between; gap:12px; margin-bottom:12px; }} .summary-head span {{ color:var(--muted); font-size:13px; white-space:nowrap; }}
.table-wrap {{ overflow-x:auto; border:1px solid var(--line); border-radius:14px; }} table {{ width:100%; border-collapse:collapse; min-width:760px; }} th,td {{ padding:10px 9px; border-bottom:1px solid var(--line); text-align:right; white-space:nowrap; }} th {{ color:#e0f2fe; background:#233047; font-size:12px; text-transform:uppercase; letter-spacing:.04em; }} td {{ color:var(--text); font-variant-numeric:tabular-nums; }} tr:last-child td {{ border-bottom:none; }} th:nth-child(1),td:nth-child(1) {{ width:112px; }} th:nth-child(1),td:nth-child(1),th:nth-child(2),td:nth-child(2) {{ text-align:left; }} th:not(:nth-child(2)),td:not(:nth-child(2)) {{ width:108px; }}
.product-cell {{ max-width:430px; white-space:normal; line-height:1.25; }} .product-cell a {{ color:#7dd3fc; text-decoration:none; }} .product-cell a:hover {{ color:#ffb84d; text-decoration:underline; }} .product-cell span {{ display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; text-overflow:ellipsis; }} .deal-row {{ background:rgba(34,197,94,.10); }} .deal-row td {{ color:#86efac; font-weight:800; }} .deal-row .product-cell a {{ color:#86efac; }} .note {{ margin-top:18px; border-left:4px solid var(--accent); padding:12px 14px; background:rgba(255,153,0,.10); border-radius:10px; }} .footer {{ margin-top:18px; font-size:13px; color:var(--muted); }}
</style></head><body><main><div class="hero"><div class="badge">Hermes</div><p>Urun linkleri cok siteli calisir; Amazon arama sayfalari Amazon'a ozel mod olarak korunur.</p><div class="actions"><a class="button primary" href="{log_url}" target="_top">LOG</a><a class="button secondary" href="{app_url}" target="_top">Config</a><form class="inline-form" method="post" action="./test-pushover"><button class="button test" type="submit">Pushover</button></form></div>{notice_html}<div class="grid">{card_html}</div>{_render_table()}<p class="note">LOG butonu log sekmesini, Config butonu yapilandirma sekmesini acar. Pushover butonu test bildirimi gonderir.</p><p class="footer">Sayfa 60 saniyede bir otomatik yenilenir.</p></div></main></body></html>"""
    return html.encode("utf-8")


class _StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        payload = b"ok\n" if path == "/health" else _render_page(self.path)
        content_type = "text/plain; charset=utf-8" if path == "/health" else "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0") or 0)
        if content_length:
            self.rfile.read(content_length)
        if not urllib.parse.urlparse(self.path).path.rstrip("/").endswith("/test-pushover"):
            self.send_error(404)
            return
        ok, message = _send_test_notification()
        status = "ok" if ok else "fail"
        self.send_response(303)
        self.send_header("Location", f"?test={status}&msg={urllib.parse.quote(message)}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def log_message(self, _format, *args) -> None:
        _ = args
        return


def run_dashboard() -> None:
    ThreadingHTTPServer(("0.0.0.0", WEB_PORT), _StatusHandler).serve_forever()


if __name__ == "__main__":
    run_dashboard()
