import urllib.parse
from html import escape
from http.server import ThreadingHTTPServer

from .dashboard import WEB_PORT, _StatusHandler, _render_page, _render_public_page, _send_test_notification
from .settings_ui import SETTINGS_CSS, handle_settings_save, render_settings_page


def _render_restart_page(message: str) -> bytes:
    safe_message = escape(message)
    html = f"""<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Hermes yeniden başlatılıyor</title>
  <style>{SETTINGS_CSS}</style>
</head>
<body>
  <main>
    <div class="hero">
      <h1>Hermes yeniden başlatılıyor</h1>
      <p class="notice notice-ok">{safe_message}</p>
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
            payload = _render_page(self.path).replace(
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
            status = "ok" if ok else "fail"
            self.send_response(303)
            self.send_header("Location", f"?test={status}&msg={urllib.parse.quote(message)}")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        self.send_error(404)


def run_dashboard() -> None:
    ThreadingHTTPServer(("0.0.0.0", WEB_PORT), SettingsDashboardHandler).serve_forever()


if __name__ == "__main__":
    run_dashboard()
