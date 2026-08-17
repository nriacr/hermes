# Hermes Development Contract

This file is the authoritative working agreement for coding agents in this
repository. Read it before making changes. The detailed system handoff is in
`docs/CURSOR_HANDOFF.md`.

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
  `ha-addon/app/hermes/providers/`. A fix for one site must not change another
  site's parser or pricing semantics.
- New sites receive a new provider and focused tests. Register them through
  `providers/registry.py`; do not add site-specific parsing to `service.py`.
- Shared orchestration belongs in `service.py`, shared HTTP behavior in
  `http_client.py`, persistence in `storage.py`, and UI rendering in the shared
  dashboard/settings modules.
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
- `/data/state.json` contains notification suppression and price history.
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
  and work through both ingress and public access.

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
  restarts depend on Supervisor access.
- The container starts three processes through `ha-addon/run.sh`: ingress UI,
  public UI, and the foreground monitoring service.
- Persistent runtime data stays under `/data`; code stays under `/app`.
- Do not expose the public dashboard without its token path and the user's
  reverse proxy/tunnel controls.

## Change workflow

1. Inspect the current implementation and relevant tests before editing.
2. Confirm the working tree and current version in `ha-addon/config.yaml`.
3. Add a regression test that demonstrates parser or business-rule bugs.
4. Make the smallest coherent architectural change; remove superseded logic.
5. Run focused tests while iterating, then run `sh tools/check.sh` before release.
6. For runtime behavior changes, increment the current add-on patch version.
   Never guess the version from conversation history.
7. Update README/docs when behavior, options, providers, or operations change.
8. Review the diff for secrets, unrelated changes, stale code, and compatibility.
9. Commit intentionally and push to `main` so Home Assistant receives the update.

Documentation-only changes do not require an add-on version bump.

## Definition of done

- The reported behavior is fixed at its root and covered by a regression test.
- Other providers still pass their tests.
- `sh tools/check.sh` passes.
- Ingress/public/mobile parity is preserved.
- No secrets or runtime state are committed.
- Runtime changes include a version bump and are pushed to GitHub.
- The user receives a concise Turkish summary of behavior and verification.
