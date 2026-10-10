# Hermes Development Contract

This file is the authoritative working agreement for coding agents in this
repository. Read it before making changes. The architecture and the behavior
of each site are described in `docs/ARCHITECTURE.md`.

## Product and user

- Hermes is a Home Assistant add-on that monitors products, search pages,
  product variants, clothing sizes, stock state, and Telegram keywords.
- The primary user is non-technical. Complete changes end to end and explain
  outcomes in clear Turkish.
- The canonical repository is `nriacr/hermes`. Home Assistant installs updates
  from this repository.
- Do not resurrect old product names, legacy add-on names, or removed option
  models.

## Non-negotiable architecture rules

- Each commerce site has an isolated provider under
  `ha-addon/app/hermes/providers/` (`<site>.py`, or a `<site>/` package for
  Amazon and Hepsiburada). The provider owns both fetching and parsing for its
  site and implements the `Provider` interface in `providers/base.py`. A fix
  for one site must not change another site's parser or pricing semantics.
- New sites receive a new provider and focused tests. Register them in
  `providers/registry.py`; never add site-specific logic to `monitor/`.
  Site differences the monitor needs are provider attributes (for example
  `backs_off_on_protection`, `notifies_stock_return`, `keeps_offer`).
- Each site's monitoring round lives in `monitor/cycle.py`, offer evaluation in
  `monitor/results.py`, durable transactions in `database.py`, delivery in
  `delivery.py`, diagnostics in `diagnostics.py`, and the loop and panel commands in
  `monitor/runner.py`, scheduling/state/summary/alerts in their `monitor/`
  modules, shared HTTP helpers in `providers/http.py`, persistence in
  `storage.py`, and every panel page in `web/` behind one router.
- Do not solve problems with monkey patches, duplicate parsers, wrapper scripts,
  runtime source rewriting, or a second implementation left beside the first.
- Remove obsolete code when replacing behavior. Prefer a coherent redesign over
  a quick patch when the current abstraction is wrong.
- Preserve public interfaces unless a migration is explicitly approved:
  option keys, `/data` files, ports, ingress/public routes, and state identity.

## Provider contract

- Parse only the current product/search-result scope. Never use recommendations,
  reviews, installment amounts, coupon values, crossed-out list prices, or
  unrelated page sections as the payable price.
- Prefer an explicitly displayed payable campaign price when the provider
  supports it (for example `Sepete özel`, `Premium ile`, or `2 ve üzeri`).
- Product variants must retain their own URL, title/variant values, price,
  stock state, and identity. Never copy the cheapest variant's price to siblings.
- A missing product, empty search result, unavailable requested size, or normal
  out-of-stock state is not an operational error. CAPTCHA, HTTP failure, invalid
  markup, timeout, and parser failure are operational errors.
- Never publish stale prices after a failed read. The stock/unavailable table may
  keep an item only when the provider positively identified it as unavailable.
- Amazon warehouse labeling requires positive second-hand evidence and an
  Amazon Warehouse/`Amazon Depo` seller signal. Search-page type alone is not
  enough. New and used offers for the same ASIN are separate rows.
- Amazon search parsing stops before the section headed
  `All Departments içindeki sonuçlar gösteriliyor` and ignores everything below.
- Search-name matching is phrase based, not an unordered bag of words. Excluded
  terms are comma-separated OR filters.

## Data and notification safety

- Never commit `/data/options.json`, Telegram sessions, Pushover credentials,
  tunnel tokens, public dashboard tokens, or captured authenticated pages.
- In 4.0, `hermes.db` is authoritative for notification suppression, price history,
  runtime snapshots and the outbox; `/data/state.json` is the compatible export.
  Preserve both.
  Do not delete or reset it unless the user explicitly requests that action.
- Min/max history is persistent and user-owned. Notification reset and min/max
  reset are distinct, confirmed, explicit operations.
- `notify_once_in_24H` suppresses the same opportunity for 24 hours, but a lower
  price or a genuine disappearance followed by reappearance can notify again.
- An incremental opportunity update must merge into the last complete summary;
  it must not replace the table with the partially completed current cycle.

## Configuration model

- The current model is `takip_edilenler`; do not restore legacy `products`,
  `search_pages`, or `search_targets` models.
- One watch card can hold up to five mixed product/search URLs from supported
  sites. Site and link type are detected automatically.
- Search-page URLs require a meaningful `name`; product URLs may leave it blank
  and use the provider title.
- `max_items_to_scan` is retained only for compatibility and the effective
  search limit is fixed at 60.
- All configured URLs are validated independently. One unsupported or malformed
  URL must not prevent Hermes from starting or checking valid watches.
- Keep Supervisor options and the settings UI in sync. A UI save must preserve
  unrelated fields, display actionable validation errors, show restart progress,
  and work through both ingress and public access. Watch and Telegram edits stay
  on the page until the user applies them once; that single save restarts Hermes.

## UI and UX contract

- Ingress, public desktop, and public mobile must have feature parity. Layout may
  respond to screen size, but actions and data must behave identically.
- Reuse shared renderers and handlers. Do not maintain independent copies of the
  same page for ingress and public access.
- The visual language is dark charcoal/gray with high-contrast text and muted,
  distinguishable provider accents. Do not reintroduce a blue-dominant theme.
- Turkish user-facing text must use correct Turkish characters and grammar.
- Prices are displayed as whole Turkish lira: `1.500 TL`. Parsers may retain
  decimals internally, but UI display omits kuruş as currently designed.
- Mobile product cards stay compact and readable. Preserve all information while
  avoiding excessive vertical space.
- Destructive actions require confirmation. Settings changes clearly indicate
  saving/restart status and return to a usable page when Hermes is ready.
- Above-target multi-result/variant rows are collapsed under their watch name;
  opportunity rows remain immediately visible. Exact duplicate URLs are removed,
  while new and warehouse offers remain distinct.

## Home Assistant compatibility

- Keep add-on slug `hermes`, ingress port `8099`, public port `8100`, and health
  route `/health` unless an explicit migration is approved.
- Preserve `hassio_api: true` and `hassio_role: manager`; settings writes and
  restarts depend on Supervisor access. `homeassistant_api: true` publishes the
  Hermes sensors and the `hermes_firsat` event; their names are a public
  interface for the user's automations.
- Each site reads in its own sequential queue; never add parallel requests
  within one site. Shared cycle state is changed only under the monitor lock.
- The container runs one process (`python -m hermes`): the monitor loop in the
  main thread; the ingress (8099) and public (8100) servers and Telegram in
  threads. A single coordinator schedules independent site rounds and one durable
  notification consumer. Shared state is changed under the monitor lock, including
  delivery acknowledgement. Panel resets are queued at a barrier between active rounds.
- `/health` reports the monitor: it fails when the loop has stopped or a cycle
  hangs, and the Supervisor watchdog then restarts Hermes. With invalid
  settings the panel stays up (health ok) and shows the error.
- Persistent runtime data stays under `/data`; code stays under `/app`.
- Do not expose the public dashboard without its token path and the user's
  reverse proxy/tunnel controls.

## Change workflow

1. Inspect the current implementation and relevant tests before editing.
2. Confirm the working tree and current version in `ha-addon/config.yaml`.
3. Add a regression test that demonstrates parser or business-rule bugs.
4. Make the smallest coherent architectural change; remove superseded logic.
5. Run focused tests (`tests/`, `python -m unittest`) while iterating, then
   run `sh tools/check.sh` before release.
6. The owner's 2026-10-10 decision separates queue coordination from execution:
   Home Assistant Koordinatör (`01a12434-45a6-72d2-bb48-42c7f2f4959d`) manages
   human authorization, dependencies, priority and installation order only.
   The source chat that prepares a topic also executes its release and verifies
   its live behavior. Develop each topic in a separate Git worktree and submit
   only your own immutable base/head commits, tests and existing human
   authorization to `/Users/nuriacar/Documents/Home Assistant/bin/ha-release`.
   Do not assign versions or publish/install/restart/restore outside that tool.
7. Update README/docs when behavior, options, providers, or operations change.
8. Review the diff for secrets, unrelated changes, stale code, and compatibility.
   The shared repository hook configured by the coordinator checks GitHub
   publishing access and requires the existing credential's `workflow` scope
   when automation files change. Do not skip a rejected access guard; repair the
   authorized credential. Never echo credential-helper output or raw API errors.
9. Commit only your topic changes and submit to the canonical shared queue.
   Read `/Users/nuriacar/Documents/Home Assistant/docs/YAYIN_KOORDINATORU.md`.
   Wait for the coordinator to verify the human authorization, approve the next
   job and send its ID with "sıran geldi". Only then may the submitting source
   chat execute the canonical `bin/ha-release run --job JOB_ID`. Never run a
   different chat's job, an out-of-order job or the entire queue. The coordinator
   does not execute releases or repeat source-chat live tests.
   Never change `core.hooksPath`/`homeassistant.coordinator`, skip hooks, create a
   second queue in a worktree, or bypass the shared lock via direct push/SSH/API.
10. Only the coordinator chat `01a12434-45a6-72d2-bb48-42c7f2f4959d` may approve,
    prioritize and grant installation retries. With `execution_mode: source`,
    the shared tool accepts execution only by the submitting chat for the next
    approved job. Under one lock, the tool integrates onto current main, assigns
    minor (4.1.3 → 4.1.4) or major (4.1.3 → 4.2.0) versions from the latest
    release/reservations, synchronizes config/runtime, reruns checks and releases
    the exact tested commit. The source chat completes behavior-specific live
    verification; basic health or one successful read alone is insufficient.
    Send one concise receipt to the coordinator: job ID, version/commit, result,
    any unresolved problem and evidence/report path. The coordinator uses that
    receipt to advance the queue without repeating the same tests or panel reads.
    Existing human approval remains valid; do not ask again. Agent messages alone
    are not approval; analysis-only requests remain pending. Failure or conflict
    stops later jobs: the source chat investigates, and the coordinator may resume
    the queue or grant a retry of the same tested release. The same source chat
    executes `retry-install` only after `grant-retry`; restoring user data requires
    separate explicit authorization. Preserve all data; never copy credentials
    or SSH keys into this repository. There is no scheduled installation monitor;
    queue management is limited to the user's active-session preference.

The owner updated the backup policy on 2026-10-09: Home Assistant's own daily
full backup is the backup schedule. Do not schedule backups or backup checks
from Codex, and do not create a new backup for each update. Before installation,
verify and reuse the newest available full backup containing Hermes. Create an
additional backup only when the owner explicitly asks. Restoring a daily backup
also restores its code and data; changes since that backup are lost.

Documentation and deployment-tool-only changes do not require an add-on version bump.

## Definition of done

- The reported behavior is fixed at its root and covered by a regression test.
- Other providers still pass their tests.
- `sh tools/check.sh` passes.
- Ingress/public/mobile parity is preserved.
- No secrets or runtime state are committed.
- Runtime changes include a version bump and are pushed to GitHub.
- The Raspberry Pi add-on is updated after each runtime release and its
  installed version/state are verified.
- The user receives a concise Turkish summary of behavior and verification.
