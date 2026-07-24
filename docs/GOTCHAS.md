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
