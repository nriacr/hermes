# Hermes Cursor Handoff

This document is the current technical and product handoff for continuing Hermes
in Cursor. It describes the real repository at version `2.5.5`; source code and
tests remain authoritative when this document and code ever differ.

## 1. Product snapshot

Hermes is a Home Assistant add-on running continuously on a Raspberry Pi. It:

- checks product and search-page URLs across multiple Turkish commerce sites;
- detects each URL's site and product/search type automatically;
- evaluates prices, product variants, requested clothing sizes, and stock state;
- sends opportunity and operational notifications through Pushover;
- monitors configured Telegram channels and Saved Messages;
- exposes the same management dashboard through Home Assistant ingress and a
  token-protected public/mobile web route;
- preserves notification suppression state and lifetime min/max prices across
  restarts.

The user manages watches from Hermes Settings, views current opportunities,
above-target products, unavailable items, errors, and recent Telegram matches,
and can test links without adding them to the real table.

## 2. Repository map

```text
.
├── AGENTS.md                       # Binding agent/development contract
├── .cursor/rules/hermes.mdc         # Cursor always-on project rules
├── docs/CURSOR_HANDOFF.md            # This handoff
├── docs/CURSOR_START_PROMPT.md       # First-chat prompt
├── repository.yaml                   # Home Assistant repository metadata
├── ha-addon/
│   ├── config.yaml                   # Add-on metadata, options/schema, ports
│   ├── Dockerfile                    # Python 3.12 + Chromium image
│   ├── run.sh                        # Starts ingress UI, public UI, service
│   └── app/
│       ├── main.py                   # Minimal entry point
│       ├── requirements.txt
│       └── hermes/
│           ├── service.py            # Scheduling and business flow
│           ├── config_loader.py      # Supervisor option normalization
│           ├── models.py             # Domain models
│           ├── http_client.py        # HTTP/session/retry/anti-bot flow
│           ├── storage.py            # Atomic persistent JSON state
│           ├── notifier.py           # Pushover transport
│           ├── telegram_listener.py  # Telegram monitoring/quick-add
│           ├── dashboard.py           # Shared dashboard rendering/styles
│           ├── settings_ui.py         # Shared settings rendering/actions
│           ├── dashboard_with_settings.py # Ingress HTTP server
│           ├── public_dashboard.py   # Token-protected public server
│           ├── link_test_ui.py       # Temporary link test
│           ├── web_assets.py         # Manifest/icons/assets
│           ├── search_amazon.py      # Amazon search helpers
│           └── providers/             # One isolated module per site
├── tools/check.sh                    # Complete local quality gate
├── tools/hermes_smoke_test.py        # Regression/smoke tests
└── .github/workflows/quality.yml     # Tests and Docker build
```

## 3. Runtime and Home Assistant contract

- Base runtime: Python 3.12 slim.
- Browser fallback: Chromium installed in the add-on image.
- Libraries: Requests, BeautifulSoup, `curl_cffi`, Telethon.
- Ingress: enabled on internal port `8099`.
- Public dashboard: host port `8100`, protected by a configured token path.
- Supervisor access: `hassio_api: true`, `hassio_role: manager`.
- Health endpoint: `/health`.
- `/app` contains application code; `/data` contains persistent data.
- `run.sh` starts ingress and public servers in the background, then runs the
  monitor service in the foreground so Supervisor manages its lifecycle.

Do not change ports, slug, Supervisor permissions, routes, or data paths without
a migration plan and explicit approval.

## 4. Persistent data

Current files under `/data`:

| File | Purpose |
|---|---|
| `options.json` | Supervisor-managed options; contains secrets |
| `state.json` | notification, price history, and availability state |
| `latest_price_summary.json` | last complete/merged dashboard table |
| `status.json` | service health and cycle timing |
| `error_events.json` | recent operational errors shown by UI |
| `login_state.json` | provider/browser login-related state when used |
| `seen_messages.json` | Telegram deduplication |
| `telegram_quick_add.json` | Saved Messages quick-add conversation |
| `telegram_keyword_alert/` | Telegram session/supporting state |

Rules:

- Never commit these files or their contents.
- Use atomic writes from `storage.py`.
- Preserve unrelated settings when saving through the custom UI.
- A broken watch or unsupported URL is isolated; it must not prevent startup.
- Min/max and notification history are deleted only through their separate,
  confirmed user actions.

## 5. Configuration

Top-level options currently include:

- `interval_seconds`: delay between complete monitoring cycles.
- `request_delay_min_seconds`, `request_delay_max_seconds`: randomized request
  spacing; provider order is balanced to avoid one site consecutively.
- `pushover_user_key`, `pushover_api_token`.
- `public_dashboard_enabled`, `public_dashboard_token`.
- Telegram enable flags, credentials, session, channels, keywords, and excludes.
- `gruplar`: user-defined organization/filter groups.
- `takip_edilenler`: the only current product/watch model.

Each `takip_edilenler` card supports:

- optional `name` (required for search pages, optional for product pages);
- optional `group`;
- required `target_price`;
- optional `minimum_price` lower bound;
- optional comma-separated `exclude_terms`;
- optional `size` (case-insensitive, provider-normalized);
- `include_variations`;
- up to five mixed URLs (`url_1` ... `url_5`);
- optional per-watch interval override;
- `notify_once_in_24H` and `active` flags.

`max_items_to_scan` is deprecated compatibility input; the effective search cap
is fixed at 60 and should not be restored as a user-facing setting.

## 6. Core monitoring flow

1. Load and normalize Supervisor options.
2. Expand each watch card into provider-specific checks while retaining a shared
   stable `tracking_id`.
3. Skip inactive or not-yet-due per-watch checks.
4. Balance checks so different providers alternate as much as possible.
5. Wait the configured randomized delay and log provider plus watch name.
6. Provider returns one or more results with explicit URL, title, price, stock,
   warehouse state, and variant identity.
7. Apply phrase match, excludes, minimum price, requested size, and target.
8. Update persistent availability and price history.
9. Notify immediately when eligible. Merge changed rows into the last full
   summary so the dashboard never collapses to a partial cycle.
10. At cycle end publish a complete summary, update timing, and schedule next run.

The dashboard separates target-price opportunities, above-target products,
positively identified unavailable products, operational errors, and recent
Telegram alerts.

Rows are provider-colored. Above-target multi-result/variant watches collapse
under the configured watch name. Rows are grouped by provider and ordered by
price difference ascending. Duplicate canonical URLs are removed; normal and
verified warehouse offers remain separate identities.

## 7. Matching and filtering semantics

- A search watch name is an ordered, contiguous phrase requirement after text
  normalization. Do not fall back to independent token containment.
- Example: `Apple iPhone 17 Pro` must not match `Apple iPhone 17 Pro Max` merely
  because all shorter tokens occur. Prefer the most specific matching watch when
  configured phrases overlap.
- `exclude_terms` is comma-separated; any one matching term excludes the result.
- `minimum_price` removes suspiciously low/unrelated results from table and
  notifications.
- Site recommendation/fallback sections are outside search scope.
- Empty legitimate search results and requested-size absence are expected states,
  not errors.

## 8. Notification semantics

- Pushover carries opportunities, operational error summaries, summary-count
  anomaly warnings, tests, and Telegram keyword relays.
- `notify_once_in_24H` suppresses an unchanged repeat for 24 hours.
- A lower price can notify before 24 hours expires.
- A product that genuinely disappears and later returns can notify again at the
  same qualifying price.
- Manual notification reset makes qualifying suppressed opportunities eligible
  once, then normal suppression resumes.
- Search-page empty inventory should not generate repetitive operational errors.
- A significant summary-count drop warns only after repeated cycles and respects
  cooldown and quiet-hour rules.

## 9. Provider behavior

### Amazon

- Supports product and search URLs with isolated rescue diagnostics.
- Search names use phrase matching; recommendation sections are cut.
- Parsing stops before `All Departments içindeki sonuçlar gösteriliyor`.
- Product variation expansion is opt-in with `include_variations`.
- A product may yield normal and genuine used Amazon Warehouse offers as separate
  rows and prices.
- `DEPO` requires positive second-hand text/offer evidence and Amazon Warehouse
  or `Amazon Depo` seller evidence. URL type, low price, or third-party seller is
  never enough.
- `Stokta sadece N adet kaldı` adds `(Stok N)`; plain `stokta var` does not.
- Never replace a normal price with another seller, installment, trade-in,
  crossed-out, or warehouse price.

### Hepsiburada

- Supports product and search URLs with direct result links.
- Uses the lowest valid seller offer and retains the seller name.
- Explicit payable campaign prices such as `Sepete özel fiyat` and visible
  Premium prices take precedence over ordinary prices.
- Variant combinations are separate products. Variant labels contain values,
  not field names. Never copy a sibling's cheapest price.
- Reviews, recommendations, and attribute labels are not sellers/prices.

### Trendyol

- Product-price provider with isolated site-specific parsing.

### Network

- Reads current price and explicit `Sepette`, `2 ve üzeri`, `3 ve üzeri`
  campaign prices; the visible payable campaign price wins.
- Requested sizes are case-insensitive; absence is stock state, not an error.

### Beymen Club

- Independent provider with Network-like campaign semantics without parser
  coupling.
- Supports campaign prices and requested-size stock.
- DNS/403 is operational failure; ordinary size absence is not.

### Nordbron

- Reads title and price with isolated bot-protection detection.
- CAPTCHA/access failures are errors and must not leave a stale current price.

### Zara

- Expands color variants and checks the requested normalized size.
- Size matching is case-insensitive; parenthetical regional labels are ignored.
- Out-of-stock requested size is a normal stock row.

### H&M

- Expands colors and checks normalized size through its own data path.
- Access/browser failure is an error; size absence is not.

### Ben Gurme

- Shopify-style provider.
- Every weight/package variant has its own price and stock row.
- `Tükendi`/availability drives stock and restock notifications.

## 10. Telegram

- Channel mode matches any configured keyword unless an exclude matches. It
  forwards the original post/link and does not parse Telegram prices.
- Saved Messages resolves short URLs, asks for numeric target price, and creates
  a watch in `Paylaşılanlar` for later editing.
- Deduplication and suppression state persist across restarts.

## 11. UI/UX criteria

Ingress, public desktop, and public mobile must have functional parity. Only
responsive layout may differ; use shared renderers and actions.

Visual contract:

- charcoal/dark gray backgrounds and panels;
- high-contrast light typography, no blue-dominant theme;
- muted but clearly distinct provider accents;
- compact mobile cards without dropping seller, product, current, target,
  difference, min/max, stock, or links;
- whole-lira display (`1.500 TL`), correct Turkish grammar and characters;
- confirmation for destructive actions and clear save/restart progress;
- working settings search/group filters;
- temporary link-test data never enters real state or summary.

Provider colors live in `dashboard.py`. Re-evaluate all colors when adding a new
provider so adjacent providers remain distinguishable.

## 12. Public security

- Public access is explicit and uses `/public/<token>`.
- Never log or embed the token in docs, screenshots, or source.
- Cloudflare Tunnel is deployment infrastructure, not a reason to weaken token
  protection.
- Never expose Supervisor APIs directly; public actions are mediated by Hermes.

## 13. Logging and diagnostics

- Logs use `[YYYY-MM-DD HH:MM:SS]` in Istanbul local time.
- Every cycle starts with a visually distinct banner containing actual version
  and interval.
- Request lines identify provider and watch.
- Errors identify context, failed URL, and actionable reason.
- Amazon diagnostics retain method/status/reason without secrets.
- Avoid successful internal parser noise; keep concise success and rich failure.

## 14. Testing and release

Initial local setup:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pip install -r ha-addon/app/requirements.txt
```

Focused iteration:

```bash
.venv/bin/python tools/hermes_smoke_test.py
```

Complete required gate:

```bash
sh tools/check.sh
```

CI additionally builds the Docker image. Before runtime release:

1. inspect `git status` and current `ha-addon/config.yaml` version;
2. add or update regression tests;
3. run focused checks, then `sh tools/check.sh`;
4. increment the actual patch version;
5. update docs for behavior/options/provider changes;
6. inspect diff for secrets and unrelated edits;
7. commit and push to `main`;
8. confirm Actions and Home Assistant update availability.

Documentation-only commits do not require a version bump.

## 15. High-risk areas

- Amazon normal/warehouse identity and price pairing.
- Search grouping across multiple URLs sharing one watch.
- Phrase specificity when one product name prefixes another.
- Hepsiburada campaign price versus discount/installment amounts.
- Variant-specific URLs/prices and duplicate canonicalization.
- Settings save preservation and Supervisor schema requirements.
- Incremental summary merge during an unfinished cycle.
- Expected out-of-stock versus operational failure.
- Public token handling and ingress/public action parity.

Always add regression coverage before changing these areas.

## 16. Anti-patterns

- One generic price regex for all sites.
- Choosing the smallest number on a page.
- Treating URL type or low price as warehouse proof.
- Copying one variant result to every sibling.
- Keeping stale current rows after a failed read.
- Saving a partial settings payload to Supervisor.
- Separate ingress and public implementations.
- Masking failures with cooldown instead of diagnosing them.
- Leaving obsolete functions after replacing a design.
- Guessing the version from conversation history.
