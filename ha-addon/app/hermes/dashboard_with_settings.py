import urllib.parse
from datetime import datetime, timedelta
from html import escape
from http.server import ThreadingHTTPServer

from . import dashboard as dashboard_module
from .constants import OPTIONS_PATH, STATE_PATH, SUMMARY_PATH
from .dashboard import (
    WEB_PORT,
    _StatusHandler,
    _render_public_page,
    _reset_notifications_async,
    _reset_price_history,
    _send_test_notification,
)
from .settings_ui import SETTINGS_CSS, handle_settings_save, render_settings_page
from .storage import load_json
from .utils import parse_iso_datetime


def _collect_summary_all_errors():
    options = load_json(OPTIONS_PATH, {})
    state = load_json(STATE_PATH, {})
    latest_summary = load_json(SUMMARY_PATH, {})
    if not isinstance(latest_summary, dict):
        latest_summary = {}
    watches = options.get("takip_edilenler") if isinstance(options.get("takip_edilenler"), list) else []
    contexts = dashboard_module._error_contexts(options if isinstance(options, dict) else {})

    error_cutoff = timedelta(hours=24)
    now = datetime.now().astimezone()
    last_checks = []
    error_count = 0
    error_details = []
    seen_details = set()

    if isinstance(state, dict):
        for key, value in state.items():
            if key == "_meta" or not isinstance(value, dict):
                continue
            checked_at = parse_iso_datetime(value.get("last_checked_at"))
            if checked_at:
                checked_local = checked_at.astimezone()
                last_checks.append(checked_local)
                if value.get("last_error") and now - checked_local <= error_cutoff:
                    error_count += 1
                    detail = dashboard_module._error_detail(key, value, contexts)
                    detail_key = dashboard_module._error_detail_key(detail)
                    if detail_key not in seen_details:
                        seen_details.add(detail_key)
                        error_details.append(detail)
    interval_seconds = int(options.get("interval_seconds") or 60)
    last_check = max(last_checks) if last_checks else None
    return {
        "interval": interval_seconds,
        "watches": len(watches),
        "last_check": last_check.strftime("%Y-%m-%d %H:%M:%S") if last_check else "-",
        "next_check": (last_check + timedelta(seconds=interval_seconds)).strftime("%Y-%m-%d %H:%M:%S") if last_check else "-",
        "cycle_duration": dashboard_module._duration_text(
            latest_summary.get("cycle_duration_seconds"),
            latest_summary.get("cycle_duration_minutes") or "-",
        ),
        "last_update": dashboard_module._relative_time_text(latest_summary.get("checked_at")),
        "errors": error_count,
        "error_details": error_details,
        "configured": bool(options),
        "telegram": dashboard_module._collect_telegram_summary(options if isinstance(options, dict) else {}),
    }


def _render_page_with_all_errors(path: str) -> bytes:
    dashboard_module._collect_summary = _collect_summary_all_errors
    return dashboard_module._render_page(path)


def _render_restart_page(message: str) -> bytes:
    html = """<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Hermes yeniden başlatılıyor</title>
  <style>__SETTINGS_CSS__</style>
</head>
<body>
  <main>
    <div class="hero">
      <h1>Hermes yeniden başlatılıyor</h1>
      <p class="notice notice-ok">__MESSAGE__</p>
      <p>Değişiklikler Home Assistant config kaydına yazıldı. Hermes yeniden başlarken bu sayfa kısa süre bekleyecek; hazır olduğunda ayarlar ekranı otomatik yenilenecek.</p>
      <p class="footer-note" id="restart-status">Hazırlanıyor... Birkaç saniye içinde bağlantı kontrolü başlayacak.</p>
      <div class="actions"><a class="button secondary" href="../settings">Ayarlar ekranına dön</a></div>
    </div>
  </main>
  <script>
    const statusBox = document.getElementById('restart-status');
    let attempts = 0;
    async function waitForHermes() {
      attempts += 1;
      statusBox.textContent = 'Hermes kontrol ediliyor... Deneme ' + attempts;
      try {
        const response = await fetch('../health?ts=' + Date.now(), { cache: 'no-store' });
        if (response.ok) {
          statusBox.textContent = 'Hermes hazır. Ayarlar ekranı yenileniyor...';
          window.location.href = '../settings?saved=ok&msg=' + encodeURIComponent('Hermes hazır. Ayarlar güncellendi.');
          return;
        }
      } catch (error) {
        statusBox.textContent = 'Hermes yeniden başlıyor, bağlantı bekleniyor...';
      }
      setTimeout(waitForHermes, 2000);
    }
    setTimeout(waitForHermes, 6000);
  </script>
</body>
</html>"""
    html = html.replace("__SETTINGS_CSS__", SETTINGS_CSS).replace("__MESSAGE__", escape(message))
    return html.encode("utf-8")


class SettingsDashboardHandler(_StatusHandler):
    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        status = 200
        if path == "/health":
            payload = b"ok\n"
            content_type = "text/plain; charset=utf-8"
        elif path == "/settings":
            payload = render_settings_page(self.path)
            content_type = "text/html; charset=utf-8"
        elif path == "/settings/restarting":
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            message = params.get("msg", ["Ayarlar kaydedildi. Hermes yeniden başlatılıyor."])[0]
            payload = _render_restart_page(message)
            content_type = "text/html; charset=utf-8"
        elif path == "/public" or path.startswith("/public/"):
            status, payload = _render_public_page(self.path)
            content_type = "text/html; charset=utf-8" if status == 200 else "text/plain; charset=utf-8"
        else:
            payload = _render_page_with_all_errors(self.path).replace(
                b'<form class="inline-form" method="post" action="./test-pushover">',
                b'<a class="button secondary" href="./settings">Ayarlar</a><form class="inline-form" method="post" action="./test-pushover">',
                1,
            )
            content_type = "text/html; charset=utf-8"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        content_length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(content_length) if content_length else b""
        if path == "/settings/save":
            ok, message = handle_settings_save(body)
            if ok:
                location = f"../settings/restarting?msg={urllib.parse.quote(message)}"
            else:
                location = f"../settings?saved=fail&msg={urllib.parse.quote(message)}"
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        if path.endswith("/test-pushover"):
            ok, message = _send_test_notification()
            self._redirect_with_message("test", ok, message)
            return
        if path.endswith("/reset-notifications"):
            ok, message = _reset_notifications_async()
            self._redirect_with_message("reset", ok, message)
            return
        if path.endswith("/reset-price-history"):
            ok, message = _reset_price_history()
            self._redirect_with_message("history", ok, message)
            return
        self.send_error(404)


def run_dashboard() -> None:
    ThreadingHTTPServer(("0.0.0.0", WEB_PORT), SettingsDashboardHandler).serve_forever()


if __name__ == "__main__":
    run_dashboard()
