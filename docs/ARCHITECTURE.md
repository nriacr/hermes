# Hermes architecture (4.0)

The owner approved this migration on 2026-10-09. `AGENTS.md` is the development contract.

## Responsibilities

| Module | Responsibility |
|---|---|
| `app.py` | One process, panel/Telegram lifecycle, runtime health and safe startup |
| `monitor/runner.py` | Independent site deadlines, bounded round recovery, command barrier |
| `monitor/cycle.py` | One site round, streamed provider reads, measurement and HA publication |
| `monitor/results.py` | Validated price/filter/stock evaluation and incremental publication |
| `monitor/scheduling.py`, `state.py`, `summary.py`, `alerts.py` | Scheduling, suppression/history rules, derived panel, warnings |
| `providers/<site>` | Site-owned fetching, parsing, stock, variants and pricing semantics |
| `database.py`, `history.py`, `storage.py` | One locked SQLite writer, migrations/history, compatible JSON export |
| `delivery.py`, `notifier.py` | Durable single-consumer outbox, bounded retry, actual Pushover transport |
| `diagnostics.py` | Jobs, incidents, recovery, bounded retention and secret-free numerical health |
| `web/` | One router/render model for ingress, desktop and mobile; `static/` and `templates/` assets |
| `telegram/listener.py` | Telegram transport, keyword selection and Saved Messages quick add |
| `homeassistant.py`, `supervisor.py` | Existing sensors/event, persistent warnings, options and restart |

## Runtime and scheduling

The main thread coordinates a sequential queue for each active site. Completion starts that site's interval;
other sites do not wait for it. Existing provider priority and due rules are authoritative. The finite
`run_cycle()` diagnostic uses the same round implementation; it is not a second scheduler.
Amazon's own two lanes, serialization, budgets, visitor handling, variant parsing and rhythm remain intact.
Long variant streams commit and publish each validated opportunity as it arrives.

One shared monitor RLock protects changes to state, summary and delivery suppression. A Database RLock
serializes short transactions. Never acquire the monitor lock while holding the Database lock. Pushover/network
calls run outside both. Panel resets wait for all current readers to finish, then run under the monitor lock;
a new notification generation makes old queued opportunities invalid. A reset never clears price history unless
it is specifically the min/max action.

Round exceptions affect that site's deadline only (bounded delay, up to 300 seconds). A normal stock/empty result
is distinct from operational failure. Each ordinary provider has a progress deadline; Amazon retains a three-hour
allowance for its existing budget waits. Requests carry finite transport timeouts. Python threads cannot be
forcibly terminated safely: when progress is lost, Supervisor's `/health` watchdog restarts the whole process.
Shutdown uses one 60-second budget shared by readers and cleanup beneath Supervisor’s 90-second timeout.
Readers check cancellation before new requests; completed observations remain durable. Unsent outbox jobs are
left for the next startup rather than drained during shutdown. Active writers are never closed underneath.
Separate reader processes and an autonomous code-writing AI are not part of this architecture.

## Durable data and migration

`/data/hermes.db` is authoritative for state/summary snapshots, observations, jobs, incidents and notification intent.
All writers share one connection and WAL with `synchronous=FULL`. A validated changed price, updated offer state
and its outbox intent commit together. A failed transaction rolls back the pending marker; an uncommitted
opportunity cannot be considered notified. Suppression and outbox acknowledgement also commit together.
JSON exports use fsync plus atomic rename and keep the existing `/data` interface for compatibility and rollback.
Options/Telegram credentials and sessions stay in their existing managed files, outside SQLite diagnostics.

On the first 4.0 start, existing state keys/min/max/suppression and old SQLite price points are preserved.
The new snapshot becomes authoritative; the migration records numerical counts once. Missing legacy JSON is
allowed; malformed critical JSON without a valid SQLite snapshot is an explicit startup failure and is never
silently replaced. With a valid authoritative snapshot, a corrupt JSON export can be regenerated.
A database failure preserves files and leaves the panel process available with failing monitor health.

Old valid card IDs are reused as the initial `id` field and saved through Supervisor without a restart.
New cards get UUIDs. Settings edits carry the existing ID; product URL still forms the separate offer identity,
so changing to a different product does not merge unrelated product histories. Unsupported links are skipped
individually. Startup skips a malformed card and reports it; UI save validation is strict.

The existing ports (8099/8100), slug, token-protected public paths, HA sensors/event and options remain.
`/runtime` is available through ingress or an authenticated public path and returns counts/timing only.
`/health` remains a minimal liveness/progress gate. No raw options, messages, URLs or credentials are returned
by runtime diagnostics.

## Delivery and recovery

One durable consumer serves price, stock, Telegram and warning messages. Telegram marks a message seen only
after the corresponding intent is persisted; its message ID deduplicates reprocessing across restarts.
Price delivery requires the same current price, no read error, a matching reset generation, and an observation
at most five minutes old. Stale entries expire; a fresh read can recreate an expired intent. Stock alerts share
freshness/identity checks but can be above the price target. Telegram/warning entries expire after 24 hours.

A successful HTTP and Pushover `status=1` mean the service accepted a message. They do not prove phone display.
Transient failures receive at most six attempts with increasing delays. Definite API/4xx rejection stops;
the existing successful Pushover-test action can retry failed jobs after the user repairs the configuration.
Failures stay visible until the failed jobs are resolved. An accepted request whose response is lost can still
produce a duplicate on retry; the API has no transactional exactly-once guarantee with the local database.

Critical delivery rejection/repeated round failure is recorded once per incident episode and communicated through
Home Assistant persistent notifications, independent of Pushover. Resolved incidents are closed. The independent
HA automation in `tools/homeassistant/hermes_availability_watch.json` watches Supervisor's running sensor and
warns if Hermes is off/unavailable for two minutes; recovery clears the same notification. Thus a completely
stopped Hermes need not notify itself. It complements, rather than replaces, Supervisor's restart watchdog.

## Evidence and retention

Each job records a durable ID, version, watch/site, planned/actual start, scheduling delay, result, total time,
request count/time, persistence time, provider/wait remainder, non-secret configuration fingerprint and offer
source/currency/condition/read time. Request rows link to the job. The provider/wait remainder is not claimed
as pure parsing time. Partial variant errors are retained even when other variants succeed. Known secrets are
redacted before diagnostic persistence. No authenticated raw page or full response headers are captured.

Completed jobs, request/read measurements, resolved incidents and terminal outbox rows retain 30 days;
cycle history retains 90 days; user price points/min/max are not automatically deleted. Open incidents and
pending notification intent are retained until resolved/processed. Hata Sıfırla clears the user's statistical
error view; operational incidents remain until the underlying issue is resolved.

The numerical endpoint exposes preserved migration counts, jobs/reads, outbox states, observation age per site,
and p50/p95 measured scheduling/delivery delays. Delivery delay starts at local intent persistence, not at the
unknown instant the commerce site changed its price. System load is correctly labeled as load, not actual CPU
utilization. Actual Pi CPU/memory is measured externally through Supervisor during release verification.

## Product validation and panel

Providers keep their own positive product/variant identity, seller, condition and stock rules. Product-scoped
helpers reject recommendation/related/cross-sell sections, match JSON-LD to the current product, and require
an explicit payable price. Broad minimum-of-any-script fallbacks were removed from ordinary providers.
Ambiguous prices are errors, not guessed opportunities or asserted missing stock. Site campaign rules stay
with their provider. A genuine steep price drop is not rejected merely because it is a good opportunity.

One responsive panel uses the same router, actions, data and shared styling for public/ingress/mobile.
Static CSS/JS and the HTML shell are separate files. Appearance is preserved; unused old style classes were
removed. Form bodies/timeouts are bounded. Destructive actions retain confirmation. No separate mobile UI,
extra frontend service or external font request is required.

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
  at start from the bounded `request_window` table of `hermes.db` (`AmazonAccess.restore`;
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
  show) and every variant of its family. An every-cycle (red) watch is read
  once per search round (`AmazonProvider.read_due` checks `main_cycle`, floor
  `AMAZON_RED_ROUND_FLOOR_SECONDS`, 20 s), the others every 30 min, 60 min,
  3 h or 6 h (`AMAZON_PRIORITY_INTERVAL_SECONDS`); the price never changes
  it. The round lasts as long as its reads and grows when slower cards
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
- By owner policy, `Normalden yüksek fiyat` in the selected product's purchase
  area is treated as unavailable before reading any price or used-offer listing.
  It creates no read error; sibling discovery continues and normal scheduled
  probes resume price monitoring after the warning disappears. Hidden/script
  text and unrelated recommendation/review sections do not trigger this rule.
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


## Release gate

Run `tools/check.sh` with Python 3.12. Existing provider regressions and 4.0 fault/migration/concurrency tests
must pass. Inspect the diff for unused paths, secrets and unrelated files. The existing workflow builds the container and runs the tests. Runtime startup is checked locally and on the Pi. Validate real desktop/mobile routes and client interactions.

After committing/pushing main, `tools/install_rpi.sh` verifies a full backup containing Hermes, reruns checks,
validates HA configuration, refreshes the repository, installs the exact version, and checks both `/health`
and a completed 4.0 read through `/runtime`. Record live memory/CPU, migration counts, job progress and queue
states. Use `sh tools/rollback_rpi.sh BACKUP_SLUG` for a single-command Hermes-only restoration; the rest of HA
is not restored. The backup contains the pre-migration code and data together, avoiding mixed-version recovery.
Do not declare speed or reliability gains from one short observation window.

Home Assistant's native daily full backup owns the schedule. Updates reuse the newest full backup
(or the explicitly supplied backup) and verify that it contains Hermes; they do not create a new backup.
Only an explicit owner request creates an extra backup. A restoration returns code and data to that
backup's time, so subsequent changes are lost. Do not add a Codex backup schedule or monitoring task.
HA's native backup API labels automatic backups `partial` even when every component is selected.
`tools/backup_rpi.py` checks their contents: HA, Hermes, share, SSL and media must be included.
The native daily plan includes every app, the database and all four optional folders. The restore
tool supports the existing encrypted daily backups, keeping the configured key in memory only.

## Statistical reset

`POST /reset-statistics` clears all successful/failed/partial/interrupted reads, request
measurements, cycles, finished jobs and resolved incidents in one durable transaction
at the command barrier. `/reset-errors` remains a compatible alias for this same action.
The button confirms the complete scope; it is shared by ingress/public/mobile.
`meta.statistics_reset` records the timestamp, actual cleared counts and zero remaining
counts at commit, so a queued reset can be verified while new reads accumulate.

Prices, min/max, state/summary snapshots and JSON exports, watches, outbox/suppression,
unfinished jobs, last successful read state and open incidents remain intact. Active
incidents predating the reset stay in the dashboard; statistical problem history starts
with events updated/resolved after the reset. The last-cycle tile reads statistical
`cycles`, never the retained price summary.

`request_window` is the bounded last hour of operational request times/durations/outcomes,
seeded once from existing request measurements and committed/pruned with each request.
It is independent of erasable statistics so reset/restart preserves provider pacing.
It contains no product URL, watch identity, price or credentials.
