"""Release counts use the HA ingress session without exposing credentials."""

import importlib.util
import io
import json
from pathlib import Path
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools/read_runtime.py"
SPEC = importlib.util.spec_from_file_location("pin_runtime_reader", MODULE_PATH)
reader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reader)


class RuntimeAccessTests(unittest.TestCase):
    def response(self):
        return io.BytesIO(json.dumps({"version": "4.1.3", "healthy": True}).encode())

    def test_cookie_only_stays_in_request_headers_not_in_url_or_command_arguments(self):
        with patch.object(reader, "rpc", side_effect=[{"ingress_entry": "app-entry"}, {"session": "test-session"}]), \
                patch.object(reader, "open_ingress", return_value=self.response()) as get:
            result = reader.read_runtime(Path("/fixture"))
        self.assertTrue(result["healthy"])
        request = get.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/api/hassio_ingress/app-entry/internal/runtime"))
        self.assertNotIn("test-session", request.full_url)
        self.assertEqual(request.get_header("Cookie"), "ingress_session=test-session")

    def test_old_version_falls_back_only_within_authenticated_ha_ingress(self):
        error = urllib.error.HTTPError("fixture", 404, "not found", {}, None)
        with patch.object(reader, "rpc", side_effect=[{"ingress_entry": "app-entry"}, {"session": "test-session"}]), \
                patch.object(reader, "open_ingress", side_effect=[error, self.response()]) as get:
            self.assertTrue(reader.read_runtime(Path("/fixture"))["healthy"])
        self.assertEqual(get.call_count, 2)
        self.assertTrue(get.call_args_list[1].args[0].full_url.endswith("/api/hassio_ingress/app-entry/runtime"))

    def test_denied_access_never_retries_a_public_endpoint(self):
        error = urllib.error.HTTPError("fixture", 401, "denied", {}, None)
        with patch.object(reader, "rpc", side_effect=[{"ingress_entry": "app-entry"}, {"session": "test-session"}]), \
                patch.object(reader, "open_ingress", side_effect=error) as get:
            with self.assertRaises(ValueError):
                reader.read_runtime(Path("/fixture"))
        self.assertEqual(get.call_count, 1)

    def test_malformed_ingress_entry_cannot_redirect_a_credential(self):
        for entry in ("../other", "https://example.test", "app-entry\nCookie: forged"):
            with self.subTest(entry=entry), patch.object(reader, "rpc", return_value={"ingress_entry": entry}), \
                    patch.object(reader, "open_ingress") as get:
                with self.assertRaises(ValueError):
                    reader.read_runtime(Path("/fixture"))
                get.assert_not_called()

    def test_http_redirect_cannot_forward_ha_cookie(self):
        paths = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                paths.append(self.path)
                self.send_response(302)
                self.send_header("Location", "/other-destination")
                self.end_headers()

            def log_message(self, *args):
                pass

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        request = reader.urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}/runtime",
                                                headers={"Cookie": "ingress_session=test-session"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            reader.open_ingress(request)
        self.assertEqual(caught.exception.code, 302)
        caught.exception.close()
        self.assertEqual(paths, ["/runtime"])
