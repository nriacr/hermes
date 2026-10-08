"""One router for the ingress panel (8099) and the token-protected public panel (8100).

Both surfaces render the same pages and run the same actions; only the base
address differs: relative ("." / "..") behind ingress, `/public/<token>` publicly.
"""

import gzip
import hashlib
import hmac
import json
import threading
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, List, Optional, Protocol, Tuple

from ..config import read_options
from ..constants import APP_VERSION
from ..logging_utils import log
from ..notifier import Pushover
from ..utils import parse_bool
from . import assets
from .dashboard import dashboard_live_html, render_dashboard_page
from .icons import HERMES_ICON_PNG, HERMES_ICON_SVG, render_web_manifest
from .pages import link
from .statistics import render_statistics_page, statistics_live_html
from .settings import handle_settings_save, render_restart_page, render_settings_page, should_return_to_main

PUBLIC_TOKEN_MIN_LENGTH = 24
# Text responses above this size are gzip-compressed when the browser accepts it.
GZIP_MIN_BYTES = 1024
HTML = "text/html; charset=utf-8"
TEXT = "text/plain; charset=utf-8"


class Runtime(Protocol):
    """What the panel needs from the running Hermes application."""

    config_error: str

    def health(self) -> Tuple[bool, str]: ...

    def reset_notifications(self) -> Tuple[bool, str]: ...

    def reset_price_history(self) -> Tuple[bool, str]: ...

    def reset_error_history(self) -> Tuple[bool, str]: ...


@dataclass
class Request:
    method: str
    path: str  # below the surface base, e.g. "/settings"
    base: str
    params: Dict[str, List[str]]
    body: bytes = b""


@dataclass
class Response:
    status: int
    payload: bytes = b""
    content_type: str = TEXT
    headers: Dict[str, str] = field(default_factory=dict)


NOT_FOUND = Response(404, b"not found\n")


def public_token_allowed(token: str) -> bool:
    options = read_options()
    if not parse_bool(options.get("public_dashboard_enabled"), default=False):
        return False
    expected = str(options.get("public_dashboard_token") or "").strip()
    return len(expected) >= PUBLIC_TOKEN_MIN_LENGTH and hmac.compare_digest(token.encode(), expected.encode())


def split_request(raw_path: str, method: str, body: bytes, public_only: bool) -> Optional[Request]:
    """Find the surface of a request; None when it must be answered with 404."""
    parsed = urllib.parse.urlparse(raw_path)
    params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    if parts and parts[0] == "public":
        if len(parts) < 2 or not public_token_allowed(parts[1]):
            return None
        base = f"/public/{urllib.parse.quote(parts[1], safe='')}"
        return Request(method, "/" + "/".join(parts[2:]), base, params, body)
    if public_only:
        return Request(method, "/health", ".", params, body) if parts == ["health"] else None
    depth = max(len(parts) - 1, 0)
    base = "/".join([".."] * depth) if depth else "."
    return Request(method, "/" + "/".join(parts), base, params, body)


def redirect(request: Request, target: str, **query: str) -> Response:
    location = link(request.base, target)
    if query:
        location += "?" + urllib.parse.urlencode(query)
    return Response(303, headers={"Location": location})


def send_test_notification() -> Tuple[bool, str]:
    options = read_options()
    notifier = Pushover(options.get("pushover_user_key", ""), options.get("pushover_api_token", ""))
    if not notifier.configured:
        return False, "Pushover anahtarları eksik. Home Assistant'ta Hermes yapılandırmasını kontrol et."
    try:
        notifier.send("Hermes test", "Hermes test bildirimi. Ayarlar sağlıklı görünüyor.")
        return True, "Pushover test bildirimi gönderildi."
    except Exception as exc:  # noqa: BLE001
        detail = getattr(getattr(exc, "response", None), "text", "") or str(exc)
        return False, f"Pushover test bildirimi gönderilemedi: {detail[:180]}"


ASSETS: Dict[str, Callable[[Request], Response]] = {
    "/app.css": lambda _r: Response(200, assets.APP_CSS.encode("utf-8"), "text/css; charset=utf-8"),
    "/settings.js": lambda _r: Response(200, assets.SETTINGS_SCRIPT.encode("utf-8"), "application/javascript; charset=utf-8"),
    "/restart.js": lambda _r: Response(200, assets.RESTART_SCRIPT.encode("utf-8"), "application/javascript; charset=utf-8"),
    "/live.js": lambda _r: Response(200, assets.LIVE_SCRIPT.encode("utf-8"), "application/javascript; charset=utf-8"),
    "/settings/restart.js": lambda _r: Response(200, assets.RESTART_SCRIPT.encode("utf-8"), "application/javascript; charset=utf-8"),
    "/icon.png": lambda _r: Response(200, HERMES_ICON_PNG, "image/png"),
    "/icon.svg": lambda _r: Response(200, HERMES_ICON_SVG, "image/svg+xml"),
    "/manifest.webmanifest": lambda r: Response(200, render_web_manifest(r.base), "application/manifest+json; charset=utf-8"),
}


LIVE_PARTS = {
    "/live/dashboard": lambda base, _params: dashboard_live_html(base),
    "/live/statistics": statistics_live_html,
}


class Router:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime

    def handle(self, request: Request) -> Response:
        path = request.path.rstrip("/") or "/"
        if request.method == "GET":
            return self._get(request, path)
        if request.method == "POST":
            return self._post(request, path)
        return Response(405, b"method not allowed\n")

    def _get(self, request: Request, path: str) -> Response:
        if path == "/health":
            ok, detail = self.runtime.health()
            return Response(200 if ok else 503, f"{'ok' if ok else detail}\n".encode("utf-8"))
        if path in LIVE_PARTS:
            # The fragment is shown on a top-level page, so its ingress links are relative to ".".
            page_base = request.base if request.base.startswith("/") else "."
            html = LIVE_PARTS[path](page_base, request.params)
            # The browser sends the version it shows; an unchanged block is not sent again
            # (keeps refreshes small while nothing changed).
            version = hashlib.sha256(html.encode("utf-8")).hexdigest()[:16]
            data = {"v": version, "same": True} if request.params.get("v", [""])[0] == version else {"v": version, "html": html}
            return Response(200, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
        if path in ASSETS:
            response = ASSETS[path](request)
            response.headers["Cache-Control"] = "public, max-age=86400"
            return response
        pages = {
            "/": lambda: render_dashboard_page(request.base, request.params, self.runtime.config_error),
            "/statistics": lambda: render_statistics_page(request.base, request.params),
            "/settings": lambda: render_settings_page(request.base, request.params),
            "/restarting": lambda: render_restart_page(request.base, request.params),
            "/settings/restarting": lambda: render_restart_page(request.base, request.params),
        }
        if path not in pages:
            return NOT_FOUND
        return Response(200, pages[path](), HTML)

    def _post(self, request: Request, path: str) -> Response:
        if path == "/settings/save":
            ok, message = handle_settings_save(request.body)
            if not ok:
                return redirect(request, "settings", saved="fail", msg=message)
            extra = {"return_to_main": "1"} if should_return_to_main(request.body) else {}
            return redirect(request, "restarting", msg=message, **extra)
        if path == "/reset-errors":
            ok, message = self.runtime.reset_error_history()
            return redirect(request, "statistics", saved="ok" if ok else "fail", msg=message)
        actions = {
            "/test-pushover": ("test", send_test_notification),
            "/reset-notifications": ("reset", self.runtime.reset_notifications),
            "/reset-price-history": ("history", self.runtime.reset_price_history),
        }
        if path not in actions:
            return NOT_FOUND
        _, action = actions[path]
        ok, message = action()
        return redirect(request, "settings", saved="ok" if ok else "fail", msg=message)


def make_handler(router: Router, public_only: bool):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"Hermes/{APP_VERSION}"

        def _respond(self, method: str) -> None:
            body = b""
            if method == "POST":
                body = self.rfile.read(int(self.headers.get("Content-Length", "0") or 0))
            request = split_request(self.path, method, body, public_only)
            try:
                response = router.handle(request) if request else NOT_FOUND
            except Exception as exc:  # noqa: BLE001 - a broken page must not end the server
                log(f"Panel isteği işlenemedi: {method} {urllib.parse.urlparse(self.path).path[:40]} | {exc}")
                response = Response(500, "Hermes bu sayfayı şu an hazırlayamadı.\n".encode("utf-8"))
            payload, headers = response.payload, dict(response.headers)
            if (len(payload) >= GZIP_MIN_BYTES and not response.content_type.startswith("image/png")
                    and "gzip" in self.headers.get("Accept-Encoding", "")):
                payload = gzip.compress(payload, compresslevel=6)
                headers.update({"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
            self.send_response(response.status)
            if payload or response.status != 303:
                self.send_header("Content-Type", response.content_type)
                self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", headers.pop("Cache-Control", "no-store"))
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            if payload:
                self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            self._respond("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._respond("POST")

        def log_message(self, _format, *args) -> None:
            return

    return Handler


def start_server(router: Router, port: int, public_only: bool) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(router, public_only))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name=f"web-{port}", daemon=True).start()
    return server
