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
│       ├── notifier.py               # Pushover
│       ├── models.py, errors.py, utils.py, constants.py
│       ├── monitor/
│       │   ├── cycle.py              # One cycle: read, record, notify, publish
│       │   ├── runner.py             # Loop, panel commands, health
│       │   ├── scheduling.py         # Due checks, priority and site order
│       │   ├── state.py              # state.json entries, history, guards
│       │   ├── summary.py            # latest_price_summary.json, cycle history
│       │   ├── alerts.py             # Summary-drop and search-error warnings
│       │   └── inspect.py            # Link test (no state, no notification)
│       ├── providers/
│       │   ├── base.py               # Provider interface + shared parse helpers
│       │   ├── http.py               # Shared HTTP helpers
│       │   ├── registry.py           # All sites
│       │   ├── amazon/               # client, browser, parser, search, reader
│       │   ├── hepsiburada/          # parser + fetching/variants
│       │   └── trendyol.py, network.py, beymenclub.py, bengurme.py,
│       │       nordbron.py, zara.py, hm.py, size_availability.py
│       ├── telegram/listener.py      # Channels + Saved Messages quick add
│       └── web/                      # Router, pages, settings, link test, assets
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
- `HERMES_DATA_DIR` overrides `/data` only for running Hermes outside Home
  Assistant.

## 4. Persistent data (`/data`)

| File | Purpose |
|---|---|
| `options.json` | Supervisor-managed options; contains secrets |
| `state.json` | per-watch/offer state, price history, suppression, guards |
| `latest_price_summary.json` | the published table (complete or merged) |
| `cycle_history.json` | completed cycle durations of the last seven days |
| `status.json`, `error_events.json` | Telegram status and its 24-hour errors |
| `seen_messages.json`, `telegram_quick_add.json`, `login_state.json` | Telegram |
| `telegram_keyword_alert*` | Telegram session |

State keys and fields are unchanged from 2.x (`watch_<site>_<card>_<url>_<size>`,
`watch_offer_…`, `_meta.amazon_protection`, `amazon_no_offer_retry_after`,
`amazon_partial_result`), so 2.5.48 can read a 3.x state and vice versa. Never
delete or reset these files except through the explicit, confirmed panel
actions. Never commit them.

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
2. For each watch: if a guard is active, the absence retry time has not come,
   or the priority interval has not passed, keep its last rows (not for a
   guarded watch without partial results) and skip it.
3. Read due watches high → medium → low, alternating sites inside a tier, with
   the configured random delay before each request.
4. The provider returns offers (Amazon product families stream them). Apply
   the own-seller filter, the minimum price and exclusions, then record each
   offer: history, summary row, notification.
5. A notification is sent at once; the merged table and the state are saved
   immediately so the dashboard never shows a partial cycle. A failed
   notification is logged and retried on the next read.
6. At the end publish the table, record the cycle duration, run the
   summary-drop and search-error warnings, save the state.

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
  challenge page or HTTP 429/503 is terminal: no retry, no cookie reset, no
  other transport. Any other failure gets exactly one Chromium read of the
  same address. Sessions and cookies live for the whole process; pages are
  cached only within a cycle.
- `browser.py`: headless Chromium with the cache disabled, complete page load,
  the main document's own network status, and rejection of cached documents,
  late navigations and redirects to another ASIN.
- Challenge detection looks at validation forms/inputs, a Robot Check title
  or explicit instructions; script text alone is not a challenge.
- Protection back-off: the affected watch pauses 15 → 30 → 60 minutes; the
  pause survives restarts, ends with a successful read, and an expired pause
  is probed once before the normal priority schedule applies again. Partial
  family results stay visible while paused.
- Product families (`include_variations`, at most 60 ASINs) follow the real
  Twister edges of every fetched page. Exclusions are applied to the title and
  selected variant before any price is read; discovery continues. A verified
  Amazon Depo offer is yielded before the next variant is read.
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

## 9. Panel

One router serves both surfaces. Ingress pages use relative links ("." or
".."), public pages `/public/<token>`; the token needs at least 24
characters, `public_dashboard_enabled`, and is compared in constant time.
Pages: summary (`/`), `statistics`, `link-test`, `settings`, `restarting`;
actions: `test-pushover`, `reset-notifications`, `reset-price-history`,
`settings/save`, `link-test`. Every page has the same navigation.

Visual rules: dark charcoal/gray, high-contrast text, muted provider accents,
whole-lira prices (`1.500 TL`), product names 60 characters without ellipsis,
group titles 70 characters, full text in the tooltip, priority dots on normal
rows only, DEPO tag on warehouse rows, compact mobile cards, correct Turkish.

## 10. Telegram

Channel messages matching a keyword (and no exclusion) are relayed with their
link. In Saved Messages a supported link (short links are resolved) starts a
quick add: Hermes asks for the target price and adds a card to the
`Paylaşılanlar` group. Messages are handled once across restarts. A dropped
connection is re-established after 60 seconds; a login waiting for a code or
2FA stops until the settings are saved again.

## 11. Testing and release

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt -r ha-addon/app/requirements.txt
sh tools/check.sh
```

`tools/check.sh` runs `pip check`, compiles, lints with ruff and runs the
`tests/` suite. CI repeats it and builds the image. Release: bump the version
in `config.yaml` and `constants.APP_VERSION`, update `CHANGELOG.md`, push to
`main`, then `sh tools/install_rpi.sh` and verify the running version.
