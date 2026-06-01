from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.parse

from .dashboard import _render_public_page

PUBLIC_WEB_PORT = 8100


class _PublicDashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        if path == "/health":
            status = 200
            payload = b"ok\n"
            content_type = "text/plain; charset=utf-8"
        elif path == "/public" or path.startswith("/public/"):
            status, payload = _render_public_page(self.path)
            content_type = "text/html; charset=utf-8" if status == 200 else "text/plain; charset=utf-8"
        else:
            status = 404
            payload = b"not found\n"
            content_type = "text/plain; charset=utf-8"

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        self.send_error(404)

    def log_message(self, _format, *args) -> None:
        _ = args
        return


def run_public_dashboard() -> None:
    ThreadingHTTPServer(("0.0.0.0", PUBLIC_WEB_PORT), _PublicDashboardHandler).serve_forever()


if __name__ == "__main__":
    run_public_dashboard()
