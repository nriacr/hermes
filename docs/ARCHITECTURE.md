# Hermes architecture (3.x)

This document describes how Hermes is built and the behavior each part must
keep. Source code and tests are authoritative; `AGENTS.md` holds the binding
working rules.

## 1. Product

Hermes is a Home Assistant add-on running continuously on a Raspberry Pi. It:

- checks product and search-page URLs on Turkish commerce sites;
- detects each URL's site and product/search type automatically;
- evaluates prices, product variants, requested clothing sizes and stock;
- notifies opportunities and Hermes warnings through Pushover;
- relays Telegram channel keywords and adds watches from Saved Messages;
- serves one management panel through Home Assistant ingress and an optional
  token-protected public address (desktop and mobile);
- keeps notification suppression and lifetime min/max prices across restarts.

## 2. Repository map

```text
.
├── AGENTS.md                         # Binding agent rules
├── docs/ARCHITECTURE.md              # This document
├── repository.yaml                   # Home Assistant repository metadata
├── ha-addon/
│   ├── config.yaml                   # Version, options/schema, ports, watchdog
│   ├── CHANGELOG.md                  # Shown in the Home Assistant add-on store
│   ├── Dockerfile                    # Python 3.12 + Chromium image
│   ├── run.sh                        # exec python -m hermes
│   └── app/hermes/
│       ├── __main__.py, app.py       # One process: monitor, panels, Telegram
│       ├── config.py                 # Options: read, validate, defaults
│       ├── supervisor.py             # Save options + restart via Supervisor
│       ├── storage.py                # Atomic JSON files under /data
│       ├── history.py                # SQLite: cycles, price points, measurements
│       ├── notifier.py               # Pushover
│       ├── homeassistant.py          # HA sensors and the hermes_firsat event
│       ├── models.py, errors.py, utils.py, constants.py
│       ├── monitor/
│       │   ├── cycle.py              # One cycle: read, record, notify, publish
│       │   ├── runner.py             # Loop, panel commands, health
│       │   ├── scheduling.py         # Due checks, priority and site order
│       │   ├── state.py              # state.json entries, history, the per-site guard
│       │   ├── summary.py            # latest_price_summary.json, cycle history
│       │   ├── alerts.py             # Summary-drop and search-error warnings
│       │   └── inspect.py            # Link test (no state, no notification)
│       ├── providers/
│       │   ├── base.py               # Provider interface + shared parse helpers
│       │   ├── http.py               # Shared HTTP helpers
│       │   ├── registry.py           # All sites
│       │   ├── amazon/               # client, access (budget), browser, parser, search, reader
│       │   ├── hepsiburada/          # fetching; parsing split into common,
│       │   │                         # prices, variants, search, detail
│       │   └── trendyol.py, network.py, beymenclub.py, bengurme.py,
│       │       nordbron.py, zara.py, hm.py, togg.py, size_availability.py
│       ├── telegram/listener.py      # Channels + Saved Messages quick add
│       └── web/                      # Router, pages, statistics, settings, link test, assets
├── tests/                            # unittest suite (python -m unittest)
└── tools/check.sh, tools/install_rpi.sh
```

## 3. Runtime

- One process. `app.main()` loads the settings, starts the ingress server
  (8099) and the public server (8100) in threads, starts Telegram in a thread
  and runs the monitor loop in the main thread. SIGTERM stops the loop between
  watches, saves state and closes Chromium.
- Invalid settings do not stop the panel. Monitoring stays off, the dashboard
  shows the error with a link to Settings, `/health` answers ok so the
  watchdog does not restart in a loop.
- `/health` fails when the monitor loop has ended or one cycle runs longer
  than three hours; the Supervisor watchdog (`config.yaml`) then restarts Hermes.
- Only the monitor writes `state.json` while it runs. Panel resets are queued
  commands applied between cycles; "Bildirim Sıfırla" then starts a new cycle
  immediately. With the monitor off they apply directly to the file.
- Every JSON write is atomic (unique temporary file, fsync, rename).
- A cycle that reads no watch (nothing due, or every due watch paused by a
  guard) logs nothing; the price table is logged
  when it changes and otherwise at most every 30 minutes.
- `HERMES_DATA_DIR` overrides `/data` only for running Hermes outside Home
  Assistant.

## 4. Persistent data (`/data`)

| File | Purpose |
|---|---|
| `options.json` | Supervisor-managed options; contains secrets |
| `state.json` | per-watch/offer state, price history, suppression, the per-site guard |
| `amazon_access.json` | Amazon's request-window limit, threshold, last block and slow-start end (since 3.3) |
| `amazon_cookies.json` | Amazon's anonymous cookies, so a restart is not a new visitor (since 3.3, mode 600) |
| `latest_price_summary.json` | the published table (complete or merged) |
| `hermes.db` | SQLite (since 3.2): cycles, price points, site reads and requests |
| `cycle_history.json` | cycle durations up to 3.1; read once for the migration, then left as is |
| `status.json`, `error_events.json` | Telegram status and its 24-hour errors |
| `seen_messages.json`, `telegram_quick_add.json`, `login_state.json` | Telegram |
| `telegram_keyword_alert*` | Telegram session |

State keys and fields are unchanged from 2.x (`watch_<site>_<card>_<url>_<size>`,
`watch_offer_…`, `_meta.amazon_protection`, `amazon_no_offer_retry_after`,
`amazon_partial_result`), so 2.5.48 can read a 3.x state and vice versa. Never
delete or reset these files except through the explicit, confirmed panel
actions. Never commit them.

`hermes.db` (`history.py`) uses WAL with one writer per file: a single
connection behind a lock, shared by the site queues; the panel reads through
separate read-only connections. Tables: `cycles` (kept 90 days), `prices`
(a point whenever an offer's price changes; kept, cleared by "Fiyat geçmişini
sıfırla"), `reads` (each watch read: outcome ok, empty, stock, captcha,
http_<status>, timeout, connection, unreadable or error, its duration, and
since 3.4 the watch key and priority; older rows keep these two empty) and `requests` (each Amazon network request with
method and outcome); measurements are kept 30 days. On first start the cycle
durations of `cycle_history.json` and the min/max/last prices in `state.json`
are copied in once (`meta.json_migrated_at`); the JSON files are not changed.
`state.json` keeps min/max, alerts and guards, so older versions still work
after a rollback; they only miss the cycle statistics recorded since 3.2. A
database error is logged once and never stops monitoring.

## 5. Configuration

The only watch model is `takip_edilenler`. Each card has optional `name`
(required for search links), `group`, `target_price`, optional
`minimum_price`, comma-separated `exclude_terms`, `size`,
`include_variations`, `priority` (high: every cycle, medium: ≥ 2 h, low:
≥ 6 h), `official_seller_only`, `url_1` … `url_5`, a legacy
`check_interval_minutes`, `notify_once_in_24H` and `active`. The search limit
is fixed at 60. An unsupported link is skipped without stopping the card.
A card's identity (`tracking_id`) is derived from name, target, size and links
exactly as in 2.x.

A settings save writes the complete option set (unrelated values such as
Pushover keys and the public token are preserved) and restarts Hermes once.
Editing a single card or adding one stamps `check_now_token`, which makes that
card due immediately after the restart.

## 6. Monitoring cycle

1. Load `state.json`; reset every provider's cycle caches.
2. For each watch: if the absence retry time has not come, the priority
   interval has not passed or the provider's own rhythm says it is not due
   (`Provider.read_due`), keep its last rows and skip it. While a site's guard
   is active (Amazon pauses as a whole), its watches keep their last rows too.
3. Every site reads its due watches in its own queue (one thread per site),
   high → medium → low (inside a tier the provider's `read_rank` puts quick
   reads before long ones), with the configured random delay before each of its
   requests. A slow or protected site never delays another; each site has one
   sequential queue, except Amazon (since 3.5): `Provider.has_depo_lane` gives it
   a second thread, the Depo lane, beside its sweep queue. The sweep queue gets
   the watches whose family is due for a sweep (`needs_sweep`); the Depo lane
   looks after the main page of every active watch of the site (`next_read_is_main`:
   any remembered family, however old its sweep is, and every product without
   variants), also those not due when the cycle began, for as long as the sweep
   queue is busy. A watch is read by one lane at a time (`busy`; the sweep queue
   waits while the Depo lane reads its watch). A watch read again in the same
   cycle replaces its own rows on the table (`CycleRun.rows_of`). All changes to the
   cycle's state, table and files are serialized under one lock; network reads
   and notifications happen outside it. On top of the delay, a minimum gap
   between request starts per site (`SITE_MIN_REQUEST_GAP_SECONDS`, Network 1 s)
   keeps that site at or below its old pace. Amazon has no fixed gap since 3.8.3:
   its client waits a random decimal time between the configured minimum and
   maximum (`request_delay_*`, stretched by the start and block slow-downs)
   before every network request (product, variant, listing, search detail);
   cached pages never wait. The monitor gives it the delay (`set_request_delay`)
   and does not wait for it itself. The cycle ends when the slowest queue
   has finished.
4. The provider returns offers (Amazon product families stream them). Apply
   the own-seller filter, the minimum price and exclusions, then record each
   offer: history, summary row, notification.
5. A notification is sent at once; the merged table and the state are saved
   immediately so the dashboard never shows a partial cycle. A failed
   notification is logged and retried on the next read.
6. At the end publish the table, record the cycle duration, run the
   summary-drop and search-error warnings, update the Home Assistant sensors,
   save the state.

Out-of-stock results become stock rows and allow a later notification at the
same price. Errors remove only that watch's stale rows. An explicit "no
results" search notice is a normal stock row read again after five minutes.

## 7. Notifications

- Opportunity: price ≤ target. With `notify_once_in_24H`, the same offer is
  silent for 24 hours unless the price drops; without it, it notifies again
  only after the price was above target in between.
- Ben Gurme stock return: one stock notification when a sold-out product is
  back; it counts as the alert of that read.
- Search errors: at most one per page per day at 11:00, plus an aggregate
  warning for ≥ 4 pages or ≥ 6 links (quiet 22–08, one hour cooldown).
- Summary drop: a meaningful drop for five consecutive cycles (quiet hours and
  cooldown apply).
- CAPTCHA and HTTP 503 never notify, never count for the aggregate warnings,
  and suppress the summary-drop warning while they last. Verified offers read
  before a block still notify.

## 8. Sites

### Amazon (`providers/amazon/`)

- `client.py`: one canonical request (curl with Chrome TLS, or requests). A
  challenge page or HTTP 429/503 is terminal: no retry, no other transport.
  Any other failure gets exactly one Chromium read of the same address.
  Sessions and cookies live for the whole process; pages are cached only
  within a cycle. One consistent browser (since 3.6): curl_cffi's `chrome146`
  profile sets TLS, HTTP/2 and every header in Chrome's order; Hermes only
  overrides the language, the Linux platform and the matching Chrome 146 user
  agent and client hints. No Referer (a bookmark visit, `Sec-Fetch-Site: none`)
  and no forced reload. After a block the client sends nothing for
  `AMAZON_BLOCK_HOLD_SECONDS` (the ladder's first step, 5 minutes since 3.7; a refused request raises a
  `mola` protection error without touching the network), and the first request
  after that closes the old session and starts a new anonymous visitor; cookies
  saved before the last block are not restored after a restart.
- `browser.py`: headless Chromium with the cache disabled, complete page load,
  the main document's own network status, and rejection of cached documents,
  late navigations and redirects to another ASIN.
- Challenge detection looks at validation forms/inputs, a Robot Check title
  or explicit instructions; script text alone is not a challenge.
- Speed governor (since 3.7, `AmazonAccess.speed_factor`): a block wave doubles
  every category's reading interval (x2, x4 on the next wave, never more;
  `AMAZON_SLOWDOWN_MAX`), and every clean `AMAZON_SLOWDOWN_RECOVER_SECONDS`
  (10 minutes) halves it again until the normal speed is back; nothing needs
  to switch it. The provider asks it for every interval (`main_interval`,
  `sweep_interval`). The factor and its clock survive restarts in
  `amazon_access.json` (schema 3; an older file is ignored). The old hour of
  half speed after a block is gone: speed is the goal, so only the governor and
  the pause slow Amazon down.
- Protection back-off (since 3.3; every block is site-wide since 3.6): the
  2026-10-04 log showed that a block marks the visitor, not the page (43 of
  the 60 requests right after a block were blocked too), so the first block
  pauses every Amazon watch (5 → 10 → 20 → 30 minutes since 3.7,
  `PROTECTION_PAUSE_LADDER_SECONDS`); the first read after the pause is the
  single probe. A failed probe climbs one step; a successful read after the
  pause clears the guard. During the pause neither a further block (the other
  lane's read that was under way) nor a success of a read that began before it
  changes the guard. The pause survives restarts (`state.json`). A blocked
  watch keeps its last rows on the table (they carry their read time). Guards
  of older versions (one per watch) are dropped at the first cycle.
- Request budget (`access.py`, since 3.3): a rolling 35-minute window caps how
  many requests may start. The limit begins at 400 (300 before 3.7) and rises 5 % after every
  clean hour in which the window reached 80 % of it (never above 500). The first
  site-wide block records the window count as the threshold and lowers the limit
  once to 85 % of it (floor 200); later blocks only record the threshold (the
  3.3.0 night showed blocks do not follow the request count). The main-page lane
  (Depo reads) may exceed the limit by 20 %. After a site-wide block the minimum
  request gap doubles for an hour, and for ten minutes after every start. Limit,
  threshold and slow start survive restarts in `amazon_access.json`; since
  3.5.1 the window itself and the last hour's requests and blocks are rebuilt
  at start from the `requests` table of `hermes.db` (`AmazonAccess.restore`;
  the Depo lane's share starts unused) (schema 2; a
  3.3.0 file is ignored and the limit starts at 300); cookies in
  `amazon_cookies.json`. Every ten minutes (checked at every fetch
  since 3.5.1, so also during a long sweep) the log gets one `Amazon ölçüm:`
  line (window, limit, threshold, requests/min, last-hour requests and blocks,
  the day's block waves, Depo checks and verified offers, skipped excluded
  pages). Each block wave (the first block after a success) gets one
  `Amazon engel dalgası:` line with its number of the day, its cause (challenge
  marker and/or HTTP status), the page, the requests of the hour before it and
  the calm time since the previous block; the day's count survives restarts.
- One read per watch and round (since 3.9, `WatchRhythm`): a product watch is
  read as a whole, its configured page with the used listing (where Depo offers
  show) and every variant of its family. A red watch is read once per search
  round (a cycle; `AmazonProvider.read_due` checks `main_cycle`, floor
  `AMAZON_RED_ROUND_FLOOR_SECONDS`, 20 s), a yellow one hourly and a green one
  every 3 hours (`AMAZON_PRIORITY_INTERVAL_SECONDS`); the price never changes
  it. The round lasts as long as its reads and grows when yellow or green cards
  are due in it, so each red page is read every ~5-6 minutes at the 1-4 s delay
  (the old fast main-page loop of 3.5-3.8.3 and the replay of remembered variant
  offers are gone: the main pages took half the requests and the variants went
  stale for 14 minutes). The work runs in two lanes (since 3.5): the Depo lane
  thread reads the single-page products (once per round), the sweep thread
  works through the variant families and search pages. Both go through one
  request turn (one request at a time; since 3.8.3 the two lanes alternate when
  both wait), one rolling window (the Depo lane may use all of it; the sweep
  all but the part of the Depo reserve not used yet, the reserve being what the
  Depo lane used in the last 35 minutes, at least 12 requests, at most 28 % of
  the limit; and the sweep steps aside while the Depo lane waits for a slot) and one pause: a block on either lane
  stops both. The Depo lane's reads use caches of their own, so they never get a
  page the sweep fetched earlier in the same cycle. Between sweeps the other variants' offers are replayed
  from memory with `OfferResult.checked_at` (the time they were really read);
  the cycle shows them but adds no price point and no alert for them. Search
  watches follow the sweep interval.
- Product families (`include_variations`, at most 60 ASINs) follow the real
  Twister edges of every fetched page. Exclusions are applied to the title and
  selected variant before any price is read; discovery continues. A page a
  watch excludes by title is read once for its neighbours; the neighbours are
  then kept for 15–45 minutes (spread per page) and the page itself is not
  requested again (`client.excluded_pages`; the watch's exclusion terms are
  part of the key). A first page lists only one dimension's edges, so the page
  cannot be skipped before it was read once. A verified Amazon Depo offer is
  yielded before the next variant is read.
- An explicitly unavailable or unpriced variant is not requested again for
  five minutes (process-wide, at most 512 entries); prices are never cached.
- Search pages stop at "All Departments / Tüm Kategoriler içindeki sonuçlar",
  match the card name as a phrase, give overlapping models to the most specific
  card, and inspect matching product pages for verified Depo offers.
- DEPO requires second-hand evidence and the Amazon Depo seller together. New
  and Depo offers of one ASIN are separate rows. "Stokta sadece N adet kaldı"
  adds `(Stok N)`.
- `official_seller_only` keeps only `Amazon.com.tr` new offers; verified Depo
  offers are always kept.

### Others

- Hepsiburada: product, variant and search pages; payable campaign prices
  (`Sepete özel`, Premium) win; each variant keeps its own price and label;
  search cards get their variant label from the product page.
- Trendyol, Nordbron: product price.
- Network, Beymen Club: `Sepette` / `2 ve üzeri` campaign prices win; requested
  sizes are case-insensitive; a missing size is a stock state.
- Zara, H&M: colors and the requested size; a missing size is a stock state.
- Ben Gurme: Shopify JSON; every weight variant is a row; stock-return notice.
- Togg: the configurator's public model list (one request per cycle for all Togg cards); the card `name` is the model, matched as a phrase. A model in the list is in stock, a missing one is a stock state; stock-return notice.

## 9. Home Assistant entities

With `homeassistant_api: true` Hermes writes through the Supervisor proxy:

- `sensor.hermes_firsat_sayisi`: number of rows at or below target; attribute
  `firsatlar` (site, urun, fiyat, hedef, fark, depo, url; at most 25, biggest
  saving first).
- `sensor.hermes_son_tur`: end of the last cycle (timestamp); attributes
  `sure_saniye`, `tarama_saniye`, `urun_sayisi`, `stokta_olmayan`.
- `sensor.hermes_hata_sayisi`: watches whose last read failed in 24 hours;
  attribute `hatalar` (site, takip, hata).
- Event `hermes_firsat` after every delivered opportunity notification with
  `site`, `takip`, `urun`, `fiyat`, `fiyat_metni`, `hedef`, `fark`, `depo`,
  `satici`, `url`.

These entities are not stored by Home Assistant itself; Hermes rewrites them
after every cycle, so they reappear after a Home Assistant restart at the end
of the next cycle. Home Assistant being unreachable never affects monitoring.

## 10. Panel

One router serves both surfaces. Ingress pages use relative links ("." or
".."), public pages `/public/<token>`; the token needs at least 24
characters, `public_dashboard_enabled`, and is compared in constant time.
Pages: summary (`/`), `statistics`, `link-test`, `settings`, `restarting`;
actions: `test-pushover`, `reset-notifications`, `reset-price-history`,
`settings/save`, `link-test`. Every page has the same navigation. The
statistics page (`web/statistics.py`, since 3.4) is built from the `reads`
table around one number, the check frequency: the median time between two
reads of the same high-priority watch (gaps over 6 hours are pauses, not the
rhythm). One switch, `statistics?p=24h|7d`, drives the whole page: four tiles
(check frequency, last cycle from the published summary, success rate,
blocks and errors), a bar chart of the check frequency per hour or day with
that slot's block/error count above the bar, one card per site (health bar
ok/blocked/error, check frequency, typical read time, blocks, errors, last
read, Amazon network requests), a table of block and error types per site
with when each was last seen, and a collapsed 7-day daily history. Cycles
that read no watch are not recorded (rows from before 3.2.2 were removed once,
`meta.idle_cycles_dropped_at`); the `cycles` table is kept but not shown.

The summary and statistics pages update in place: `live.js` fetches
`live/dashboard` (every 15 seconds) or `live/statistics?p=…` (every minute;
anchored to the minute) as JSON with the region's HTML while the
tab is visible and swaps only the live region, keeping open
groups (`details[data-key]`) and the scroll position. The browser sends the
version it shows (`v`); an unchanged block is answered with `{"same": true}`.
Text responses of 1 KB or more are gzip-compressed when accepted. Tools, notices and forms
stay outside the live region. Without JavaScript the page falls back to a
full reload every 60 seconds (`<noscript>` refresh).

Visual rules: dark charcoal/gray, high-contrast text, muted provider accents,
whole-lira prices (`1.500 TL`), product names 60 characters without ellipsis,
group titles 70 characters, full text in the tooltip, priority dots on normal
rows only, DEPO tag on warehouse rows, compact mobile cards, correct Turkish.

## 11. Telegram

Channel messages matching a keyword (and no exclusion) are relayed with their
link. In Saved Messages a supported link (short links are resolved) starts a
quick add: Hermes asks for the target price and adds a card to the
`Paylaşılanlar` group. Messages are handled once across restarts. A dropped
connection is re-established after 60 seconds; a login waiting for a code or
2FA stops until the settings are saved again.

## 12. Testing and release

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt -r ha-addon/app/requirements.txt
sh tools/check.sh
```

`tools/check.sh` runs `pip check`, compiles, lints with ruff and runs the
`tests/` suite. CI repeats it and builds the image. Release: bump the version
in `config.yaml` and `constants.APP_VERSION`, update `CHANGELOG.md`, push to
`main`, then `sh tools/install_rpi.sh` and verify the running version.
