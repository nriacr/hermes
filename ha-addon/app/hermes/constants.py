from pathlib import Path

APP_VERSION = "2.2.53"
OPTIONS_PATH = Path("/data/options.json")
STATE_PATH = Path("/data/state.json")
SUMMARY_PATH = Path("/data/latest_price_summary.json")
TELEGRAM_SESSION_PATH = Path("/data/telegram_keyword_alert")
TELEGRAM_LOGIN_STATE_PATH = Path("/data/login_state.json")
TELEGRAM_SEEN_MESSAGES_PATH = Path("/data/seen_messages.json")
TELEGRAM_STATUS_PATH = Path("/data/status.json")
TELEGRAM_ERROR_EVENTS_PATH = Path("/data/error_events.json")
PUSHOVER_URL = "https://api.pushover.net/1/messages.json"

SITE_AMAZON = "amazon"
SITE_HEPSIBURADA = "hepsiburada"
SITE_TRENDYOL = "trendyol"
SITE_NETWORK = "network"
SITE_NORDBRON = "nordbron"
SITE_ZARA = "zara"
SITE_HM = "hm"
SITE_LABELS = {
    SITE_AMAZON: "Amazon",
    SITE_HEPSIBURADA: "Hepsiburada",
    SITE_TRENDYOL: "Trendyol",
    SITE_NETWORK: "Network",
    SITE_NORDBRON: "Nordbron",
    SITE_ZARA: "Zara",
    SITE_HM: "H&M",
}

AMAZON_BASE_URL = "https://www.amazon.com.tr"
NOTIFY_REPEAT_SECONDS = 24 * 60 * 60
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
RETRY_DELAYS_SECONDS = [10, 30, 75]
DEFAULT_REQUEST_TIMEOUT_SECONDS = 20
DEFAULT_REQUEST_DELAY_MIN_SECONDS = 3
DEFAULT_REQUEST_DELAY_MAX_SECONDS = 8
AMAZON_SEARCH_HTTP_COOLDOWN_SECONDS = 45 * 60
AMAZON_SEARCH_ERROR_NOTIFICATION_HOUR = 11
TELEGRAM_STATUS_HEARTBEAT_SECONDS = 60 * 60

DEFAULT_HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]
