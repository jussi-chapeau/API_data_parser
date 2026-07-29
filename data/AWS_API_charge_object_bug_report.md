# API Bug Report: Missing Financial Breakdown on `/manual-order` Endpoint

**Date:** 2026-07-13  
**Updated:** 2026-07-23  
**Reported by:** Apukuski data team  
**Severity:** Medium (accepted interim workaround)  
**Status:** **Accepted for now** — not blocking pipeline sync

---

## Decision (2026-07-23)

Confirmed with the development team:

- Manual-order financial **breakdown is not available via the API**.
- Ops calculates partner payout / Apukuski margin **by hand** (Airtable) for these jobs.
- The only usable price from `/manual-order` is `charge.charge`, which is the **total including VAT**.
- **Interim rule:** use that total **as-is**. Do not reverse-engineer net fees, commission split, or margin from the API for manual orders.

Full charge-object parity with `/order` remains a desirable backend improvement, but it is **not required** for the current data pipeline.

---

## Summary

The `/order` and `/manual-order` endpoints return structurally different `charge` objects. Regular orders include a full financial breakdown. Manual (hub/B2B) orders return only a VAT-inclusive total amount with no breakdown.

**Pipeline impact (accepted):**

- Store and display the manual total gross (incl. VAT).
- Do **not** attempt automated partner settlement or Apukuski margin from API fields for `is_manual=true` rows.
- Settlement/rev-share for manual jobs continues to live in Airtable / ops process.

---

## The Two Formats

### Format A — `/order` endpoint (full breakdown)

```json
{
  "orderId": "40ba2927-...",
  "orderType": "Muuttopalvelu",
  "orgId": null,
  "hubId": null,
  "commissionRate": 0.26,
  "platformFee": 797,
  "serviceFee": 0,
  "charge": {
    "workPrice": "5692",
    "hubDrivePrice": "1275",
    "servicesPrice": "0",
    "basePrice": "6967",
    "platformFee": "797",
    "serviceFee": "0",
    "vatPercentage": "25.5",
    "vatPrice": "9744"
  }
}
```

Fee fields on `/order` are **excl. VAT** (net). Platform fee gross ≈ net × 1.255 for Airtable comparison.

When breakdown is present, settlement formulas can be derived from API data:

```
partnerPayoutEur  = (workPrice + hubDrivePrice) × (1 − commissionRate)
apukuskiMarginEur = (workPrice + hubDrivePrice) × commissionRate + platformFee + serviceFee
```

(Amounts above in the same unit basis as the API fields — typically net cents converted to euros.)

---

### Format B — `/manual-order` endpoint (total only — accepted)

```json
{
  "orderId": "845b4a80-...",
  "orderType": "Muuttopalvelu",
  "orgId": "ee903bc3-...",
  "hubId": "30268822-...",
  "commissionRate": 0.3,
  "platformFee": null,
  "serviceFee": null,
  "charge": {
    "paymentMethod": "invoice",
    "charge": "323.40"
  }
}
```

`charge.charge` is a **euro string including VAT** (e.g. `"323.40"` = €323.40 gross).  
Top-level `platformFee` / `serviceFee` are null. No `workPrice` / `hubDrivePrice` breakdown.

---

## Interim Transform Rule (Supabase / N8N)

See also `docs/project-architecture.md` and `docs/supabase-api.md`.

For **manual** rows (`is_manual=true`):

1. Keep raw `charge` JSON as returned by the API.
2. Parse `charge.charge` as a float euro amount (gross, incl. VAT).
3. Store normalized cents in `manual_data.total_incl_vat_cents`.
4. Leave `platform_fee` and `service_fee` as `null`.
5. Do **not** strip VAT or invent fee lines from this total.

```javascript
// Manual-order financial transform (accepted interim)
const raw = d.charge && d.charge.charge != null ? String(d.charge.charge).trim() : null;
const normalized = raw ? raw.replace(/\s/g, '').replace(',', '.') : null;
const isPlainEuro = normalized && /^[-+]?\d+(\.\d+)?$/.test(normalized);
const totalInclVatEur = isPlainEuro ? parseFloat(normalized) : null;
const totalInclVatCents =
  totalInclVatEur != null && !isNaN(totalInclVatEur)
    ? Math.round(totalInclVatEur * 100)
    : null;

manual_data: {
  customer: d.customer || null,
  additionalInfo: d.additionalInfo || null,
  serviceFeeApplied: d.serviceFeeApplied || null,
  payment_method: (d.charge && d.charge.paymentMethod) || null,
  total_incl_vat_eur: totalInclVatEur,
  total_incl_vat_cents: totalInclVatCents,
  price_basis: 'gross_incl_vat',
  source_field: 'charge.charge',
  raw_charge_total: raw  // kept even when non-numeric (e.g. "119e/h")
}

platform_fee: null
service_fee: null
```

Non-numeric `charge.charge` values (hourly rates / free text) are **not** coerced into totals — `total_incl_vat_*` stays `null` and `raw_charge_total` preserves the original string.
For **platform** rows (`is_manual=false`): continue storing API net fee fields in `platform_fee` / `service_fee` (integer cents) and the full `charge` breakdown object.

---

## Scale of Impact (historical findings)

- Tested across hundreds of manual orders: Format B on essentially all of them.
- Airtable still holds the hand-calculated settlement breakdown for these jobs.
- API exposure of that breakdown would still be useful later; not required for current sync.

---

## Desired Future Fix (not blocking)

If/when backend can expose it, `/manual-order` `charge` should match `/order` field shape (`workPrice`, `hubDrivePrice`, `platformFee`, `serviceFee`, VAT fields, etc.). Until then, consumers must branch on `is_manual`.

---

## Field-Level Comparison: `/order` vs `/manual-order`

Measured across 330 regular orders (Nov 2025) and 329 manual orders (Jan–Mar 2026).

### Top-Level Response Fields

| Field | `/order` (330) | `/manual-order` (329) | Notes |
|-------|:--------------:|:---------------------:|-------|
| `orderId` | 330/330 | 329/329 | |
| `orderType` | 330/330 | 322/329 | |
| `orderState` | 330/330 | 329/329 | |
| `created` | 330/330 | 329/329 | |
| `schedule` | 330/330 | 329/329 | |
| `firstSchedule` | 330/330 | 329/329 | |
| `content` | 330/330 | 329/329 | |
| `stops` | 330/330 | 281/329 | |
| `organizationName` | 330/330 | 329/329 | |
| `commissionRate` | 330/330 | 329/329 | Present but not used for automated settlement on manual |
| **`platformFee`** | **330/330** | **—** | Missing on manual-order (accepted) |
| **`serviceFee`** | **330/330** | **—** | Missing on manual-order (accepted) |
| `underwayAt` | 188/330 | — | Delivery tracking timestamp |
| `inTransitAt` | 183/330 | — | Delivery tracking timestamp |
| `deliveredAt` | 175/330 | — | Delivery tracking timestamp |
| `hubId` | — | 329/329 | Manual-order only |
| `orgId` | — | 329/329 | Manual-order only |
| `customer` | — | 329/329 | Manual-order only |
| `serviceFeeApplied` | — | 329/329 | Manual-order only |
| `additionalInfo` | — | 246/329 | Manual-order only |

### `charge` Object Fields

| Field | `/order` (330) | `/manual-order` (329) | Notes |
|-------|:--------------:|:---------------------:|-------|
| `workPrice` | 330/330 | — | Missing on manual (accepted) |
| `hubDrivePrice` | 330/330 | — | Missing on manual (accepted) |
| `platformFee` | 330/330 | — | Missing on manual (accepted) |
| `serviceFee` | 330/330 | — | Missing on manual (accepted) |
| `basePrice` | 330/330 | — | |
| `servicesPrice` | 218/330 | — | |
| `vatPercentage` | 330/330 | — | |
| `vatPrice` | 330/330 | — | |
| **`charge`** | — | **~329/329** | **VAT-inclusive total euro string — use as-is** |
| `paymentMethod` | — | 329/329 | e.g. `"invoice"` |

---

## Related: `/route` Endpoint Timeout

Separately, `GET /production/route` has been observed to return API Gateway timeouts (`{"message":"Endpoint request timed out"}`) even for short date windows, while `/hub` and chunked `/order` calls succeed. That blocks `routes-sync`, not manual-order pricing. See `docs/project-architecture.md` pending issues.
