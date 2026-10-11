"""One router for the ingress panel (8099) and the token-protected public panel (8100).

Both surfaces render the same pages and run the same actions; only the base
address differs: relative ("." / "..") behind ingress, `/public/<token>` publicly.
"""

import gzip
import hashlib
import hmac
import json
import math
import re
import threading
import urllib.parse
from dataclasses import dataclass, field
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, List, Optional, Protocol, Tuple

from ..config import read_options
from ..constants import APP_VERSION
from ..logging_utils import log, redact
from ..notifier import Pushover
from ..utils import parse_bool
from . import assets
from .access import Session, WebAccess
from .dashboard import dashboard_live_html, render_dashboard_page
from .icons import HERMES_ICON_PNG, HERMES_ICON_SVG, render_web_manifest
from .pages import link
from .pin import csrf_field, render_configure_page, render_pin_page, render_session_bar
from .statistics import render_statistics_page, statistics_live_html
from .settings import handle_restart, handle_settings_save, render_restart_page, render_settings_page, should_return_to_main

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

    def reset_statistics(self) -> Tuple[bool, str]: ...


@dataclass
class Request:
    method: str
    path: str  # below the surface base, e.g. "/settings"
    base: str
    params: Dict[str, List[str]]
    body: bytes = b""
    cookie: str = ""
    cookie_path: str = "/"
    trusted_ingress: bool = False
    secure: bool = False

    @property
    def scope(self) -> str:
        return self.base if self.base.startswith("/public/") else self.cookie_path


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
        status = getattr(getattr(exc, "response", None), "status_code", None)
        detail = f"HTTP {status}" if status else type(exc).__name__
        return False, f"Pushover test bildirimi gönderilemedi: {redact(detail)}. Anahtarları ve bağlantıyı kontrol et."


ASSETS: Dict[str, Callable[[Request], Response]] = {
    "/app.css": lambda _r: Response(200, assets.APP_CSS.encode("utf-8"), "text/css; charset=utf-8"),
    "/settings.js": lambda _r: Response(200, assets.SETTINGS_SCRIPT.encode("utf-8"), "application/javascript; charset=utf-8"),
    "/pin.js": lambda _r: Response(200, assets.PIN_SCRIPT.encode("utf-8"), "application/javascript; charset=utf-8"),
    "/session.js": lambda _r: Response(200, assets.SESSION_SCRIPT.encode("utf-8"), "application/javascript; charset=utf-8"),
    "/restart.js": lambda _r: Response(200, assets.RESTART_SCRIPT.encode("utf-8"), "application/javascript; charset=utf-8"),
    "/live.js": lambda _r: Response(200, assets.LIVE_SCRIPT.encode("utf-8"), "application/javascript; charset=utf-8"),
    "/overview.js": lambda _r: Response(200, assets.OVERVIEW_SCRIPT.encode("utf-8"), "application/javascript; charset=utf-8"),
    "/settings/restart.js": lambda _r: Response(200, assets.RESTART_SCRIPT.encode("utf-8"), "application/javascript; charset=utf-8"),
    **{f"/fonts/{name}": (lambda _r, name=name: Response(200, assets.read_font(name), "font/woff2")) for name in assets.FONT_FILES},
    "/icon.png": lambda _r: Response(200, HERMES_ICON_PNG, "image/png"),
    "/icon.svg": lambda _r: Response(200, HERMES_ICON_SVG, "image/svg+xml"),
    "/manifest.webmanifest": lambda r: Response(200, render_web_manifest(r.base), "application/manifest+json; charset=utf-8"),
}


LIVE_PARTS = {
    "/live/dashboard": lambda base, _params: dashboard_live_html(base),
    "/live/statistics": statistics_live_html,
}


class Router:
    def __init__(self, runtime: Runtime, access: Optional[WebAccess] = None) -> None:
        self.runtime = runtime
        self.access = access or WebAccess()

    def _cookie(self, request: Request, token: str) -> str:
        return self.access.cookie(token, request.scope, request.secure)

    def _pin_reply(self, request: Request, *, status: int = 200, error: str = "", configure: bool = False) -> Response:
        session = self.access.session(request.cookie, request.scope)
        headers = {}
        if not session:
            token, session = self.access.create_session(request.scope)
            headers["Set-Cookie"] = self._cookie(request, token)
        if configure:
            payload = render_configure_page(request.base, session.csrf, configured=self.access.configured, error=error)
        else:
            payload = render_pin_page(request.base, session.csrf, configured=self.access.configured,
                                      trusted=request.trusted_ingress, error=error,
                                      expired=request.params.get("expired") == ["1"], retry_after=self.access.retry_after())
        return Response(status, payload, HTML, headers)

    def _decorate(self, request: Request, response: Response, session: Session) -> Response:
        if response.content_type != HTML or not response.payload:
            return response
        html = response.payload.decode("utf-8")
        # All POST forms share one authorization/CSRF boundary, including legacy actions.
        html = re.sub(r"(<form\b[^>]*\bmethod=['\"]post['\"][^>]*>)",
                      lambda match: match[0] + csrf_field(session.csrf), html, flags=re.IGNORECASE)
        remaining = max(0, math.floor(session.deadline - self.access.clock()))
        script = (f"<script id='hermes-session-script' src='{escape(link(request.base, 'session.js'), quote=True)}?v={APP_VERSION}' "
                  f"data-csrf='{escape(session.csrf, quote=True)}' data-remaining='{remaining}' "
                  f"data-touch-path='{escape(link(request.base, 'auth/touch'), quote=True)}' "
                  f"data-status-path='{escape(link(request.base, 'auth/status'), quote=True)}' "
                  f"data-pin-path='{escape(link(request.base, 'auth/pin'), quote=True)}?expired=1' defer></script>")
        response.payload = html.replace("</body>", script + "</body>").encode("utf-8")
        return response

    @staticmethod
    def _form(request: Request) -> Dict[str, List[str]]:
        return urllib.parse.parse_qs(request.body.decode("utf-8", errors="replace"), keep_blank_values=True, max_num_fields=5000)

    def _auth_action(self, request: Request, path: str, session: Optional[Session]) -> Response:
        if path == "/auth/configure" and not request.trusted_ingress:
            return NOT_FOUND
        form = self._form(request)
        if not self.access.valid_csrf(session, form.get("csrf_token", [""])[0]):
            return Response(403, "Oturum geçersiz. Sayfayı yenile ve tekrar dene.\n".encode("utf-8"))
        if path == "/auth/configure":
            pin = form.get("pin", [""])[0]
            if pin != form.get("pin_confirm", [""])[0] or not self.access.configure(pin):
                return self._pin_reply(request, status=400, configure=True, error="Aynı 4 haneli PIN’i iki alana da gir.")
            # Saving a PIN does not bypass PIN entry, and invalidates other browsers.
            response = redirect(request, "auth/pin")
            response.headers["Set-Cookie"] = self._cookie(request, "")
            return response
        if path == "/auth/unlock":
            if not self.access.verify(form.get("pin", [""])[0]):
                return self._pin_reply(request, status=429 if self.access.retry_after() else 401,
                                       error="PIN yanlış. Tekrar dene." if self.access.configured else "PIN kullanıma hazır değil.")
            self.access.discard(request.cookie)
            token, _ = self.access.create_session(request.scope, authenticated=True)
            response = redirect(request, "settings")
            response.headers["Set-Cookie"] = self._cookie(request, token)
            return response
        if not session or not session.authenticated:
            return Response(401, b"PIN required\n")
        if path == "/auth/lock":
            self.access.discard(request.cookie)
            response = redirect(request, "auth/pin")
            response.headers["Set-Cookie"] = self._cookie(request, "")
            return response
        if path == "/auth/touch":
            self.access.session(request.cookie, request.scope, touch=True)
            return self._session_status(session)
        return NOT_FOUND

    def _session_status(self, session: Session) -> Response:
        return Response(200, json.dumps({"remaining": max(0, math.floor(session.deadline - self.access.clock()))}).encode(),
                        "application/json; charset=utf-8")

    def handle(self, request: Request) -> Response:
        path = request.path.rstrip("/") or "/"
        # An explicit guest allowlist keeps future management routes protected by default.
        if request.method == "GET" and path in {"/", "/live/dashboard", "/health", *ASSETS}:
            return self._get(request, path)
        if path == "/internal/runtime":
            # HA-authenticated maintenance only; never accessible on the public surface.
            if request.method == "GET" and request.trusted_ingress:
                return self._get(request, "/runtime")
            return NOT_FOUND
        session = self.access.session(request.cookie, request.scope)
        if request.method == "GET" and path in {"/auth/pin", "/auth/configure"}:
            if path == "/auth/configure" and not request.trusted_ingress:
                return NOT_FOUND
            return self._pin_reply(request, configure=path == "/auth/configure")
        if request.method == "POST" and path in {"/auth/unlock", "/auth/configure", "/auth/lock", "/auth/touch"}:
            return self._auth_action(request, path, session)
        if not session or not session.authenticated:
            if request.method == "GET" and path in {"/settings", "/statistics", "/restarting", "/settings/restarting"}:
                return self._pin_reply(request)
            return Response(401, b"PIN required\n")
        if request.method == "GET" and path == "/auth/status":
            return self._session_status(session)  # background polling never extends the session
        if request.method == "POST":
            if not self.access.valid_csrf(session, self._form(request).get("csrf_token", [""])[0]):
                return Response(403, b"invalid form token\n")
            self.access.session(request.cookie, request.scope, touch=True)
            return self._post(request, path)
        if request.method == "GET":
            if path not in LIVE_PARTS and path != "/runtime":
                self.access.session(request.cookie, request.scope, touch=True)
            return self._decorate(request, self._get(request, path), session)
        return Response(405, b"method not allowed\n")

    def _get(self, request: Request, path: str) -> Response:
        if path == "/health":
            ok, detail = self.runtime.health()
            return Response(200 if ok else 503, f"{'ok' if ok else detail}\n".encode("utf-8"))
        if path == "/runtime":
            return Response(200, json.dumps(self.runtime.metrics(), ensure_ascii=False).encode("utf-8"),
                            "application/json; charset=utf-8")
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
            "/settings": lambda: render_settings_page(request.base, request.params,
                                                       access_bar=render_session_bar(request.base, request.trusted_ingress)),
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
        if path == "/restart":
            ok, message = handle_restart()
            return redirect(request, "restarting", msg=message) if ok else redirect(request, "settings", saved="fail", msg=message)
        if path in {"/reset-statistics", "/reset-errors"}:
            ok, message = self.runtime.reset_statistics()
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
        if ok and path == "/test-pushover" and getattr(self.runtime, "service", None):
            self.runtime.service.monitor.delivery.retry_failed()
        return redirect(request, "settings", saved="ok" if ok else "fail", msg=message)


def make_handler(router: Router, public_only: bool):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"Hermes/{APP_VERSION}"

        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def _respond(self, method: str) -> None:
            body = b""
            if method == "POST":
                try:
                    length = int(self.headers.get("Content-Length", "0") or 0)
                    if not 0 <= length <= 1024 * 1024:
                        self.send_error(413)
                        return
                except ValueError:
                    self.send_error(400)
                    return
                body = self.rfile.read(length)
            request = split_request(self.path, method, body, public_only)
            if request:
                request.cookie = self.headers.get("Cookie", "")
                ingress_path = self.headers.get("X-Ingress-Path", "")
                request.trusted_ingress = (not public_only and not request.base.startswith("/public/")
                                           and self.client_address[0] == "172.30.32.2"
                                           and bool(re.fullmatch(r"/api/hassio_ingress/[A-Za-z0-9_-]+", ingress_path)))
                request.cookie_path = ingress_path if request.trusted_ingress else "/"
                request.secure = self.headers.get("X-Forwarded-Proto", "").lower() == "https"
            try:
                response = router.handle(request) if request else NOT_FOUND
            except Exception as exc:  # noqa: BLE001 - a broken page must not end the server
                # Never log a token path, cookie, submitted PIN or exception value.
                log(f"Panel isteği işlenemedi: {method} | {type(exc).__name__}")
                response = Response(500, "Hermes bu sayfayı şu an hazırlayamadı.\n".encode("utf-8"))
            payload, headers = response.payload, dict(response.headers)
            if (len(payload) >= GZIP_MIN_BYTES and not response.content_type.startswith(("image/png", "font/"))
                    and "gzip" in self.headers.get("Accept-Encoding", "")):
                payload = gzip.compress(payload, compresslevel=6)
                headers.update({"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
            self.send_response(response.status)
            if payload or response.status != 303:
                self.send_header("Content-Type", response.content_type)
                self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", headers.pop("Cache-Control", "no-store"))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
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
