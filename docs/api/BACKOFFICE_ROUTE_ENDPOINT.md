# GET /route — response format (Backoffice API)

**Source:** supplied by the backend team 2026-09-22. Stored here verbatim in substance;
everything under "Verified against production" is this repo's own measurement, added
2026-09-23. Handler: `backend/src/apigw_backoffice/get_routes_by_date.py`.

Consumer this was designed for: the Supabase BI sync. Goal: make route profitability and
per-order economics visible downstream.

---

## ⚠️ Production does not yet match this document

Measured live 2026-09-23. **Read this before building against the spec.**

| Piece | Spec says | Production actually |
|---|---|---|
| Base response | live | ✅ live, and **fast** — 0.2 s for a 30-day window |
| `internalCostCents` (cents, string) | live | ❌ **not shipped** — still `internalCost` in **euros** |
| `?include=stops,orders` | PR to staging pending | ❌ 400 `Unknown query parameters: ['include']` |

Two corrections to the spec's own assumptions:

- **"the Supabase routes table holds 0 rows" is false.** It holds **25 rows** (2026-08-05 →
  2026-09-21), written by the N8N *Routes Sync* workflow. The spec uses the empty-table claim to
  argue that dropping `internalCost` outright is safe. It is still *probably* safe — 25 rows are
  cheap to rebuild — but the premise should be corrected before someone relies on it for a
  bigger table.
- **`/route` is no longer timing out.** `docs/GOTCHAS.md` and `CLAUDE.md` both record routes
  sync as blocked on an upstream Lambda timeout. Measured: 1 day 1.1 s, 8 days 0.2 s, 30 days
  0.2 s, zero failures. That gotcha is stale.

### The unit trap, which is live right now

`internalCost` is **euros**; `totalSales` is **cents**. Proven two ways:

```
internalCost values carry decimals   3985.97, 786.3, 359.25, 69.9   -> cannot be integer cents
cost as EUR  = 31,248 of 40,100 EUR sales = 78%   plausible partner cost
cost as cent =    312 of 40,100 EUR sales =  0.8% absurd
```

`public.routes.internal_cost` is an `integer` column with no unit in its name, so
`total_sales - internal_cost` is wrong by 100×. This is exactly what the rename fixes, and
until it ships the sync must convert.

---

## Request

```
GET {BO_URL}route?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD[&include=stops,orders]
x-api-key: {BO_KEY}
```

| Param | Required | Notes |
|---|---|---|
| `start_date` | yes | Filters on `route.date` (service date), inclusive |
| `end_date` | yes | Inclusive; must be ≥ `start_date` |
| `include` | no | `stops`, `orders`. Unknown value → 400. **Not yet in production.** |

Invalid/missing dates → 400. Unknown query params → 400. No routes → `200 []`.

## Response

JSON array, sorted by `date` desc then `routeId`.

```jsonc
{
  "routeId": "b3f1…",
  "date": "2026-05-26",                 // service date
  "hubId": "9a2c…",
  "partner": "Apukuski Helsinki Oy",    // HUB#{hubId}.organizationName; null if hub gone
  "internalCostCents": "12050",         // TARGET. Today: `internalCost`, euros. "0" if empty,
                                        // null only on legacy routes
  "orderIds": ["ord-a", "ord-b"],
  "warehouseOrderIds": ["ord-b"],       // orders with ≥1 stop carrying a storageId
  "totalSales": "32450",                // cents, VAT-incl; sum of attributedRevenue

  "orders": [                           // only with include=orders — NOT LIVE
    { "orderId": "ord-a", "revenue": "23450", "attributedRevenue": "23450", "warehouse": false },
    { "orderId": "ord-b", "revenue": "18000", "attributedRevenue": "9000",  "warehouse": true }
  ],

  "stops": [                            // only with include=stops — NOT LIVE
    { "index": 0, "type": "PICKUP", "orderId": "ord-a", "storageId": null,
      "status": "DONE", "arrivalTime": "2026-05-26T08:30:00+03:00",
      "stopDurationSeconds": "300", "driveDurationSeconds": "1200",
      "address": "Testikatu 1, Helsinki", "location": { "lat": "60.17", "lng": "24.94" } }
  ]
}
```

### `orders` — per-order revenue

One entry per `orderIds` element, same order.

- **`revenue`** — full charge, integer-cent string, **VAT-inclusive**: `charge.vatPrice +
  charge.serviceFee`. Both components VAT-inclusive (serviceFee confirmed 2026-09-22; the order
  app labels it *"Palvelumaksu (sis. ALV 25,5 %)"*). This is what the customer paid — Stripe
  charges exactly this.
- **`attributedRevenue`** — contribution to this route's `totalSales`: equals `revenue`, halved
  when `warehouse` is true, encoding "a warehouse order's revenue is shared between its two
  route legs".
- **Invariant:** `sum(attributedRevenue) == totalSales` for every route. Self-verifying — the
  sync should assert it.
- Unpriceable orders (deleted, unparseable charge) give `null` for both. **`null` means unknown
  revenue, not zero**, and `totalSales` silently skips them.

*Pending:* `serviceFee` is being removed platform-wide; its migration folds historical
serviceFee into `vatPrice`, after which `revenue` is `charge.vatPrice` alone — same totals.

### `stops` — per-stop breakdown

Driving order, `index` 0-based and contiguous. Zipped from the ROUTE item's parallel arrays —
zero extra DynamoDB reads.

- `type` PICKUP/DELIVERY; `status` progress state.
- **`storageId` non-null ⇒ warehouse stop**, which is exactly what puts its `orderId` into
  `warehouseOrderIds`.
- One order can appear at several stops (pickup and delivery are separate).
- Legacy routes with short/missing arrays return `null` per value rather than erroring.
- **Privacy whitelist:** the stored stop dicts also carry `contactInfo` (customer name/phone)
  and `rahtikirja` (waybill links). These are **deliberately never emitted**, and a regression
  test asserts they cannot leak. Do not add fields without a privacy review.

### `internalCostCents` — per-route cost

Staff type a **euro** amount into a free-text *'Internal cost (€)'* field; storage stays euros,
the API converts at read time. Empty stores `0`, so expect `"0"`; `null` only on legacy routes.

- **Per-route only, by design.** No per-order allocation rule exists in the backend and the API
  will not invent one (equal split vs revenue-weighted is a product decision). Allocate
  downstream if you must.
- **VAT status is NOT encoded and NOT verified** — it is whatever the staff member typed from
  the partner's invoice. **Do not compute margins against the VAT-inclusive revenue fields
  until this is answered.** Record the answer here when it exists.

## Conventions

- **Money:** integer cents as strings, VAT-inclusiveness stated where known
  (`internalCostCents` stays VAT-neutral until verified).
- **Contract stability:** without `?include=`, the shape matches the pre-breakdown response
  except the deliberate break `internalCost` (euros, number) → `internalCostCents` (cents,
  string).
- **Nulls** mean "recorded as empty / not determinable"; breakdown arrays exist only when their
  `include` token is passed.

## Design history (2026-09-22, from the backend team)

The morning design chose always-on additive fields, a flat `orderId→cents` map and minimal
`{index, orderId, storageId}` stops — decided before discovering the Aug 27 implementation
(`0ad0dd1a`) and that the ROUTE item already stores rich per-stop data. The Aug 27 shape won on
review: richer stops at zero extra cost, tested privacy whitelist, and `attributedRevenue`
making shared-route accounting explicit instead of pushing the halving rule to BI. Still
rejected: full customer contact details on stops (privacy), per-order `internalCost` allocation
(no rule exists), exposing route-share without gross (drift risk — both are sent).

## Verified against production (this repo, 2026-09-23)

- 25 routes in 2026, 2026-08-05 → 2026-09-21. 14 in the last 30 days.
- Latency: 0.2 s for a 30-day window. No timeouts in any window tried.
- Fields present: `routeId, date, hubId, partner, internalCost, orderIds, totalSales,
  warehouseOrderIds`. Absent: `internalCostCents, orders, stops`.
- `internalCost` confirmed **euros** (decimal values; 78% cost ratio).
