# Changelog

One entry per deploy to production — date, what shipped, why it matters. Detail in `git log`.

## 2026-08-06

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

## 2026-07-30

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

## 2026-07-29

- **N8N API key rotated** — New key issued in N8N Cloud; updated in local
  `.claude/settings.json` (gitignored) and Supabase Edge Function secrets via
  `scripts/deploy-edge-functions.sh`. Old key revoked after verifying
  `n8n-trigger-sync` / `n8n-update-schedule` still authenticate.

## 2026-07-24

- **`orders.origin` column** — Migration `002_add_origin_to_orders.sql`; N8N Transform Orders
  maps platform order source (`app`, `AVY#{id}`). Manual orders store null. Backfill script:
  `scripts/backfill_order_origin.py`.

## 2026-07-23

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

## 2026-07-13

- **Project architecture docs** — `docs/project-architecture.md`, `docs/supabase-api.md` added
  for pipeline and consumer reference.

- **Routes sync blocked** — `/route` Lambda timeout documented; upstream fix pending.

## 2026-06

- **Initial pipeline** — N8N workflows syncing orders, hubs to Supabase; tiered hot/warm/cool
  schedules; health check + Slack alerts to `#tech-alerts-sos`.
