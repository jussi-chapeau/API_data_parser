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
