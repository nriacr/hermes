"""Shared helpers for the Hermes regression tests."""

import json
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import Mock

# Nothing in the tests may touch the real /data (Amazon's budget and cookie files live there).
os.environ.setdefault("HERMES_DATA_DIR", tempfile.mkdtemp(prefix="hermes-test-data-"))

APP_PATH = Path(__file__).resolve().parents[1] / "ha-addon" / "app"
if str(APP_PATH) not in sys.path:
    sys.path.insert(0, str(APP_PATH))

from hermes import logging_utils  # noqa: E402
from hermes.history import History  # noqa: E402
from hermes.models import HermesConfig, TelegramConfig, WatchRule  # noqa: E402
from hermes.monitor.cycle import DataFiles, Monitor  # noqa: E402
from hermes.monitor.state import watch_key  # noqa: E402
from hermes.providers.registry import ProviderSet  # noqa: E402

# Request gaps are tested on their own; elsewhere tests must not sleep.
from hermes import constants  # noqa: E402

for _site in list(constants.SITE_MIN_REQUEST_GAP_SECONDS):
    constants.SITE_MIN_REQUEST_GAP_SECONDS[_site] = 0

# Tests assert on behavior, not on log lines; keep the run output readable.
LOG_LINES: List[str] = []
logging_utils.set_output(LOG_LINES.append)


def amazon_product(asin: str = "B000000001", **params: str) -> str:
    url = f"https://www.amazon.com.tr/dp/{asin}"
    return url + ("?" + "&".join(f"{key}={value}" for key, value in params.items()) if params else "")


def watch(name: str = "iPhone", url: str = "https://www.amazon.com.tr/dp/B000000001", target: str = "1000",
          site: Optional[str] = None, **fields: Any) -> WatchRule:
    from hermes.utils import detect_site_from_url

    return WatchRule(name=name, site=site or detect_site_from_url(url), url=url, target_price=Decimal(target), **fields)


def telegram_config(**fields: Any) -> TelegramConfig:
    values = dict(enabled=False, api_id=None, api_hash="", phone_number="", verification_code="",
                  session_name="telegram_keyword_alert", channels=[], keywords=[], exclude_keywords=[])
    values.update(fields)
    return TelegramConfig(**values)


def config(watches: List[WatchRule], interval_seconds: int = 1, pushover: bool = True) -> HermesConfig:
    return HermesConfig(
        interval_seconds=interval_seconds,
        request_timeout_seconds=10,
        request_delay_min_seconds=0,
        request_delay_max_seconds=0,
        pushover_user_key="user" if pushover else "",
        pushover_api_token="token" if pushover else "",
        watches=watches,
        telegram=telegram_config(),
    )


def notifier(configured: bool = True) -> Mock:
    mock = Mock()
    mock.configured = configured
    return mock


class TempData:
    """A private /data directory for one test."""

    def __init__(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        root = Path(self._dir.name)
        self.root = root
        self.files = DataFiles(state=root / "state.json", summary=root / "summary.json", cycle_history=root / "cycles.json",
                               database=root / "hermes.db")

    def cleanup(self) -> None:
        History.at(self.files.database).close()
        self._dir.cleanup()

    def write_state(self, state: Dict[str, Any]) -> None:
        self.files.state.write_text(json.dumps(state), encoding="utf-8")

    def state(self) -> Dict[str, Any]:
        return json.loads(self.files.state.read_text(encoding="utf-8")) if self.files.state.exists() else {}

    def write_summary(self, summary: Dict[str, Any]) -> None:
        self.files.summary.write_text(json.dumps(summary), encoding="utf-8")

    def summary(self) -> Dict[str, Any]:
        return json.loads(self.files.summary.read_text(encoding="utf-8")) if self.files.summary.exists() else {}


def monitor(cfg: HermesConfig, data: TempData, notify: Optional[Mock] = None, providers: Optional[ProviderSet] = None) -> Monitor:
    return Monitor(cfg, providers=providers or ProviderSet(), notifier=notify or notifier(), files=data.files,
                   sleep=lambda _seconds: None)


def key(rule: WatchRule) -> str:
    return watch_key(rule)


def price_html(price: str, title: str = "iPhone", extra: str = "") -> str:
    return (f'<span id="productTitle">{title}</span><div id="corePrice_feature_div"><span class="a-price">'
            f'<span class="a-offscreen">{price} TL</span></span></div>{extra}')


def search_card(asin: str, title: str, price: Optional[str] = "100,00", extra: str = "") -> str:
    price_html_part = f'<span class="a-price"><span class="a-offscreen">{price} TL</span></span>' if price else ""
    return (f'<div data-component-type="s-search-result" data-asin="{asin}"><h2><a href="/dp/{asin}"><span>{title}</span>'
            f'</a></h2>{price_html_part}{extra}</div>')
