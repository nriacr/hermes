import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from support import APP_PATH  # noqa: F401

from hermes.errors import BotProtectionHermesError, HermesError, HttpStatusHermesError, OutOfStockHermesError, error_status
from hermes.providers.amazon import browser as amazon_browser
from hermes.providers.amazon import client as amazon_client
from hermes.providers.amazon.client import AmazonClient, is_protection_error, is_protection_page, request_url
from hermes.providers.http import HtmlResponse

PRODUCT = "https://www.amazon.com.tr/dp/B000000001"


def response(status: int, html: str, url: str = PRODUCT) -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result._content = html.encode("utf-8")
    result.encoding = "utf-8"
    result.url = url
    return result


class FakeCurlSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.cookies = requests.cookies.RequestsCookieJar()
        self.closed = False

    def get(self, *_args, **_kwargs):
        self.calls += 1
        self.cookies.set("session-id", "keep-me")
        item = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        self.closed = True


def curl_module(session):
    return SimpleNamespace(Session=lambda: session)


class AmazonProtectionDetectionTests(unittest.TestCase):
    def test_scripts_styles_and_product_words_are_not_a_challenge(self):
        html = '''<html><title>Amazon</title><body>
          <span id="productTitle">Robot süpürge</span>
          <script>var url="/errors/validateCaptcha"; var message="not a robot";</script>
          <style>.captcha { color:red; }</style><!-- Robot Check -->
          <img src="captcha-example.jpg"><p>CAPTCHA teknolojisi hakkında bilgi</p>
        </body></html>'''
        self.assertFalse(is_protection_page(html))

    def test_real_challenges_are_detected(self):
        for html in (
            '<form action="/errors_page/validateCaptcha"><button>Alışverişe Devam Et</button></form>',
            '<form action="/errors/validateCaptcha"><input id="captchacharacters"></form>',
            '<title>Robot Check</title><body>Amazon</body>',
            '<body>Enter the characters you see below</body>',
            '<body>Robot olmadığınızı doğrulayın</body>',
            '<body>For automated access to Amazon data please contact us</body>',
        ):
            with self.subTest(html=html):
                self.assertTrue(is_protection_page(html))

    def test_protection_errors_are_challenges_and_http_429_503(self):
        self.assertTrue(is_protection_error(BotProtectionHermesError("x")))
        self.assertTrue(is_protection_error(HttpStatusHermesError(503, PRODUCT)))
        self.assertTrue(is_protection_error(requests.HTTPError("x", response=SimpleNamespace(status_code=429))))
        self.assertTrue(is_protection_error(HermesError("Amazon captcha")))
        self.assertFalse(is_protection_error(HttpStatusHermesError(500, PRODUCT)))
        self.assertFalse(is_protection_error(requests.Timeout("timeout")))

    def test_request_url_keeps_only_variant_parameters(self):
        url = "https://www.amazon.com.tr/gp/product/B0B2PSDNV1?ref=ppx_yo2ov_dt_b_fed_asin_title&th=1"
        self.assertEqual(request_url(url), "https://www.amazon.com.tr/dp/B0B2PSDNV1?th=1")
        self.assertEqual(request_url("https://www.amazon.com.tr/s?k=test&ref=x"), "https://www.amazon.com.tr/s?k=test&ref=x")


class AmazonTransportTests(unittest.TestCase):
    def test_page_is_cached_within_a_cycle_but_read_again_next_cycle(self):
        curl = FakeCurlSession([response(200, "<html>Amazon price 1</html>"), response(200, "<html>Amazon price 2</html>")])
        with patch.object(amazon_client, "curl_requests", curl_module(curl)):
            with AmazonClient() as client:
                first_cycle, second_cycle = {}, {}
                first = client.fetch(PRODUCT, 10, cache=first_cycle)
                self.assertIs(first, client.fetch(PRODUCT + "?ref=other", 10, cache=first_cycle))
                second = client.fetch(PRODUCT, 10, cache=second_cycle)
                self.assertNotEqual(first, second)
                self.assertEqual(curl.calls, 2)
                # Anonymous cookies and connections survive between cycles.
                self.assertEqual(curl.cookies.get("session-id"), "keep-me")
        self.assertTrue(curl.closed)

    def test_challenge_page_is_terminal_without_rescue(self):
        curl = FakeCurlSession([response(200, '<form action="/errors_page/validateCaptcha">Amazon</form>')])
        with (patch.object(amazon_client, "curl_requests", curl_module(curl)),
              AmazonClient() as client, patch.object(client.browser, "read") as browser):
            with self.assertRaises(BotProtectionHermesError):
                client.fetch("https://www.amazon.com.tr/s?k=test", 10, expect_search=True)
        self.assertEqual(curl.calls, 1)
        browser.assert_not_called()

    def test_http_429_and_503_stop_after_one_request(self):
        for status in (429, 503):
            with self.subTest(status=status):
                curl = FakeCurlSession([response(status, "Amazon temporary error")])
                with (patch.object(amazon_client, "curl_requests", curl_module(curl)), AmazonClient() as client,
                      patch.object(client.browser, "read") as browser, patch.object(client.requests_session, "get") as plain):
                    with self.assertRaises(requests.HTTPError) as caught:
                        client.fetch("https://www.amazon.com.tr/s?k=test", 10, expect_search=True)
                    self.assertEqual(error_status(caught.exception), status)
                    browser.assert_not_called()
                    plain.assert_not_called()
                    self.assertEqual(curl.calls, 1)

    def test_503_page_with_a_challenge_form_is_classified_by_status(self):
        curl = FakeCurlSession([response(503, '<form action="/errors/validateCaptcha">Amazon</form>')])
        with patch.object(amazon_client, "curl_requests", curl_module(curl)), AmazonClient() as client:
            with self.assertRaises(requests.HTTPError) as caught:
                client.fetch(PRODUCT, 10)
        self.assertEqual(error_status(caught.exception), 503)

    def test_other_failure_gets_one_browser_read_which_is_cached(self):
        curl = FakeCurlSession([requests.Timeout("timeout")])
        cache = {}
        with (patch.object(amazon_client, "curl_requests", curl_module(curl)), AmazonClient() as client,
              patch.object(client.browser, "read", return_value=HtmlResponse(PRODUCT, "<html>Amazon product</html>", 200)) as browser):
            first = client.fetch(PRODUCT, 10, cache=cache)
            second = client.fetch(PRODUCT, 10, cache=cache)
        self.assertEqual(first, second)
        self.assertEqual(curl.calls, 1)
        browser.assert_called_once()

    def test_browser_failure_does_not_start_more_transports(self):
        curl = FakeCurlSession([requests.Timeout("timeout")])
        with (patch.object(amazon_client, "curl_requests", curl_module(curl)), AmazonClient() as client,
              patch.object(client.browser, "read", side_effect=HermesError("browser timeout")) as browser):
            with self.assertRaisesRegex(HermesError, "browser timeout"):
                client.fetch("https://www.amazon.com.tr/s?k=test", 10, expect_search=True)
        self.assertEqual(curl.calls, 1)
        browser.assert_called_once()

    def test_requests_is_used_when_curl_is_unavailable(self):
        with patch.object(amazon_client, "curl_requests", None), AmazonClient() as client, \
                patch.object(client.requests_session, "get", return_value=response(200, "<html>Amazon</html>")) as get:
            self.assertIn("Amazon", client.fetch(PRODUCT, 10))
        get.assert_called_once()

    def test_wrong_kind_of_search_page_is_an_error(self):
        curl = FakeCurlSession([response(200, "<html>Amazon ana sayfa</html>")])
        with (patch.object(amazon_client, "curl_requests", curl_module(curl)), AmazonClient() as client,
              patch.object(client.browser, "read", side_effect=HermesError("browser")) as browser):
            with self.assertRaises(HermesError):
                client.fetch("https://www.amazon.com.tr/s?k=test", 10, expect_search=True)
        browser.assert_called_once()

    def test_genuine_empty_search_page_is_usable(self):
        html = '<html>Amazon<div id="search"><h3>Amazon Depo içinde juo 240w için sonuç bulunamadı</h3></div></html>'
        self.assertIn("sonuç bulunamadı", amazon_client.checked_html(response(200, html), expect_search=True))

    def test_browser_transport_reads_only_through_chromium(self):
        with (patch.object(amazon_client, "curl_requests", curl_module(FakeCurlSession([response(200, "Amazon")]))),
              AmazonClient(transport="browser") as client,
              patch.object(client.browser, "read", return_value=HtmlResponse(PRODUCT, "<html>Amazon</html>", 200)) as browser,
              patch.object(client, "_http_read") as http_read):
            client.fetch(PRODUCT, 10)
        browser.assert_called_once()
        http_read.assert_not_called()

    def test_browser_captcha_and_service_failure_are_errors_not_stock(self):
        for status, html in ((503, "<html>Amazon Service Unavailable</html>"),
                             (200, '<html>Amazon<form action="/errors/validateCaptcha"><input name="captchacharacters"></form></html>')):
            with self.subTest(status=status), AmazonClient(transport="browser") as client:
                read = (patch.object(client.browser, "read", side_effect=HttpStatusHermesError(503, PRODUCT)) if status == 503
                        else patch.object(client.browser, "read", return_value=HtmlResponse(PRODUCT, html, status)))
                with read, self.assertRaises(HermesError) as caught:
                    client.fetch(PRODUCT, 10)
                self.assertNotIsInstance(caught.exception, OutOfStockHermesError)
                self.assertEqual(error_status(caught.exception), 503 if status == 503 else None)
                self.assertTrue(is_protection_error(caught.exception))


def performance_entry(method: str, **params) -> dict:
    return {"message": json.dumps({"message": {"method": method, "params": params}})}


class AmazonBrowserTests(unittest.TestCase):
    def test_main_document_status_ignores_frames_and_subresources(self):
        driver = Mock()
        driver.execute_cdp_cmd.return_value = {"frameTree": {"frame": {"id": "main"}}}
        driver.get_log.return_value = [
            performance_entry("Network.responseReceived", frameId="iframe", type="Document", response={"status": 503}),
            performance_entry("Network.responseReceived", frameId="main", type="Document", response={"status": 200}),
            performance_entry("Network.responseReceived", frameId="main", type="Image", response={"status": 503}),
        ]
        self.assertEqual(amazon_browser.main_document_response(driver)["status"], 200)
        driver.get_log.return_value = []
        self.assertIsNone(amazon_browser.main_document_response(driver))

    def test_late_navigation_never_pairs_an_earlier_200_with_new_html(self):
        driver = Mock()
        driver.get_log.return_value = [
            performance_entry("Page.frameNavigated", frame={"id": "main", "loaderId": "one"}),
            performance_entry("Network.responseReceived", frameId="main", type="Document", loaderId="one", response={"status": 200}),
            performance_entry("Network.requestWillBeSent", frameId="main", type="Document", loaderId="two"),
        ]
        self.assertIsNone(amazon_browser.main_document_response(driver))
        driver.execute_cdp_cmd.assert_not_called()

    def _browser_with(self, driver):
        browser = amazon_browser.AmazonBrowser()
        browser.profile = tempfile.TemporaryDirectory(prefix="hermes-test-browser-")
        browser.driver = driver
        return browser

    def test_driver_is_reused_and_cleaned_up(self):
        driver = Mock(page_source="<html>Amazon product</html>", current_url=PRODUCT)
        browser = amazon_browser.AmazonBrowser()

        def start():
            browser.profile = tempfile.TemporaryDirectory(prefix="hermes-test-browser-")
            return driver

        with (patch.object(browser, "_start", side_effect=start) as launch,
              patch.object(amazon_browser, "main_document_response", return_value={"status": 200})):
            for _ in range(2):
                self.assertEqual(browser.read(PRODUCT, 10).status_code, 200)
        profile = Path(browser.profile.name)
        launch.assert_called_once()
        driver.set_page_load_timeout.assert_called_once_with(25)
        self.assertEqual(driver.get.call_count, 2)
        browser.close()
        driver.quit.assert_called_once()
        self.assertFalse(profile.exists())

    def test_cached_unverified_failed_or_wrong_product_documents_are_rejected(self):
        for document, expected in ((None, HermesError), ({"status": 200, "fromDiskCache": True}, HermesError),
                                   ({"status": 200, "fromServiceWorker": True}, HermesError),
                                   ({"status": 200, "fromPrefetchCache": True}, HermesError),
                                   ({"status": 503}, HttpStatusHermesError)):
            driver = Mock(page_source="<span>Amazon</span>", current_url=PRODUCT)
            with self.subTest(document=document), patch.object(amazon_browser, "main_document_response", return_value=document):
                browser = self._browser_with(driver)
                with self.assertRaises(expected):
                    browser.read(PRODUCT, 10)
                browser.close()
        driver = Mock(page_source="<span>Amazon</span>", current_url=PRODUCT)
        with patch.object(amazon_browser, "main_document_response", return_value={"status": 200}):
            browser = self._browser_with(driver)
            with self.assertRaisesRegex(HermesError, "farklı ürün"):
                browser.read("https://www.amazon.com.tr/dp/B000000002", 10)
            browser.close()

    def test_missing_browser_is_a_clear_error(self):
        browser = amazon_browser.AmazonBrowser()
        with patch.object(browser, "_start", side_effect=HermesError("Chromium bulunamadı")):
            with self.assertRaisesRegex(HermesError, "oturumu başlatılamadı"):
                browser.read(PRODUCT, 10)


if __name__ == "__main__":
    unittest.main()
