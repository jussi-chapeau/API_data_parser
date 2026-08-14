# Gotchas

Hard-won lessons. Read before changing sync transforms, financial fields, or dashboard metrics.

---

**Manual orders have no API fee breakdown.** `/manual-order` returns only `charge.charge`
(VAT-inclusive euro string) plus `paymentMethod`. Top-level `platformFee` / `serviceFee` are
null. Ops calculates partner payout and Apukuski margin by hand in Airtable. Do not
reverse-engineer fees from the total. Implemented in N8N Transform Orders →
`manual_data.total_incl_vat_*`. See `data/AWS_API_charge_object_bug_report.md`.

**Platform fees are excl. VAT; Airtable platform fee column is incl. VAT.** API `platformFee`
is net cents. Airtable "Alustamaksu (sis alv 25,5%)" ≈ API net × 1.255. Comparing without
normalizing looks like 100% mismatch.

**"Total gigs" ≠ "delivered gigs".** Total gigs for a month = count of rows from `/order` +
`/manual-order` with API date filter on **creation** (`created_at` in Supabase). Delivered
metrics use `order_state = DELIVERED`, `delivered_at`, or `first_schedule` — each gives
different numbers. Label dashboards explicitly.

**`/route` Lambda times out.** `GET /production/route` returns 504 even for small date windows.
Routes sync and `routes` table population blocked upstream. `/hub` and chunked `/order` work.

**Non-numeric manual `charge.charge` values.** Some manual orders store rates or free text
(e.g. `"119e/h"`, `"119e/h + laatikot 3,20e/kpl"`). Parser must reject these for
`total_incl_vat_cents` but keep `raw_charge_total` in `manual_data`.

**Supabase service key placeholder in workflow JSON.** Committed workflows use
`SUPABASE_SERVICE_KEY`. Live N8N must have the real key injected — placeholder causes 401 on
every write. GitHub push protection blocks committing the real key.

**Duplicate N8N workflows caused lock contention.** Running duplicate Routes/Orders Hot syncs
simultaneously crashed Supabase (July 2026). Use canonical workflow IDs in
`N8N_WORKFLOW_IDS.md` only.

**Manual orders stay CONFIRMED in API.** Manual `/manual-order` rows often never show
`DELIVERED` state or delivery timestamps — do not expect manual orders in delivered-by-state
counts.

**`origin` is platform-order only.** Backoffice API adds top-level `origin` on `/order` rows
(e.g. `app`, `AVY#{id}`) from July 2026 onward. `/manual-order` has no `origin` field — store
null in Supabase for `is_manual=true`.

**Commission rate drift.** API `commissionRate` may not match Airtable settlement rate for
some partners (e.g. 0.25 vs 0.26). Settlement source of truth for rev-share is Airtable until
backend aligns rates.

**N8N date filter is on order creation, not execution.** Sync windows (`start_date` /
`end_date`) filter when the order was created, not `Toteutuspäivä` / `first_schedule`.

**`/order` silently omits unscheduled orders unless `includeUnscheduled=true` is passed.**
Offer/"schedule later" freight gigs (no `firstSchedule` yet, but real and paid — confirmed
via Paytrail) never appeared in any sync, at any date range, going back to project start.
Found 2026-08-08 after a daily gig-count mismatch (10 synced vs. 14 real for 2026-08-07).
Verified live: baseline vs. `includeUnscheduled=true` diff over 45 days found 96 hidden
orders, all with `firstSchedule: null`; other guessed param names all 400'd, so this one is
real and specific. Fixed in `orders-hot/warm/cool.json` + `backfill.json`; historical gap
backfilled via `scripts/backfill_unscheduled_orders.py`. Also partly explains
`docs/BACKOFFICE_API_ISSUES.md` #15's Huutokaupat.com "confirmed gap" — 9 of the 96 hidden
orders were Huutokaupat.com-tagged. Manual orders don't need this: `/manual-order` 400s if
you pass it.

**`$input.all().map()` in an HTTP Request node's body still runs once per item unless
`executeOnce: true` is set on the node — and each run re-sends the full array.** N8N HTTP
Request nodes execute once per input item by default; `$input.all()` inside the body
expression doesn't change that, it just means every one of those per-item executions
independently re-evaluates to the *same full array* and re-sends it. Caused a real
production incident 2026-08-12: `payments-stripe-daily`'s Upsert node (322 checkout
sessions, no `executeOnce`) fired ~90+ near-simultaneous duplicate full-array POSTs to
`payments_stripe` within a 30ms window, causing real Postgres lock contention
(`ShareLock` waits, `canceling statement due to statement timeout` in Supabase's own logs)
that cascaded into a full outage — Database/PostgREST/Auth/Storage all went unhealthy
(Cloudflare 522s), Realtime/Edge Functions stayed up since they don't depend on the same
synchronous Postgres path. `orders-hot/warm/cool.json` and `backfill.json` already had
`executeOnce: true` on their Upsert nodes from an earlier lesson — but it didn't carry
forward when the marketing-sync workflows were built 2026-08-06, so `ads-google-daily`,
`ads-meta-daily`, and `analytics-ga-daily` had been running the identical bug daily,
just at low enough volume to not visibly break anything until Stripe's larger batch hit
it. All 4 fixed same day. **Check `executeOnce: true` is set on every future Upsert/batch
node that uses `$input.all()` in its body — this is not optional, it's the difference
between one request and N duplicate ones.**

**Monitoring workflows share credentials with what they monitor — and had two more
independent bugs on top of that.** A Supabase key rotation (2026-08) silently broke every
workflow writing to Supabase, ~2 days of missing order/route/hub data, zero alerts. Three
separate, independently-broken things had to each be fixed before alerting actually worked
again (fixing only the key was not enough):
1. The stale key itself, baked into 8 workflows including `Supabase Health Check`/
   `Sync Error Handler`.
2. `Sync Error Handler`'s `callerPolicy` was `workflowsFromSameOwner` — it lives in Jussi's
   **personal** N8N project, while `Orders Hot/Warm/Cool Sync` etc. live in the shared
   **"Apukuski" team project**. The `errorWorkflow` link was correctly configured on the
   callers, but n8n silently refused the actual cross-project call every time. Likely broken
   since whenever the team project was set up — probably well before this specific incident.
   Fixed: `callerPolicy: 'any'`.
3. `Supabase Health Check`'s `Healthy?` IF node had `typeValidation: 'strict'`, which crashed
   on Supabase's ping response (likely an `error: null` field strict mode can't coerce)
   *before* ever reaching the Slack-alert node — unrelated to the key, would have blocked
   alerting even with a valid key. Fixed: `typeValidation: 'loose'` (n8n's own error message
   suggested this exact fix).

Crashes that happen before a workflow's Log Sync node produce no `sync_log` row at all —
check `GET /executions` via the N8N API (or the UI) for silent failures, don't trust
`sync_log`'s absence of `'failed'` rows to mean things are healthy. And don't assume a
monitoring/alerting workflow works just because it's *there* — verify it actually fires,
end to end, occasionally.

**Asuntosäätiö B2B deals are recorded as €0.** Manual orders where Asuntosäätiö sponsors a
tenant's move ("signing bonus", free of charge to the tenant) always show
`manual_data.total_incl_vat_eur = 0` in the source data — Asuntosäätiö pays Apukuski outside
the order record. `orders.is_asuntosaatio_gig` (`'Yes'`/`'No'`, added 2026-08-06) flags these
(mention of "Asuntosäätiö" anywhere in the order + recorded value = €0, manual orders only)
and overrides the value to the real flat rate: €400 excl. VAT / €502 incl. VAT
(`manual_data.total_excl_vat_eur` / `total_incl_vat_eur`). Original €0 preserved in
`manual_data.original_total_incl_vat_eur` for audit. Applied in N8N Transform Orders (all 4
order-sync workflows) going forward, backfilled for 24 existing rows. Don't sum
`total_incl_vat_eur` for revenue without knowing this override exists, or Asuntosäätiö
volume will look like €0 in reports.

**A `raw_payload` column storing a provider's response verbatim needs an explicit
allowlist, not "it's probably fine."** Paytrail's `GET /payments/{id}` includes a
`cardInfo` object (card BIN, last-4, country) that isn't obvious from the fields used
elsewhere in the sync — found 2026-08-13 storing verbatim in `payments_paytrail.raw_payload`
for 2 days before catching it, inconsistent with `payments_stripe`'s explicit sanitizer
built the same day it shipped. When a new provider integration's `raw_payload` is built,
check the *full* live response (not just the fields you plan to use) for card/PII-adjacent
data, and allowlist known-safe fields rather than denylisting known-bad ones — a denylist
only catches what you already knew to look for; an unexpected future field from the
provider passes through an allowlist as excluded by default, not included by default.
