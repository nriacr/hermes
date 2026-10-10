import os
from pathlib import Path

APP_VERSION = "4.0.3"

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
SITE_TOGG = "togg"
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
    SITE_TOGG: "Togg",
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
    ("togg", SITE_TOGG),
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
# 3.12.0: five priorities named by how often they are read, from every cycle to every 6 hours.
# Values from before 3.12 ("high", "medium", "low") and missing or unknown values mean the lowest
# priority: the owner moved every existing card to 6 hours with this release.
PRIORITIES = ("cycle", "30m", "60m", "3h", "6h")
DEFAULT_PRIORITY = "cycle"
LOWEST_PRIORITY = "6h"
PRIORITY_LABELS = {"cycle": "Her çevrim", "30m": "30 dk", "60m": "60 dk", "3h": "3 saat", "6h": "6 saat"}
PRIORITY_DESCRIPTIONS = {
    "cycle": "Her çevrimde fiyat taranır", "30m": "30 dk'da bir taranır", "60m": "60 dk'da bir taranır",
    "3h": "3 saatte bir taranır", "6h": "6 saatte bir taranır",
}
# Minimum time between two request starts to one site, on top of the random
# request delay (Network only; since 3.8.3 Amazon has none: its client waits the configured
# random delay before every request instead).
SITE_MIN_REQUEST_GAP_SECONDS = {
    SITE_NETWORK: 1.0,
}
# The priority alone decides how often a watch is read, on every site; "cycle" every cycle
# (Amazon: every search round, see below).
PRIORITY_INTERVAL_SECONDS = {"30m": 30 * 60, "60m": 60 * 60, "3h": 3 * 60 * 60, "6h": 6 * 60 * 60}
# 3.8.1: an every-cycle (red) watch is read once per search round (the Depo lane goes through every active card and starts
# over); the round lasts as long as the reads in it, so yellow and green cards due in a round lengthen it. The
# floor only keeps a list with very few red cards from hammering Amazon.
AMAZON_RED_ROUND_FLOOR_SECONDS = 20
AMAZON_PRIORITY_INTERVAL_SECONDS = {"cycle": AMAZON_RED_ROUND_FLOOR_SECONDS, **PRIORITY_INTERVAL_SECONDS}


def normalize_priority(value) -> str:
    """A current priority key; anything else (pre-3.12 values, empty, unknown) is the lowest."""
    text = str(value or "").strip().casefold()
    return text if text in PRIORITIES else LOWEST_PRIORITY

# -- Amazon access control (3.3.0) ---------------------------------------------
# Measured 2026-10-03 (Pi logs, 1,147 requests): three unbroken runs at ~10.8
# requests/min ran 32-36 minutes (347-395 requests) before the first block.
# After a block the whole site pauses; each failed single probe climbs one step.
# 3.6.0: the 2026-10-04 Pi log (3,130 requests, 60 blocks) showed that a block marks the
# visitor, not the page: 43 of the 60 requests right after a block were blocked too. The
# first block pauses all of Amazon. Since 41 clean hours at 6 requests/min showed that the
# blocks came from Hermes' mixed-up browser identity (fixed in 3.6.0) and not from volume,
# 3.7.0 keeps the pause short and lets the speed governor below do the careful part.
PROTECTION_PAUSE_LADDER_SECONDS = (5 * 60, 10 * 60, 20 * 60, 30 * 60)
# Speed governor (3.7.0): a block wave doubles every category's reading interval (x2, then x4
# on the next wave, never more); every clean AMAZON_SLOWDOWN_RECOVER_SECONDS halves it again
# until the normal speed is back. Speed stays the goal: nothing else slows reads down.
AMAZON_SLOWDOWN_MAX = 4.0
AMAZON_SLOWDOWN_RECOVER_SECONDS = 10 * 60
# For ten minutes after every start the minimum gap between requests doubles.
AMAZON_START_SLOW_SECONDS = 10 * 60
AMAZON_START_SLOW_FACTOR = 2.0
# Rolling request window (3.8.3: starts at 500, up to 700, because 41 clean hours at 6 requests/min and the
# red category's search rounds need more room than the old 300; the speed governor handles real blocks): starts at 500 in 35 minutes, +5 % after every clean
# hour whose window reached 80 % of the limit, never above 500. A block lowers
# the limit to 85 % of the window count it happened at and freezes it there.
AMAZON_WINDOW_SECONDS = 35 * 60
AMAZON_WINDOW_START_LIMIT = 500
AMAZON_WINDOW_MIN_LIMIT = 200
AMAZON_WINDOW_MAX_LIMIT = 700
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
# 3.5.2: the share is not held back idle. The sweep leaves the Depo lane only what it still
# needs: as much as it used in the last 35 minutes, at least this many requests, at most the share.
AMAZON_MAIN_LANE_FLOOR = 12
# How many gaps between two reads of the same watch the measurement line looks at (a red watch: the round time).
AMAZON_MAIN_GAPS_KEPT = 200
# After a block the client itself sends nothing for this long, so a read already under way
# in the other lane stops at its next request instead of collecting more blocks.
AMAZON_BLOCK_HOLD_SECONDS = PROTECTION_PAUSE_LADDER_SECONDS[0]
# 3.9.0: a product watch is read as a whole (its configured page with the used listing, where Amazon Depo
# offers show up, and every variant): an every-cycle one once per search round, the others every
# AMAZON_PRIORITY_INTERVAL_SECONDS. A variant page that a watch excludes by title is read once
# for its neighbours and then taken from memory for 30 minutes.
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
