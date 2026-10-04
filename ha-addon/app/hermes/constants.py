import os
from pathlib import Path

APP_VERSION = "3.5.1"

# Persistent add-on data. File names are a public interface: 2.x releases read
# the same files, so a rollback never loses history or notification state.
# HERMES_DATA_DIR is only for running Hermes outside Home Assistant.
DATA_DIR = Path(os.getenv("HERMES_DATA_DIR", "/data"))
OPTIONS_PATH = DATA_DIR / "options.json"
STATE_PATH = DATA_DIR / "state.json"
SUMMARY_PATH = DATA_DIR / "latest_price_summary.json"
CYCLE_HISTORY_PATH = DATA_DIR / "cycle_history.json"
# History and measurements since 3.2 (see history.py); the JSON files above
# keep their data for a rollback.
DATABASE_PATH = DATA_DIR / "hermes.db"
AMAZON_ACCESS_PATH = DATA_DIR / "amazon_access.json"
AMAZON_COOKIES_PATH = DATA_DIR / "amazon_cookies.json"
TELEGRAM_SESSION_PATH = DATA_DIR / "telegram_keyword_alert"
TELEGRAM_LOGIN_STATE_PATH = DATA_DIR / "login_state.json"
TELEGRAM_SEEN_MESSAGES_PATH = DATA_DIR / "seen_messages.json"
TELEGRAM_STATUS_PATH = DATA_DIR / "status.json"
TELEGRAM_ERROR_EVENTS_PATH = DATA_DIR / "error_events.json"
TELEGRAM_QUICK_ADD_PATH = DATA_DIR / "telegram_quick_add.json"

INGRESS_PORT = 8099
PUBLIC_PORT = 8100
PUSHOVER_URL = "https://api.pushover.net/1/messages.json"

SITE_AMAZON = "amazon"
SITE_HEPSIBURADA = "hepsiburada"
SITE_TRENDYOL = "trendyol"
SITE_NETWORK = "network"
SITE_BEYMENCLUB = "beymenclub"
SITE_BENGURME = "bengurme"
SITE_NORDBRON = "nordbron"
SITE_ZARA = "zara"
SITE_HM = "hm"
SITE_LABELS = {
    SITE_AMAZON: "Amazon",
    SITE_HEPSIBURADA: "Hepsiburada",
    SITE_TRENDYOL: "Trendyol",
    SITE_NETWORK: "Network",
    SITE_BEYMENCLUB: "Beymen Club",
    SITE_BENGURME: "Ben Gurme",
    SITE_NORDBRON: "Nordbron",
    SITE_ZARA: "Zara",
    SITE_HM: "H&M",
}
# Host fragments are checked in this order; "amazon" stays last because other
# hosts never contain it, while a broad match must not shadow a specific site.
SITE_HOST_MARKERS = (
    ("hepsiburada", SITE_HEPSIBURADA),
    ("trendyol", SITE_TRENDYOL),
    ("network", SITE_NETWORK),
    ("beymenclub", SITE_BEYMENCLUB),
    ("bengurme", SITE_BENGURME),
    ("nordbron", SITE_NORDBRON),
    ("zara", SITE_ZARA),
    ("hm.com", SITE_HM),
    ("amazon", SITE_AMAZON),
)
# Zara and H&M cards without a group are shown under this group.
FASHION_SITES = {SITE_ZARA, SITE_HM}
FASHION_GROUP = "Moda"

AMAZON_BASE_URL = "https://www.amazon.com.tr"
NOTIFY_REPEAT_SECONDS = 24 * 60 * 60
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
RETRY_DELAYS_SECONDS = [10, 30, 75]
DEFAULT_REQUEST_TIMEOUT_SECONDS = 20
DEFAULT_REQUEST_DELAY_MIN_SECONDS = 3
DEFAULT_REQUEST_DELAY_MAX_SECONDS = 8
DEFAULT_INTERVAL_SECONDS = 60
SEARCH_RESULT_LIMIT = 60
SEARCH_ERROR_NOTIFICATION_HOUR = 11
TELEGRAM_STATUS_HEARTBEAT_SECONDS = 60 * 60
TELEGRAM_QUICK_ADD_GROUP = "Paylaşılanlar"
PRIORITIES = ("high", "medium", "low")
# Minimum time between two request starts to one site, on top of the random
# request delay. Measured from ~17 hours of 2.5.48 Pi logs (2026-10-03), where
# all sites shared one queue, so per-site queues are not faster than before:
# Amazon median 5.1 s over 4,992 requests (lower quartile 4.5 s); Network
# median 1.0 s over 10 reads. Other sites had no reads in that window and keep
# only the configured delay.
SITE_MIN_REQUEST_GAP_SECONDS = {
    SITE_AMAZON: 5.0,
    SITE_NETWORK: 1.0,
}
PRIORITY_INTERVAL_SECONDS = {"medium": 2 * 60 * 60, "low": 6 * 60 * 60}

# -- Amazon access control (3.3.0) ---------------------------------------------
# Measured 2026-10-03 (Pi logs, 1,147 requests): three unbroken runs at ~10.8
# requests/min ran 32-36 minutes (347-395 requests) before the first block.
# After a block the whole site pauses; each failed single probe climbs one step.
PROTECTION_PAUSE_LADDER_SECONDS = (3 * 60, 6 * 60, 12 * 60, 20 * 60)
# After a block requests run at half speed (the minimum gap doubles) for an hour,
# and for ten minutes after every start.
AMAZON_RECOVERY_SLOW_SECONDS = 60 * 60
AMAZON_START_SLOW_SECONDS = 10 * 60
AMAZON_RECOVERY_SLOW_FACTOR = 2.0
# Rolling request window: starts at 300 in 35 minutes, +5 % after every clean
# hour whose window reached 80 % of the limit, never above 500. A block lowers
# the limit to 85 % of the window count it happened at and freezes it there.
AMAZON_WINDOW_SECONDS = 35 * 60
AMAZON_WINDOW_START_LIMIT = 300
AMAZON_WINDOW_MIN_LIMIT = 200
AMAZON_WINDOW_MAX_LIMIT = 500
AMAZON_WINDOW_RAISE_EVERY_SECONDS = 60 * 60
AMAZON_WINDOW_RAISE_FACTOR = 1.05
AMAZON_WINDOW_RAISE_MIN_USE = 0.8
AMAZON_WINDOW_THRESHOLD_FACTOR = 0.85
# 3.3.1: a block lowers the limit only once (never below 200) and the limit may rise again
# after every clean hour. The first 3.3.0 night showed that blocks do not follow the request
# count (one came at 0.2 requests/min), so a limit that fell with every block only starved
# the site (300 -> 119).
# 3.5.0: the Depo lane (main pages with their used listings) reads on its own thread, apart
# from the variant sweep, and shares the window and the pause with it. It may use the whole
# window limit; the sweep lane may use all of it except the part of the Depo lane's 28 % share
# that the Depo lane has not used yet.
AMAZON_MAIN_LANE_SHARE = 0.28
# How many gaps between two main-page reads the measurement line looks at.
AMAZON_MAIN_GAPS_KEPT = 200
# The whole site pauses only when two different pages fail one after the other. One page
# that is blocked again and again rests alone: the first block only ends its read; from
# the second block within 2 hours it rests 30 minutes, from the third 60 minutes.
AMAZON_PAGE_REPEAT_WINDOW_SECONDS = 2 * 60 * 60
AMAZON_QUARANTINE_SECONDS = (30 * 60, 60 * 60)
AMAZON_QUARANTINE_COUNT_RESET_SECONDS = 6 * 60 * 60
# Two reading rhythms per product watch: the configured page (with its used
# listing, where Amazon Depo offers show up) every 100 s, and the variant
# sweep every 270 s. A variant page that a watch excludes by title is read once
# for its neighbours and then taken from memory for 30 minutes.
AMAZON_MAIN_INTERVAL_SECONDS = 100
AMAZON_SWEEP_INTERVAL_SECONDS = 270
AMAZON_EXCLUDED_PAGE_REFRESH_SECONDS = 30 * 60
AMAZON_STATS_LOG_SECONDS = 10 * 60
AMAZON_COOKIE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60

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

CHROME_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
CHROME_CLIENT_HINTS = {
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Linux"',
}
USER_AGENTS = [
    CHROME_USER_AGENT,
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]
