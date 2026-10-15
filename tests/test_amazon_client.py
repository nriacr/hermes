import unittest
from types import SimpleNamespace
from unittest.mock import patch

import requests

from support import APP_PATH  # noqa: F401

from hermes.errors import BotProtectionHermesError, HermesError, HttpStatusHermesError, error_status
from hermes.providers.amazon import client as amazon_client
from hermes.providers.amazon.client import AmazonClient, is_protection_error, is_protection_page, request_url

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
              AmazonClient() as client):
            with self.assertRaises(BotProtectionHermesError):
                client.fetch("https://www.amazon.com.tr/s?k=test", 10, expect_search=True)
        self.assertEqual(curl.calls, 1)

    def test_http_429_and_503_stop_after_one_request(self):
        for status in (429, 503):
            with self.subTest(status=status):
                curl = FakeCurlSession([response(status, "Amazon temporary error")])
                with (patch.object(amazon_client, "curl_requests", curl_module(curl)), AmazonClient() as client,
                      patch.object(client.requests_session, "get") as plain):
                    with self.assertRaises(requests.HTTPError) as caught:
                        client.fetch("https://www.amazon.com.tr/s?k=test", 10, expect_search=True)
                    self.assertEqual(error_status(caught.exception), status)
                    plain.assert_not_called()
                    self.assertEqual(curl.calls, 1)

    def test_503_page_with_a_challenge_form_is_classified_by_status(self):
        curl = FakeCurlSession([response(503, '<form action="/errors/validateCaptcha">Amazon</form>')])
        with patch.object(amazon_client, "curl_requests", curl_module(curl)), AmazonClient() as client:
            with self.assertRaises(requests.HTTPError) as caught:
                client.fetch(PRODUCT, 10)
        self.assertEqual(error_status(caught.exception), 503)

    def test_failed_read_is_raised_once_without_a_second_transport(self):
        for failure in (requests.Timeout("timeout"), requests.ConnectionError("dns")):
            curl = FakeCurlSession([failure])
            with (patch.object(amazon_client, "curl_requests", curl_module(curl)), AmazonClient() as client,
                  patch.object(client.requests_session, "get") as plain):
                with self.assertRaises(requests.RequestException) as caught:
                    client.fetch(PRODUCT, 10)
            self.assertIs(caught.exception, failure)
            self.assertEqual(curl.calls, 1)
            plain.assert_not_called()

    def test_requests_is_used_when_curl_is_unavailable(self):
        with patch.object(amazon_client, "curl_requests", None), AmazonClient() as client, \
                patch.object(client.requests_session, "get", return_value=response(200, "<html>Amazon</html>")) as get:
            self.assertIn("Amazon", client.fetch(PRODUCT, 10))
        get.assert_called_once()

    def test_wrong_kind_of_search_page_is_an_error(self):
        curl = FakeCurlSession([response(200, "<html>Amazon ana sayfa</html>")])
        with (patch.object(amazon_client, "curl_requests", curl_module(curl)), AmazonClient() as client):
            with self.assertRaises(HermesError):
                client.fetch("https://www.amazon.com.tr/s?k=test", 10, expect_search=True)
        self.assertEqual(curl.calls, 1)

    def test_genuine_empty_search_page_is_usable(self):
        html = '<html>Amazon<div id="search"><h3>Amazon Depo içinde juo 240w için sonuç bulunamadı</h3></div></html>'
        self.assertIn("sonuç bulunamadı", amazon_client.checked_html(response(200, html), expect_search=True))

