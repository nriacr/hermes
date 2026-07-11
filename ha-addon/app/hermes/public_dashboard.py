from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.parse

from .dashboard import (
    _public_base_path,
    _public_dashboard_allowed,
    _render_public_page,
    _reset_notifications_async,
    _reset_price_history,
    _send_test_notification,
)
from .settings_ui import (
    handle_settings_save,
    render_settings_page,
    render_settings_restart_page,
    render_settings_restart_script,
    render_settings_script,
)

PUBLIC_WEB_PORT = 8100


def _public_suffix(path: str) -> str:
    parsed = urllib.parse.urlparse(path)
    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) <= 2 or parts[0] != "public":
        return ""
    return "/" + "/".join(parts[2:])


class _PublicDashboardHandler(BaseHTTPRequestHandler):
    def _send_payload(self, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _redirect_with_message(self, target_path: str, flag_name: str, ok: bool, message: str) -> None:
        status = "ok" if ok else "fail"
        separator = "&" if "?" in target_path else "?"
        self.send_response(303)
        self.send_header("Location", f"{target_path}{separator}{flag_name}={status}&msg={urllib.parse.quote(message)}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _redirect(self, target_path: str) -> None:
        self.send_response(303)
        self.send_header("Location", target_path)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        if path == "/health":
            status = 200
            payload = b"ok\n"
            content_type = "text/plain; charset=utf-8"
            self._send_payload(status, payload, content_type)
            return
        if path == "/public" or path.startswith("/public/"):
            if not _public_dashboard_allowed(self.path):
                self._send_payload(404, b"not found\n", "text/plain; charset=utf-8")
                return
            suffix = _public_suffix(self.path)
            base_path = _public_base_path(self.path)
            if suffix == "/settings.js":
                status = 200
                payload = render_settings_script()
                content_type = "application/javascript; charset=utf-8"
            elif suffix == "/settings/restart.js":
                status = 200
                payload = render_settings_restart_script()
                content_type = "application/javascript; charset=utf-8"
            elif suffix == "/settings":
                status = 200
                payload = render_settings_page(self.path)
                content_type = "text/html; charset=utf-8"
            elif suffix == "/settings/restarting":
                params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                message = params.get("msg", ["Ayarlar kaydedildi. Hermes yeniden başlatılıyor."])[0]
                status = 200
                payload = render_settings_restart_page(
                    message,
                    settings_path=f"{base_path}/settings",
                    health_path=f"{base_path}/health",
                )
                content_type = "text/html; charset=utf-8"
            else:
                status, payload = _render_public_page(self.path)
                content_type = "text/html; charset=utf-8" if status == 200 else "text/plain; charset=utf-8"
        else:
            status = 404
            payload = b"not found\n"
            content_type = "text/plain; charset=utf-8"

        self._send_payload(status, payload, content_type)

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(content_length) if content_length else b""
        if not _public_dashboard_allowed(self.path):
            self.send_error(404)
            return

        base_path = _public_base_path(self.path)
        suffix = _public_suffix(self.path)
        if suffix == "/settings/save":
            ok, message = handle_settings_save(body)
            if ok:
                self._redirect(f"{base_path}/settings/restarting?msg={urllib.parse.quote(message)}")
            else:
                self._redirect_with_message(f"{base_path}/settings", "saved", False, message)
            return
        if suffix == "/reset-notifications":
            ok, message = _reset_notifications_async()
            self._redirect_with_message(base_path, "reset", ok, message)
            return
        if suffix == "/reset-price-history":
            ok, message = _reset_price_history()
            self._redirect_with_message(base_path, "history", ok, message)
            return
        if suffix == "/test-pushover":
            ok, message = _send_test_notification()
            self._redirect_with_message(base_path, "test", ok, message)
            return

        self.send_error(404)

    def log_message(self, _format, *args) -> None:
        _ = args
        return


def run_public_dashboard() -> None:
    ThreadingHTTPServer(("0.0.0.0", PUBLIC_WEB_PORT), _PublicDashboardHandler).serve_forever()


if __name__ == "__main__":
    run_public_dashboard()
