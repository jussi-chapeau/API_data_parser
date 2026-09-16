# Changelog

One entry per deploy to production — date, what shipped, why it matters. Detail in `git log`.

**Versioning started 2026-08-08.** This project (`api_data_parser`: AWS Backoffice API →
Supabase → Lovable/BI) is versioned independently from the **BI Chatbot** project — they
ship on different schedules from different repos. When announcing a release anywhere shared
(Slack, the BI bot channel), always name **which project** shipped: "api_data_parser
vX.Y.Z" vs. "BI Chatbot vX.Y.Z". Scheme (SemVer-ish, no public API so read loosely):
- **MAJOR** — a schema or behavior change downstream consumers (BI Chatbot, Lovable) must
  react to (new required filter, changed column meaning, breaking migration).
- **MINOR** — new data source, new column, new capability; backward compatible.
- **PATCH** — bug fix, backfill, ops/credential change; no new capability.

Versions before 2026-08-08 are reconstructed retroactively from `git log` for continuity,
not tagged at the time.

## v1.8.0 — 2026-09-16

- **Meta cut over to Windsor** (migration 026). Boundary is **2026-03-31, not 03-16**, and the
  reason matters: Windsor's Meta feed is exact for April, May, August and September, but March
  was short by exactly €146.06 / 42,524 impressions — precisely the "Waitlist" campaign, which
  ran 2026-03-05..03-30 and appears nowhere in Windsor. That's Meta's Marketing API excluding
  deleted campaigns, not a config error; the day after it stopped, both feeds agree exactly.
  Cutting at 03-16 would have silently erased €146 of real spend. **Windsor under-reports any
  historical period containing since-deleted campaigns — legacy is the only record.**
  Supermetrics workflow `vuQOMC0tnTaZkMTC` deactivated. All three Supermetrics syncs are now
  off.
- **Fixed a live double-counting defect I introduced** (migration 027). Each contract view is
  `legacy WHERE date <= B` UNION ALL `core` — but only the legacy half was bounded. That was
  accidentally safe until core accumulated earlier history than Windsor originally had, at
  which point the halves overlapped: **93 duplicated dates in `analytics_ga_daily_totals`, 98
  duplicated keys in `ads_meta_placement_daily`**, double-counting every SUM over the affected
  range. Caught because Meta's 2026-03-31 spend read €72.02 against a true €36.01. Both sides
  of all three unions are now explicitly bounded; duplicates verified zero on the natural key.
- **The rename/OID trap struck a second time** — renaming the Meta legacy table silently
  repointed `analytics_freshness` at it again, exactly as migration 025 warned. Rebound.

## v1.7.0 — 2026-09-16

- **Durable `core` layer between Windsor and reporting** (migrations 022/023). Windsor treats
  its destination tables as a rolling window it owns — it deletes rows outside
  `Backfill data for`, replaces rows wholesale, and rewrites whatever table a task points at.
  With the contract views reading staging directly, a misconfigured task **deleted a month of
  production GA4 data** (2026-08-17..09-15) earlier the same day; it was recoverable only
  because a snapshot had been taken speculatively that morning.
  Now: `windsor.*` (volatile) → `core.*` (**merge-only, never deleted**) → `public.*` views,
  refreshed every 30 min by pg_cron. Both safety properties were tested live rather than
  assumed: deleting a row from staging leaves core intact, and a NULL from staging does not
  overwrite a real value (`COALESCE(EXCLUDED.col, core.col)` — which matters because a
  narrowed field list silently sends NULLs). The view switch was invisible to the consumer:
  715 rows, zero gaps, every spot value unchanged.
- **All three Windsor sources live** — GA4 (#564), Google Ads (#577), Meta (#578), each to its
  own table. Along the way: two duplicate GA4 tasks, both ad tasks silently pointed at the GA4
  table, and Windsor's undocumented **3-column cap on Columns to Match**, which forced Meta
  from placement grain down to `(date, campaign_id, publisher_platform)` — keeping the
  Facebook/Instagram split, dropping position/device detail that nothing reports on
  (migration 021).
- **Google Ads cut over to Windsor** (migration 024) after validating **six complete months
  exact** on spend, clicks and impressions — March €2,377.42, April €5,027.19, May €7,659.81,
  June €7,806.23, July €6,472.26, August €9,080.44. `ads_google_campaign_daily` is now a view
  (legacy ≤ 2026-03-15 + core ≥ 03-16): 4,947 rows, 2024-08-05..2026-09-16, no duplicate
  keys. Supermetrics workflow `CZbzvcmagNxvC1JN` deactivated.
- **Fixed a silent monitoring failure I introduced in the same change** (migration 025).
  Postgres views bind to object OIDs, not names, so renaming the legacy table silently
  repointed `analytics_freshness` at the frozen legacy table — it would have reported the same
  date forever and never alerted. Caught via a one-day discrepancy. Documented in GOTCHAS:
  after any rename-and-replace-with-a-view, recreate every dependent view.

## v1.6.0 — 2026-09-14

- **Ended a five-week GA4 reporting outage.** The GA4 sync stopped producing new data on
  2026-08-10; every nightly run still reported `success`. `public.analytics_ga_daily_totals`
  is now a **view** stitching Supermetrics history (587 days, ≤ 2026-08-10) to the live
  Windsor.ai feed (≥ 2026-08-15) — 617 rows through 2026-09-13, **zero code change in
  apukuski-bi-chatbot**, which keeps reading the same table name. Verified by connecting as
  the real `bi_chatbot_readonly` role, not just by checking grants.
  `supabase/migrations/014_ga4_windsor_cutover.sql`.
- **Deactivated `Analytics GA Daily Sync` (j4gatZqXaw9tk55x)** — first step of dropping
  Supermetrics. It wrote the table that is now a view; a `UNION ALL` view is not
  auto-updatable, so leaving it active would have turned a silent no-op into a nightly
  failure.
- **New `Data Freshness Watchdog` (G0y7frdNMt3EU9cL), daily 07:00.** Alerts on `MAX(date)` —
  how recent the *data* is — not on `synced_at` or job exit status. **This is the fix for the
  root cause, not the symptom:** the Supermetrics job re-stamped `synced_at` on old rows
  nightly, so the BI bot's staleness warning reported "fresh" for 35 days. Live-tested: it
  correctly flagged Meta as 12 days stale. New `public.analytics_freshness` view exposes
  `data_age_days` alongside `false_freshness_days` so the gap stays visible.
- **GA4 feed fully restored and validated.** Backfilled Windsor to 2026-07-01 (75 days, no
  gaps), which closed the 08-11..08-14 hole, replaced the partial 08-10 row (7 sessions ->
  236), and created the first overlap between the old and new feeds. **Validated on 40
  overlapping days: sessions diverge 0.02%, users 0.00%** — Windsor reproduces Supermetrics.
  View boundary moved to 2026-06-30 (migration 017); contract view now 622 rows,
  2025-01-01..2026-09-14, zero gaps, verified as `bi_chatbot_readonly`.
- **`windsor_writer` now owns its staging tables** (migration 016). Windsor issues
  `ALTER TABLE ... ADD COLUMN` for unmatched fields, which needs ownership — without it a
  single wrong field name silently took the whole feed down in a retry loop while the vendor
  UI reported "Connection successful".
- **Fixed two real bugs in `scripts/apply_supabase_migration.py`'s statement splitter**,
  both found while applying 014: a semicolon inside a single-quoted string (a `COMMENT` body)
  split a statement in half; and the fix for that initially broke apostrophes in inline `--`
  comments, which collapsed migration 011 from 10 statements to 1. Both covered by cases now,
  regression-checked against all 14 migrations.

## v1.5.0 — 2026-09-03

- **`orders-reconcile` now reconciles both directions.** It only ever detected surplus rows
  in Supabase; nothing watched for rows *missing* from Supabase. That blind spot let one May
  2026 order sit missing for three months, because a count-based check reported "difference
  of 1" which reads as ordinary sync lag. Backoffice-only rows are now re-fetched and
  upserted immediately — additive, so no approval gate; gating it would recreate the exact
  "nobody noticed" failure. Supabase-only rows still require human approval, unchanged. Both
  counts are logged every run so zero-drift is visible rather than assumed — via a new
  `sync_log.details` JSONB column (migration 013), *not* `error_message`, which stays
  reserved for real failures. An abort stops both directions.
- **Backfilled the one missing May order** (`e8578d18…`, 2026-05-24), through the new repair
  path rather than a one-off script.
- **New `scripts/verify_order_parity.py`** — per-month ID-level diff. Replaces
  count-comparison as the parity check, since counts cannot say which row differs or in which
  direction.
- **New `scripts/order_transform.py`** — shared transform so repaired rows are byte-identical
  to normally-synced ones. Note this is now the third copy of that logic (Python module, the
  four sync workflows' `Transform Orders`, and `Reconcile`); `tests/test_order_transform.py`
  exists specifically to catch divergence between them.
- Tests: 42 passing (was 22).

## v1.4.1 — 2026-09-02

- **Fixed: revoking a deletion approval was silently ignored.** `orders_delete_approvals` is
  append-only for the BI bot by design, so a revocation is a later contradicting row rather
  than an edit — but `delete_approved_orders.py` treated "an approval row exists" as
  authority and would delete anyway. Reachable through ordinary use (recording an approval
  does not clear the candidate from the queue, so the BI bot can legitimately record a
  `rejected` for the same order before the next run). New view
  `orders_delete_approvals_current` (migration 012) resolves latest-wins in SQL,
  `approved_at DESC, id DESC`. The `id` tiebreak is load-bearing: `approved_at` is
  transaction time, so `executemany` batches share a timestamp — proven live. No UPDATE grant
  added; the table stays append-only and only the read changed. A current `rejected` blocks
  deletion and leaves the candidate `pending`. Reported by the BI project.
- **GDPR: deletion backups now have a defined home, permissions and retention.** They contain
  full order rows including customer street addresses, so they are a fresh copy of the data
  the deletion erases. **They previously defaulted to the CWD — the repo root — and were not
  gitignored, so a backup of customer addresses was committable.** Now
  `backups/deleted_orders/` (gitignored, `0700`/`0600`), 30-day retention pruned on every
  run so it cannot lapse. Documented in this repo's new `docs/GDPR_REVIEW.md`, which also
  records a larger pre-existing gap: `orders` has no retention policy at all.
- Tests: 22 passing (was 13).

## v1.4.0 — 2026-09-02

- **Phantom-order deletion pipeline: detection + containment.** The orders sync was
  upsert-only with **no delete path at all**, so anything Backoffice deleted survived here
  forever — inflating BI counts and retaining customer addresses past the controller's own
  deletion (GDPR Art. 5(1)(e)). August 2026 overstated by 34 gigs / EUR 16,520 this way.
  New: `scripts/reconcile_phantom_orders.py` (proposes, never deletes),
  `scripts/delete_approved_orders.py` (the only path that deletes, and only from the
  reviewed list, re-verified against the live API immediately beforehand),
  `n8n-workflows/orders-reconcile.json` (daily 04:30), and
  `supabase/migrations/011_phantom_order_deletion.sql`.
  **Abort rails are the core of the design** — an unreadable day, a day returning zero
  upstream ids while Supabase holds rows, >50 candidates / >10% of the window, or a stalled
  sync log aborts the run writing nothing, because at the point of computation a stalled
  sync is indistinguishable from mass upstream deletion.
  `tests/test_reconcile_abort_rails.py`: 13 tests, all passing.
  Acceptance test passed exactly as specified: August → exactly 34 candidates, June → 0,
  July → 0. **Live**: migration 011 applied (2 tables, 5 indexes, 3 RLS policies), workflow
  `ur0vJrdAnHaCNhcF` active daily 04:30. End-to-end verified — the N8N run independently
  reproduced the Python implementation's exact result (34 candidates, all `pending`, all
  2026-08-14/NB-Palvelut) with `orders` untouched. **Nothing is deleted yet and nothing will
  be** until the BI approval path is wired up and a human approves; the 34 sit as `pending`
  by design. See `docs/STATUS.md` workstream G for the remaining open questions.
- **Root cause logged upstream, not fixed here** — `BACKOFFICE_API_ISSUES.md` **#17**: the
  route import has no idempotency key, so reruns mint fresh `order_id`s (4 reruns on
  2026-08-14). Also documents why the 6 remaining August *duplicate* groups can only be
  fixed in Backoffice: they still exist upstream, so deleting them here just gets them
  re-upserted by the hourly sync.
- **Fixed `scripts/apply_supabase_migration.py`'s statement splitter** — it split on every
  `;`, which tears `DO $$ ... $$` blocks apart and fails with "unterminated dollar-quoted
  string". Found while applying 011, whose RLS grants live in a DO block. Now dollar-quote
  aware; would otherwise have broken every future migration containing a DO block, function
  or trigger. Regression-checked against an existing migration.
- **Sync hardening — deployed and live-verified on all 4 order-sync workflows.**
  `sync_log` is now trustworthy, which the abort rails depend on: `rows_upserted` comes from
  PostgREST's `Content-Range` (DB-confirmed) instead of the count of rows merely
  transformed, and a new `Log Sync Failed` node writes real `status='failed'` rows —
  previously `'success'` was hardcoded and **100% of rows claimed success**. Also: explicit
  `?on_conflict=order_id` instead of PostgREST's implicit PK fallback, and Transform Orders
  now de-duplicates by `order_id` before the POST (a repeated id in one batch raises
  `ON CONFLICT DO UPDATE command cannot affect row a second time` and fails the *whole*
  batch).

## v1.3.0 — 2026-08-24

- **New excl-VAT price columns on `orders`**, requested by apukuski-bi-chatbot while
  reconciling revenue against the Master P&L sheet (VAT-exclusive throughout — they were
  re-deriving this downstream with fragile string-parsing). New columns:
  `base_price_cents`, `services_price_cents`, `recycling_surcharge_cents`,
  `total_excl_vat_cents` (the sum — välitetty myynti excl. VAT, deliberately not
  liikevaihto, which needs a commission-rate computation the sync layer doesn't own).
  Re-verified the underlying formula fresh against 973 real orders (not just re-citing
  the original June validation): 771/772 exact. Backfilled for all existing platform
  orders directly from the already-stored `charge` JSONB — no Backoffice API calls
  needed. Manual orders: `null`, no source data exists to compute this from.
  `supabase/migrations/010_add_excl_vat_price_components.sql`.
- **Fixed: `platform_fee`/`service_fee` had no decimal-cohort handling** — silently
  storing decimal-euro values (a known API quirk, `BACKOFFICE_API_ISSUES.md` #2) as
  1/100th their real amount. Fixed at the sync layer and backfilled retroactively for
  existing orders — **this changes historical `platform_fee`/`service_fee` values** for
  the affected orders (confirmed via a fresh check: 20.7% of recent platform orders, well
  above the original ~4% estimate — not a rounding difference). See `docs/STATUS.md`
  workstream F and `docs/REVENUE_DEFINITIONS.md`.

## v1.2.3 — 2026-08-13

- **GDPR fix: `payments_paytrail.raw_payload` was storing card BIN/last-4/country
  verbatim.** Found during a cross-repo GDPR review with apukuski-bi-chatbot — Paytrail's
  `GET /payments/{id}` response includes a `cardInfo` object that was never stripped,
  unlike `payments_stripe`'s explicit sanitizer. Fixed with an allowlist of known-safe
  fields (not a denylist), and cleaned the 3 already-synced rows retroactively. See
  `docs/STATUS.md` workstream E and `apukuski-bi-chatbot/docs/GDPR_REVIEW.md`.

## v1.2.2 — 2026-08-12

- **Added discount/promo-code tracking to `payments_stripe`**, requested by the BI chatbot
  after it hit a gap trying to compute the `upsell_discount_used` funnel stage.
  `supabase/migrations/009_add_stripe_discount_fields.sql`: `discount_coupon_code` +
  percent/amount-off + applied cents (flat columns for the common case) plus a `discounts`
  JSONB column (full detail, multi-discount edge case). Confirmed live: Stripe's bare
  `session.discounts` only gives an opaque promo-code object ID, not the human-readable
  code — needed `expand[]=total_details.breakdown` too, confirmed it works alongside the
  existing fee/refund expand in the same request. Verified against real data (5 of 100
  sampled sessions had a real discount) and re-synced — all landed with correct
  codes/percentages/amounts.

## v1.2.1 — 2026-08-12

- **Fixed a real production outage caused by v1.2.0's Stripe sync, same day.**
  `payments-stripe-daily`'s Upsert node lacked `executeOnce: true` — n8n HTTP Request nodes
  run once per input item by default, so it fired ~90+ near-simultaneous duplicate
  full-array upserts (322 rows, re-sent whole on every one of those runs). Caused real
  Postgres lock contention (confirmed via Supabase's own logs: `ShareLock` waits,
  `canceling statement due to statement timeout`) that cascaded into a full outage —
  Database/PostgREST/Auth/Storage all unhealthy, Cloudflare 522s. Found the same bug latent
  (not yet triggered) in `ads-google-daily`, `ads-meta-daily`, `analytics-ga-daily` — present
  since v1.0.0's ship date, just at lower volume. All 4 fixed and verified same day.
  `orders-hot/warm/cool.json`/`backfill.json` were already correctly protected from an
  earlier lesson; it didn't carry forward to the workflows built after. See
  `docs/GOTCHAS.md` for the full mechanism.

## v1.2.0 — 2026-08-12

- **Payments sync live: `payments_paytrail` and `payments_stripe`.** Requested by
  apukuski-bi-chatbot for upsell-discount tracking, cost/margin reporting, and orders
  reconciliation (previously no queryable payment source existed anywhere).
  `supabase/migrations/008_create_payments_tables.sql`. Paytrail has no list/date-range
  API — confirmed live — so the sync is event-driven: extended the existing "Paytrail
  Payment Callback Tracking" N8N workflow (not owned by this repo) with a parallel,
  fault-isolated branch, rather than the periodic-pull pattern used elsewhere. Confirmed
  live that Paytrail's `reference` field isn't reliably `orders.order_id` — it's a real
  order_id UUID for direct settlement payments, but a short Airtable OfferID for the
  "schedule later" freight flow; resolved via an Airtable lookup when needed, `null` when
  it can't be, never guessed. No historical backfill possible for Paytrail (no transaction
  list exists to backfill from) — data accumulates from ship date forward. Stripe, by
  contrast, has a real paginated list endpoint, so it follows the standard periodic-pull
  pattern (`payments-stripe-daily`, rolling 30 days); `metadata.order_id` on checkout
  sessions is a direct, reliable UUID join key, and a single nested `expand[]` call gets
  fee/refund data Paytrail doesn't expose at all. Verified end-to-end: 322 rows with real
  order_ids and fee data. GDPR: no cardholder PII, customer email/name/address synced from
  either provider — amounts, statuses, timestamps, and the order-reference key only. See
  `docs/STATUS.md` workstream E. **See v1.2.1, same day** — this release's Stripe sync
  caused a real production outage, fixed same day.

## v1.1.0 — 2026-08-08

- **Fixed a permanent, project-lifetime sync gap: unscheduled orders never synced to
  Supabase.** Backoffice `/order` silently omits orders without a `firstSchedule` (offer /
  "schedule later" freight gigs — real, paid orders, confirmed via Paytrail) unless the
  undocumented `includeUnscheduled=true` parameter is passed; our `Fetch Orders` nodes never
  passed it. Found via a daily gig-count mismatch (10 synced vs. 14 real for 2026-08-07,
  reported in the BI Chatbot's Slack channel). Verified live: a 45-day baseline-vs-param
  diff found 96 hidden orders, all `firstSchedule: null`; other guessed parameter names all
  `400`'d, confirming this one specifically is real. Fixed in `orders-hot/warm/cool.json` +
  `backfill.json`; historical gap backfilled via `scripts/backfill_unscheduled_orders.py`.
  **Partly explains** `docs/BACKOFFICE_API_ISSUES.md` issue #15's Huutokaupat.com "confirmed
  gap" finding — 9 of the 96 hidden orders were Huutokaupat.com-tagged; Tokmanni/Rusta are
  unaffected, their gap stands. See `docs/GOTCHAS.md`.

## v1.0.0 — 2026-08-06

- **Marketing data ingestion** — Google Ads, Meta Ads, and GA now live in Supabase:
  `ads_google_campaign_daily`, `ads_meta_placement_daily`, `analytics_ga_daily_totals/source/geo`.
  Ads/Meta via the Supermetrics API direct (not Sheets — avoided a pivot/staleness problem in
  the existing Sheets-based dashboard queries); GA via its existing native Sheets export. 3 new
  N8N workflows (`Ads Google Daily Sync`, `Ads Meta Daily Sync`, `Analytics GA Daily Sync`), all
  active. Historical backfill: Ads full 2yr, Meta ~13mo (a real Meta Ads Insights API retention
  limit, not fixable), GA full history via the Sheets read itself. Full build/debug trail in
  `docs/STATUS.md` workstream B.
- **Fixed a ~2-day silent production outage, and the monitoring chain that should have caught
  it** — Supabase API key rotated at some point without updating the 8 workflows using it
  (Orders Hot/Warm/Cool, Routes Sync, Reference Data Sync, Backfill, and the monitoring
  workflows themselves), so no order/route/hub data synced from 2026-08-04 16:00 UTC onward.
  Found while answering a routine gig-count question. Fixing the key alone wasn't enough —
  two more independent bugs were also silently blocking alerts: `Sync Error Handler`'s
  `callerPolicy` rejected calls from workflows in a different N8N project
  (`workflowsFromSameOwner` → `any`), and `Supabase Health Check`'s `Healthy?` node crashed on
  strict type validation before ever reaching the Slack alert (`strict` → `loose`). All three
  fixed and independently verified (live re-trigger for the key fix, a real scheduled run for
  the type-validation fix). See `docs/GOTCHAS.md`.
- **`orders.is_asuntosaatio_gig` flag + value correction** — Asuntosäätiö B2B moves (tenant
  signing-bonus deals) are recorded as €0 by the source system; Asuntosäätiö pays outside the
  order record. New column flags these (mention anywhere in the order + recorded value = €0,
  manual orders only) and overrides the value to the real flat rate (€400 excl. VAT / €502 incl.
  VAT), preserving the original €0 for audit. Applied in N8N Transform Orders across all 4
  order-sync workflows going forward; backfilled 24 existing rows
  (`supabase/migrations/006_add_asuntosaatio_gig_flag.sql`). See `docs/GOTCHAS.md`.

## v0.4.0 — 2026-07-30

- **Fixed silent order-sync data loss** — Backoffice `/order` intermittently
  500/504s on multi-day ranges (~47% failure rate reproduced on 30-day
  windows); `Fetch Orders`/`Fetch Manual Orders`/`Fetch Routes` had no retry
  configured on any of the 4 sync workflows, so failed chunks never landed in
  Supabase. Added `retryOnFail` (5 tries, 3s wait) to all Fetch nodes on the
  live workflows via `scripts/add_fetch_retry.py`. Confirmed
  `errorWorkflow`/Slack alerting (`Sync Error Handler`, added 2026-06-26) was
  already correctly wired on all 4 — that part wasn't the gap.
- **Backfilled 245 missing orders** — 2025-11 through 2026-04 (5 months) plus
  2 additional gap days found via a full 2024-01–2026-07 audit
  (`scripts/verify_sync_counts.py`), via `scripts/backfill_missing_months.py`
  (day-by-day, exponential backoff). One remaining exception is permanent and
  documented, not fixed: a 2024-01-03 `/manual-order` record with no
  `orderId` in the source API — see `docs/BACKOFFICE_API_ISSUES.md` #14.
- **New reusable scripts** — `backfill_missing_months.py`,
  `verify_sync_counts.py`, `add_fetch_retry.py` for any future recurrence.

## v0.3.1 — 2026-07-29

- **N8N API key rotated** — New key issued in N8N Cloud; updated in local
  `.claude/settings.json` (gitignored) and Supabase Edge Function secrets via
  `scripts/deploy-edge-functions.sh`. Old key revoked after verifying
  `n8n-trigger-sync` / `n8n-update-schedule` still authenticate.

## v0.3.0 — 2026-07-24

- **`orders.origin` column** — Migration `002_add_origin_to_orders.sql`; N8N Transform Orders
  maps platform order source (`app`, `AVY#{id}`). Manual orders store null. Backfill script:
  `scripts/backfill_order_origin.py`.

## v0.2.0 — 2026-07-23

- **Manual-order VAT total transform** — N8N Transform Orders writes
  `manual_data.total_incl_vat_*` from `charge.charge` (handles `203,82 €` format); platform
  fees null for manual rows. Supabase backfill script for existing rows.

- **Webhook triggers on sync workflows** — All 6 canonical workflows expose POST webhooks
  (`orders-hot-sync`, etc.) for Lovable Run Now via Edge Function.

- **Edge Function deploy script** — `scripts/deploy-edge-functions.sh` + `supabase/config.toml`
  (requires `SUPABASE_ACCESS_TOKEN`).

- **Financial reconciliation script** — `scripts/reconcile_airtable_financials.py` compares
  Airtable settlement CSV vs API snapshot.

- **Claude/agent setup integrated** — Root `CLAUDE.md`, `docs/GOTCHAS.md`, `docs/DECISIONS.md`,
  `docs/README.md` added; template archived under `docs/archive/`.

## v0.1.1 — 2026-07-13

- **Project architecture docs** — `docs/project-architecture.md`, `docs/supabase-api.md` added
  for pipeline and consumer reference.

- **Routes sync blocked** — `/route` Lambda timeout documented; upstream fix pending.

## v0.1.0 — 2026-06

- **Initial pipeline** — N8N workflows syncing orders, hubs to Supabase; tiered hot/warm/cool
  schedules; health check + Slack alerts to `#tech-alerts-sos`.
