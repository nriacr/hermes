"""Pi-only functional check of the real reader against a loopback fixture server.

No Amazon requests, authenticated pages, production prices, or settings writes.
"""

import json
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlsplit

import requests

from .http_client import AmazonClient, _get_amazon_response_with_browser
from .providers.amazon import browser_coverage_snapshot


def run_browser_check():
    counts = Counter()
    headers_seen = []

    class FixtureHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            policy = query.get("policy", [""])[0]
            case = query.get("case", [""])[0]
            if parsed.path.startswith("/asset/"):
                counts[parsed.path] += 1
                time.sleep(2)  # Simulated slow image; not an Amazon speed benchmark.
                body = b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"></svg>'
                content_type = "image/svg+xml"
            elif parsed.path.startswith("/dp/") and policy in {"full_cold", "ready_cold", "ready_cached"} and case in {"stable", "late"}:
                counts[(policy, case)] += 1
                reading = counts[(policy, case)]
                headers_seen.append({"policy": policy, "case": case,
                                     "cache_control": self.headers.get("Cache-Control", "")})
                used = f'''<div id="usedBuySection">Kullanılmış - Yeni Gibi
                  <span class="a-price"><span class="a-offscreen">{70 + reading},00 TL</span></span>Satıcı: Amazon Depo</div>'''
                extra = '<li data-asin="B000000003" title="Turuncu"></li>'
                script = (f"setTimeout(() => {{document.querySelector('#variation_color_name ul').insertAdjacentHTML('beforeend', {json.dumps(extra)});"
                          f"document.querySelector('#dp-container').insertAdjacentHTML('beforeend', {json.dumps(used)});}}, 1100);"
                          if case == "late" else "")
                body = f'''<!doctype html><html><head><title>Amazon Hermes test</title></head><body>
                  <div id="dp-container"><span id="productTitle">Hermes test telefonu Gümüş 256 GB</span>
                  <div id="corePrice_feature_div"><span class="a-price"><span class="a-offscreen">{100 + reading},00 TL</span></span></div>
                  <div id="merchant-info">Satıcı Amazon.com.tr</div><div id="availability">Stokta sadece {2 + reading} adet kaldı</div>
                  <div id="variation_color_name"><ul><li data-asin="B000000002" title="Siyah"></li></ul></div>
                  {used if case == "stable" else ''}</div>
                  <img src="/asset/{policy}.svg"><script>{script}</script></body></html>'''.encode()
                content_type = "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            # Both documents and the image deliberately look cacheable. The
            # reader must revalidate the document while reusing the image.
            self.send_header("Cache-Control", "public, max-age=3600")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Eager navigation can leave a simulated slow image behind.

    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    result = {"passed": False, "amazon_requests": 0, "profiles": []}
    try:
        for policy in ("full_cold", "ready_cold", "ready_cached"):
            profile = {"policy": policy, "reads": []}
            with AmazonClient(transport="browser", browser_policy=policy) as client:
                for case, asin in (("stable", "B000000001"), ("late", "B000000010")):
                    url = f"http://127.0.0.1:{server.server_port}/dp/{asin}?case={case}&policy={policy}"
                    for reading in (1, 2):
                        started = time.monotonic()
                        with requests.Session() as session:
                            session._hermes_amazon_client = client
                            response = _get_amazon_response_with_browser(session, url, 25, False)
                        snapshot = browser_coverage_snapshot(response.text, url, False)
                        offers = snapshot.get("offers", [])
                        expected_asins = {asin, "B000000002"} | ({"B000000003"} if case == "late" else set())
                        actual_asins = {item[0].split('/dp/')[-1].split('?')[0] for item in snapshot["variants"]}
                        correct = (len(offers) == 2 and offers[0]["price"] == f"{100 + reading}.00"
                                   and offers[1]["price"] == f"{70 + reading}.00" and offers[1]["warehouse"]
                                   and offers[0]["seller"] == "Amazon.com.tr"
                                   and offers[0]["stock_quantity"] == 2 + reading and actual_asins == expected_asins)
                        profile["reads"].append({"case": case, "reading": reading, "passed": correct,
                                                 "seconds": round(time.monotonic() - started, 3),
                                                 "snapshot": snapshot})
                profile["audits"] = list(client.browser_audits)
            profile["document_requests"] = counts[(policy, "stable")] + counts[(policy, "late")]
            profile["image_requests"] = counts[f"/asset/{policy}.svg"]
            result["profiles"].append(profile)
        result["document_headers"] = headers_seen
        result["passed"] = (all(row["passed"] for profile in result["profiles"] for row in profile["reads"])
                            and all(profile["document_requests"] == 4 for profile in result["profiles"])
                            and result["profiles"][2]["image_requests"] == 1
                            and all(row["cache_control"] == "no-cache" for row in headers_seen if row["policy"] == "ready_cached")
                            and any("variants" in audit["changed"] and "offers" in audit["changed"]
                                    for audit in result["profiles"][2]["audits"]))
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    return result
