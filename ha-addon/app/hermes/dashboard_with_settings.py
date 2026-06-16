import urllib.parse
from http.server import ThreadingHTTPServer

from .dashboard import WEB_PORT, _render_page, _render_public_page, _send_test_notification
from .settings_ui import handle_settings_save, render_settings_page


class _SettingsDashboardHandler:
    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        status = 200
        if path == "/health":
            payload = b"ok\n"
            content_type = "text/plain; charset=utf-8"
        elif path == "/settings":
            payload = render_settings_page(self.path)
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
            status = "ok" if ok else "fail"
            self.send_response(303)
            self.send_header("Location", f"./settings?saved={status}&msg={urllib.parse.quote(message)}")
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

    def log_message(self, _format, *args) -> None:
        _ = args
        return


class SettingsDashboardHandler(_SettingsDashboardHandler):
    pass


def run_dashboard() -> None:
    ThreadingHTTPServer(("0.0.0.0", WEB_PORT), SettingsDashboardHandler).serve_forever()


if __name__ == "__main__":
    run_dashboard()
