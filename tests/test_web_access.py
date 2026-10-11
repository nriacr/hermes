"""Management access must fail closed independently of visible links or JavaScript."""

import json
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

from hermes.web import server
from hermes.web.access import CHALLENGE_SECONDS, HASH_ITERATIONS, IDLE_SECONDS, MAX_SESSIONS, WebAccess


class AccessCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "web_access.json"
        self.now = 1_800_000_000.0
        self.access = WebAccess(self.path, lambda: self.now)
        self.runtime = Mock(config_error="")
        self.runtime.health.return_value = (True, "ok")
        self.runtime.metrics.return_value = {"healthy": True, "active_cards": 12}
        self.router = server.Router(self.runtime, self.access)

    def request(self, path, *, method="GET", fields=None, cookie="", scope="/public/test-link", trusted=False):
        body = urllib.parse.urlencode(fields or {}).encode()
        request = server.Request(method, path, scope, {}, body, cookie=cookie, cookie_path=scope, trusted_ingress=trusted)
        return self.router.handle(request)

    def challenge(self, scope="/public/test-link", trusted=False, configure=False):
        response = self.request("/auth/configure" if configure else "/auth/pin", scope=scope, trusted=trusted)
        cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        return cookie, self.access.session(cookie, scope).csrf

    def unlock(self, scope="/public/test-link"):
        self.access.configure("0123")  # Disposable test value; never an owner's PIN.
        cookie, csrf = self.challenge(scope)
        response = self.request("/auth/unlock", method="POST", fields={"pin": "0123", "csrf_token": csrf},
                                cookie=cookie, scope=scope)
        self.assertEqual(response.status, 303)
        return response.headers["Set-Cookie"].split(";", 1)[0], cookie


class VerifierTests(AccessCase):
    def test_only_four_ascii_digits_are_accepted_and_the_leading_zero_is_kept(self):
        for pin in ("", "123", "12345", "１２３４", "١٢٣٤", "abcd", "1234\n"):
            with self.subTest(pin=pin):
                self.assertFalse(self.access.configure(pin))
        self.assertTrue(self.access.configure("0123"))
        self.assertTrue(self.access.verify("0123"))
        self.assertFalse(self.access.verify("123"))

    def test_only_salted_verifier_is_persisted_and_survives_restart(self):
        self.access.configure("0123")
        data = json.loads(self.path.read_text())
        self.assertNotIn("pin", data)
        self.assertEqual(data["iterations"], HASH_ITERATIONS)
        self.assertNotIn('"0123"', self.path.read_text())
        other = WebAccess(self.path, lambda: self.now)
        self.assertTrue(other.verify("0123"))
        previous = data["salt"]
        self.access.configure("0123")
        self.assertNotEqual(previous, self.access.state["salt"])

    def test_lockout_is_global_restart_safe_and_expires(self):
        self.access.configure("0123")
        for _ in range(5):
            self.assertFalse(self.access.verify("9999"))
        self.assertEqual(self.access.retry_after(), 60)
        restarted = WebAccess(self.path, lambda: self.now)
        self.assertFalse(restarted.verify("0123"))
        self.now += 61
        self.assertTrue(restarted.verify("0123"))

    def test_spread_out_failures_do_not_accumulate_forever(self):
        self.access.configure("0123")
        for _ in range(8):
            self.assertFalse(self.access.verify("9999"))
            self.now += 61
        self.assertEqual(self.access.retry_after(), 0)

    def test_missing_corrupt_or_unsupported_verifier_never_opens_settings(self):
        for value in (None, "{broken", "[]", "{}", "null", '{"version":99}', "x" * 8193):
            with self.subTest(value=value and value[:20]):
                if value is not None:
                    self.path.write_text(value)
                access = WebAccess(self.path)
                self.assertFalse(access.configured)
                self.assertFalse(access.verify("0123"))
                response = server.Router(self.runtime, access).handle(server.Request("GET", "/settings", ".", {}))
                self.assertIn("PIN henüz", response.payload.decode())
                self.assertNotIn("settings-form", response.payload.decode())

    def test_failed_persistence_never_grants_a_session(self):
        self.access.configure("0123")
        cookie, csrf = self.challenge()
        with patch("hermes.web.access.save_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.request("/auth/unlock", method="POST", cookie=cookie, fields={"pin": "0123", "csrf_token": csrf})
        self.assertFalse(self.access.session(cookie, "/public/test-link").authenticated)


class AuthorizationTests(AccessCase):
    def test_guest_can_only_read_home_fragments_assets_and_health(self):
        with patch.object(server, "render_dashboard_page", return_value=b"existing home"), \
                patch.dict(server.LIVE_PARTS, {"/live/dashboard": lambda *_: "existing home fragment"}):
            self.assertEqual(self.request("/").payload, b"existing home")
            self.assertEqual(self.request("/live/dashboard").status, 200)
        for path in ("/health", "/app.css", "/overview.js", "/icon.png"):
            self.assertEqual(self.request(path).status, 200)
        for path in ("/settings", "/statistics", "/restarting", "/settings/restarting"):
            with self.subTest(path=path), patch.object(server, "render_settings_page") as render:
                response = self.request(path)
                self.assertIn("Ayarlar kilitli", response.payload.decode())
                render.assert_not_called()
        for path in ("/runtime", "/live/statistics", "/future-private-route", "/internal/runtime"):
            self.assertIn(self.request(path).status, (401, 404))

    def test_every_mutation_is_denied_without_a_pin_session_including_old_alias(self):
        paths = ("/settings/save", "/restart", "/reset-statistics", "/reset-errors", "/test-pushover",
                 "/reset-notifications", "/reset-price-history", "/future-action")
        with patch.object(server, "handle_settings_save") as save, patch.object(server, "handle_restart") as restart:
            for path in paths:
                with self.subTest(path=path):
                    self.assertEqual(self.request(path, method="POST").status, 401)
            save.assert_not_called()
            restart.assert_not_called()
        self.runtime.reset_statistics.assert_not_called()
        self.runtime.reset_price_history.assert_not_called()
        self.runtime.reset_notifications.assert_not_called()

    def test_valid_login_rotates_cookie_and_old_cookie_cannot_be_reused(self):
        cookie, old_cookie = self.unlock()
        self.assertIsNone(self.access.session(old_cookie, "/public/test-link"))
        self.assertTrue(self.access.session(cookie, "/public/test-link").authenticated)
        self.assertNotEqual(cookie, old_cookie)
        self.assertEqual(self.request("/runtime", cookie=cookie).status, 200)
        self.assertEqual(self.request("/runtime", cookie=old_cookie).status, 401)

    def test_valid_pin_without_challenge_or_with_wrong_csrf_does_not_unlock(self):
        self.access.configure("0123")
        for cookie in ("", self.challenge()[0]):
            response = self.request("/auth/unlock", method="POST", cookie=cookie, fields={"pin": "0123", "csrf_token": "wrong"})
            self.assertEqual(response.status, 403)

    def test_authorized_cookie_alone_cannot_change_data(self):
        cookie, _ = self.unlock()
        self.assertEqual(self.request("/reset-price-history", method="POST", cookie=cookie).status, 403)
        self.runtime.reset_price_history.assert_not_called()
        self.runtime.reset_price_history.return_value = (True, "ok")
        csrf = self.access.session(cookie, "/public/test-link").csrf
        response = self.request("/reset-price-history", method="POST", cookie=cookie, fields={"csrf_token": csrf})
        self.assertEqual(response.status, 303)
        self.runtime.reset_price_history.assert_called_once()

    def test_cookie_is_bound_to_surface_and_has_safe_attributes(self):
        cookie, _ = self.unlock()
        self.assertEqual(self.request("/runtime", cookie=cookie, scope="/public/other-link").status, 401)
        self.assertEqual(self.request("/runtime", cookie=cookie, scope="/api/hassio_ingress/example").status, 401)
        header = self.access.cookie("test", "/public/test-link", secure=True)
        for value in ("HttpOnly", "SameSite=Strict", "Secure", "Path=/public/test-link/"):
            self.assertIn(value, header)

    def test_lock_revokes_the_server_session(self):
        cookie, _ = self.unlock()
        csrf = self.access.session(cookie, "/public/test-link").csrf
        response = self.request("/auth/lock", method="POST", cookie=cookie, fields={"csrf_token": csrf})
        self.assertIn("Max-Age=0", response.headers["Set-Cookie"])
        self.assertEqual(self.request("/runtime", cookie=cookie).status, 401)

    def test_server_idle_expiry_cannot_be_bypassed_by_polling_or_javascript_disabled(self):
        cookie, _ = self.unlock()
        for _ in range(15):
            self.now += 59
            self.assertEqual(self.request("/auth/status", cookie=cookie).status, 200)
            self.assertEqual(self.request("/runtime", cookie=cookie).status, 200)
        self.now += 16
        self.assertEqual(self.request("/runtime", cookie=cookie).status, 401)
        self.assertEqual(self.request("/reset-notifications", method="POST", cookie=cookie).status, 401)

    def test_explicit_activity_extends_session_but_never_resurrects_expired_one(self):
        cookie, _ = self.unlock()
        csrf = self.access.session(cookie, "/public/test-link").csrf
        self.now += IDLE_SECONDS - 1
        response = self.request("/auth/touch", method="POST", cookie=cookie, fields={"csrf_token": csrf})
        self.assertEqual(json.loads(response.payload)["remaining"], IDLE_SECONDS)
        self.now += IDLE_SECONDS
        self.assertEqual(self.request("/auth/touch", method="POST", cookie=cookie, fields={"csrf_token": csrf}).status, 403)

    def test_pin_change_is_ha_only_and_invalidates_all_sessions(self):
        cookie, _ = self.unlock()
        self.assertEqual(self.request("/auth/configure").status, 404)
        self.assertEqual(self.request("/auth/configure", method="POST", cookie=cookie).status, 404)
        scope = "/api/hassio_ingress/test"
        setup_cookie, csrf = self.challenge(scope, trusted=True, configure=True)
        response = self.request("/auth/configure", method="POST", scope=scope, trusted=True, cookie=setup_cookie,
                                fields={"pin": "4567", "pin_confirm": "4567", "csrf_token": csrf})
        self.assertEqual(response.status, 303)
        self.assertEqual(self.request("/runtime", cookie=cookie).status, 401)
        self.assertTrue(self.access.verify("4567"))

    def test_first_setup_needs_authenticated_ha_and_matching_pin(self):
        scope = "/api/hassio_ingress/test"
        cookie, csrf = self.challenge(scope, trusted=True, configure=True)
        response = self.request("/auth/configure", method="POST", scope=scope, trusted=True, cookie=cookie,
                                fields={"pin": "0123", "pin_confirm": "9999", "csrf_token": csrf})
        self.assertEqual(response.status, 400)
        self.assertFalse(self.path.exists())

    def test_challenges_expire_and_unauthenticated_flood_does_not_evict_owner(self):
        token, owner = self.access.create_session("owner", authenticated=True)
        challenge, _ = self.access.create_session("guest")
        self.now += CHALLENGE_SECONDS
        self.assertIsNone(self.access.session(f"hermes_session={challenge}", "guest"))
        for _ in range(MAX_SESSIONS * 2):
            self.access.create_session("guest")
        self.assertEqual(len(self.access.sessions), MAX_SESSIONS)
        self.assertIs(self.access.session(f"hermes_session={token}", "owner"), owner)

    def test_restart_does_not_restore_any_browser_session(self):
        cookie, _ = self.unlock()
        restarted = server.Router(self.runtime, WebAccess(self.path))
        response = restarted.handle(server.Request("GET", "/runtime", "/public/test-link", {}, cookie=cookie))
        self.assertEqual(response.status, 401)

    def test_all_rendered_post_forms_receive_csrf_in_both_surfaces(self):
        for scope in ("/api/hassio_ingress/test", "/public/test-link"):
            cookie, _ = self.unlock(scope)
            with patch.object(server, "render_settings_page", return_value=b"<body><form method='post' action='save'></form></body>"):
                response = self.request("/settings", scope=scope, cookie=cookie)
            self.assertIn(b"name='csrf_token'", response.payload)
            self.assertIn(b"hermes-session-script", response.payload)

    def test_internal_diagnostics_are_not_open_to_public_guests(self):
        self.assertEqual(self.request("/internal/runtime").status, 404)
        self.assertEqual(self.request("/internal/runtime", trusted=True, scope="/api/hassio_ingress/test").status, 200)


class HttpBoundaryTests(AccessCase):
    def test_forged_ingress_headers_on_public_port_cannot_configure_or_read_diagnostics(self):
        httpd = server.start_server(self.router, 0, public_only=True)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        options = {"public_dashboard_enabled": True, "public_dashboard_token": "t" * 32}
        base = f"http://127.0.0.1:{httpd.server_address[1]}/public/" + "t" * 32
        with patch.object(server, "read_options", return_value=options):
            for path in ("/auth/configure", "/internal/runtime", "/runtime"):
                request = urllib.request.Request(base + path, headers={"X-Ingress-Path": "/api/hassio_ingress/forged",
                                                                      "X-Forwarded-For": "172.30.32.2"})
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(request, timeout=5)
                self.assertIn(caught.exception.code, (401, 404))
                caught.exception.close()

    def test_non_supervisor_ingress_peer_cannot_reset_pin(self):
        httpd = server.start_server(self.router, 0, public_only=False)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        request = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}/auth/configure",
                                         headers={"X-Ingress-Path": "/api/hassio_ingress/forged"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(caught.exception.code, 404)
        caught.exception.close()
