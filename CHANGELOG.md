# Changelog

One entry per deploy to production — date, what shipped, why it matters. Detail in `git log`.

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
