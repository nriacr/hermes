import urllib.parse
from http.server import ThreadingHTTPServer

from .dashboard import (
    WEB_PORT,
    _StatusHandler,
    _public_dashboard_allowed,
    _render_page,
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


def _public_settings_context(path: str):
    parsed = urllib.parse.urlparse(path)
    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) < 3 or parts[0] != "public" or parts[2] != "settings":
        return None
    token = urllib.parse.quote(parts[1], safe="")
    base_path = f"/public/{token}"
    return {
        "settings_path": f"{base_path}/settings",
        "restart_path": f"{base_path}/settings/restarting",
        "health_path": f"{base_path}/health",
    }


class SettingsDashboardHandler(_StatusHandler):
    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        status = 200
        if path == "/health":
            payload = b"ok\n"
            content_type = "text/plain; charset=utf-8"
        elif path.startswith("/public/") and path.endswith("/health"):
            if _public_dashboard_allowed(self.path):
                payload = b"ok\n"
                content_type = "text/plain; charset=utf-8"
            else:
                status = 404
                payload = b"not found\n"
                content_type = "text/plain; charset=utf-8"
        elif path == "/settings.js":
            payload = render_settings_script()
            content_type = "application/javascript; charset=utf-8"
        elif path == "/settings/restart.js":
            payload = render_settings_restart_script()
            content_type = "application/javascript; charset=utf-8"
        elif path == "/settings":
            payload = render_settings_page(self.path)
            content_type = "text/html; charset=utf-8"
        elif path.startswith("/public/") and path.endswith("/settings.js"):
            if _public_dashboard_allowed(self.path):
                payload = render_settings_script()
                content_type = "application/javascript; charset=utf-8"
            else:
                status = 404
                payload = b"not found\n"
                content_type = "text/plain; charset=utf-8"
        elif path.startswith("/public/") and path.endswith("/settings/restart.js"):
            if _public_dashboard_allowed(self.path):
                payload = render_settings_restart_script()
                content_type = "application/javascript; charset=utf-8"
            else:
                status = 404
                payload = b"not found\n"
                content_type = "text/plain; charset=utf-8"
        elif path.startswith("/public/") and path.endswith("/settings"):
            if _public_dashboard_allowed(self.path):
                payload = render_settings_page(self.path)
                content_type = "text/html; charset=utf-8"
            else:
                status = 404
                payload = b"not found\n"
                content_type = "text/plain; charset=utf-8"
        elif path == "/settings/restarting":
            params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            message = params.get("msg", ["Ayarlar kaydedildi. Hermes yeniden başlatılıyor."])[0]
            payload = render_settings_restart_page(message)
            content_type = "text/html; charset=utf-8"
        elif path.startswith("/public/") and path.endswith("/settings/restarting"):
            if _public_dashboard_allowed(self.path):
                params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                message = params.get("msg", ["Ayarlar kaydedildi. Hermes yeniden başlatılıyor."])[0]
                context = _public_settings_context(self.path)
                payload = render_settings_restart_page(
                    message,
                    settings_path=context["settings_path"],
                    health_path=context["health_path"],
                )
                content_type = "text/html; charset=utf-8"
            else:
                status = 404
                payload = b"not found\n"
                content_type = "text/plain; charset=utf-8"
        elif path == "/public" or path.startswith("/public/"):
            status, payload = _render_public_page(self.path)
            content_type = "text/html; charset=utf-8" if status == 200 else "text/plain; charset=utf-8"
        else:
            payload = _render_page(self.path, error_detail_limit=None).replace(
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
        public_context = _public_settings_context(self.path)
        is_public_settings_save = bool(public_context and path.endswith("/settings/save"))
        if path == "/settings/save" or is_public_settings_save:
            if is_public_settings_save and not _public_dashboard_allowed(self.path):
                self.send_error(404)
                return
            ok, message = handle_settings_save(body)
            restart_path = public_context["restart_path"] if public_context else "../settings/restarting"
            settings_path = public_context["settings_path"] if public_context else "../settings"
            if ok:
                location = f"{restart_path}?msg={urllib.parse.quote(message)}"
            else:
                location = f"{settings_path}?saved=fail&msg={urllib.parse.quote(message)}"
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
