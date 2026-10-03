"""Headless Chromium reader used as Amazon's one-time fallback.

It loads the complete page with the browser cache disabled and accepts the HTML
only when the main document itself came from the network with a known status
and still belongs to the requested product.
"""

import json
import os
import shutil
import tempfile
from typing import Optional

from ...errors import HermesError, HttpStatusHermesError
from ...utils import extract_asin_from_url
from ..http import HtmlResponse

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service as ChromeService
except ImportError:  # pragma: no cover - Selenium is part of the add-on image
    webdriver = None
    ChromeService = None

MIN_TIMEOUT_SECONDS = 25
CHROMIUM_CANDIDATES = ("/usr/lib/chromium/chromium", "/usr/lib/chromium-browser/chromium-browser")


def chromium_binary() -> str:
    configured = os.getenv("HERMES_CHROMIUM_PATH", "").strip()
    if configured:
        return configured
    for candidate in CHROMIUM_CANDIDATES:
        if os.path.exists(candidate) and os.access(candidate, os.X_OK):
            return candidate
    for candidate in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        found = shutil.which(candidate)
        if found:
            return found
    raise HermesError("Gerçek tarayıcı modu kullanılamıyor; Chromium bulunamadı.")


def main_document_response(driver) -> Optional[dict]:
    """The main frame's document response of the current navigation, if proven."""
    messages = []
    for entry in driver.get_log("performance"):
        try:
            message = json.loads(entry["message"])["message"]
        except (KeyError, TypeError, ValueError):
            continue
        if isinstance(message, dict) and isinstance(message.get("params", {}), dict):
            messages.append(message)
    frame_id = loader_id = None
    for message in messages:
        frame = message.get("params", {}).get("frame", {})
        if message.get("method") == "Page.frameNavigated" and frame.get("id") and not frame.get("parentId"):
            frame_id, loader_id = frame["id"], frame.get("loaderId")
    if not frame_id:
        frame = driver.execute_cdp_cmd("Page.getFrameTree", {})["frameTree"]["frame"]
        frame_id, loader_id = frame["id"], frame.get("loaderId")
    document = None
    for message in messages:
        params = message.get("params", {})
        if params.get("type") != "Document" or params.get("frameId") != frame_id:
            continue
        if message.get("method") not in {"Network.requestWillBeSent", "Network.responseReceived"}:
            continue
        if loader_id and params.get("loaderId") != loader_id:
            # A later navigation started; never pair an earlier 200 with its DOM.
            document = None
            continue
        if message.get("method") == "Network.responseReceived":
            try:
                document = {**params["response"], "status": int(params["response"]["status"])}
            except (KeyError, TypeError, ValueError):
                document = None
    return document


class AmazonBrowser:
    """One anonymous Chromium profile and driver, started on first use."""

    def __init__(self) -> None:
        self.profile: Optional[tempfile.TemporaryDirectory] = None
        self.driver = None
        self.timeout: Optional[int] = None

    def close(self) -> None:
        try:
            if self.driver is not None:
                self.driver.quit()
        finally:
            self.driver = None
            if self.profile is not None:
                self.profile.cleanup()
                self.profile = None

    def _start(self):
        if webdriver is None or ChromeService is None:
            raise HermesError("Amazon gerçek tarayıcı desteği kurulmamış.")
        driver_binary = shutil.which("chromedriver")
        if not driver_binary:
            raise HermesError("Amazon gerçek tarayıcı sürücüsü bulunamadı.")
        if self.profile is None:
            self.profile = tempfile.TemporaryDirectory(prefix="hermes-amazon-browser-")
        options = webdriver.ChromeOptions()
        options.binary_location = chromium_binary()
        options.page_load_strategy = "normal"
        for argument in (
            "--headless=new", "--no-sandbox", "--disable-dev-shm-usage",
            "--disable-background-networking", "--no-first-run", "--lang=tr-TR",
            "--window-size=1365,900", "--remote-debugging-port=0", "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={self.profile.name}",
        ):
            options.add_argument(argument)
        options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
        driver = webdriver.Chrome(service=ChromeService(executable_path=driver_binary), options=options)
        try:
            driver.execute_cdp_cmd("Network.enable", {})
            driver.execute_cdp_cmd("Network.setBypassServiceWorker", {"bypass": True})
            driver.execute_cdp_cmd("Network.setCacheDisabled", {"cacheDisabled": True})
        except Exception:
            driver.quit()
            raise
        return driver

    def read(self, url: str, timeout: int) -> HtmlResponse:
        if self.driver is None:
            try:
                self.driver = self._start()
                self.timeout = None
            except Exception as exc:
                raise HermesError(f"Amazon gerçek tarayıcı oturumu başlatılamadı: {exc}") from exc
        driver = self.driver
        effective_timeout = max(MIN_TIMEOUT_SECONDS, int(timeout))
        try:
            if self.timeout != effective_timeout:
                driver.set_page_load_timeout(effective_timeout)
                self.timeout = effective_timeout
            driver.get_log("performance")  # Drain the previous page's events.
            driver.get(url)
            document = main_document_response(driver)
            if not document:
                raise HermesError("Amazon tarayıcıda ana belge ağ yanıtı doğrulanamadı.")
            if any(document.get(field) for field in ("fromDiskCache", "fromServiceWorker", "fromPrefetchCache")):
                raise HermesError("Amazon tarayıcıda güncel ana belge ağ üzerinden doğrulanamadı.")
            if document["status"] >= 400:
                raise HttpStatusHermesError(document["status"], url)
            html = driver.page_source
            final_url = driver.current_url
        except HermesError:
            raise
        except Exception as exc:
            raise HermesError(f"Amazon gerçek tarayıcı sayfası okunamadı ({type(exc).__name__}).") from exc
        requested_asin = extract_asin_from_url(url)
        if requested_asin and requested_asin != extract_asin_from_url(final_url):
            raise HermesError("Amazon tarayıcı farklı ürün kimliğine yönlendirildi; fiyat kullanılmadı.")
        return HtmlResponse(final_url, html, document["status"])
