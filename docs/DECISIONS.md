# Decisions

Chronological log of architecture/design decisions. Newest entry last.

---

## 2026-07-23 — Accept manual-order total-only pricing from API

**Decided:** For `is_manual=true`, store `charge.charge` as VAT-inclusive total in
`manual_data.total_incl_vat_*`. Leave `platform_fee` / `service_fee` null. Do not attempt
automated partner settlement from API for manual orders.

**Rejected:** Treating missing breakdown as a blocking bug; reverse-engineering fees from total.

**Why:** Backend/ops confirmed manual pricing is calculated by hand in Airtable; API only
exposes the customer-facing total incl. VAT.

---

## 2026-07-23 — Separate platform vs manual financial semantics in Supabase

**Decided:** Platform rows use net fee cents + full `charge` JSON; manual rows use gross total
in `manual_data` with `price_basis: gross_incl_vat`.

**Why:** Mixing tax bases breaks Lovable dashboards and Airtable reconciliation.

---

## 2026-06 — Tiered order sync (hot / warm / cool)

**Decided:** Three schedules for orders (hourly 3d, 6h days 4–14, daily days 15–45) instead of
one full pull every run.

**Why:** Balance freshness vs API load and Supabase write contention.

---

## 2026-06 — Supabase as analytics source of truth (not direct Backoffice queries from Lovable)

**Decided:** Lovable reads Supabase; sync via N8N. Privileged actions (Run Now, schedule change)
via Edge Functions → N8N API.

**Why:** CORS, credential safety, and consistent transformed schema.
