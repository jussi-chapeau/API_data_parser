# Backoffice API — issues for the AWS/API team

**Raised:** 2026-07-29
**API:** `https://qtml5qv6uk.execute-api.eu-central-1.amazonaws.com/production`
**Reported by:** data pipeline team (Backoffice API → N8N → Supabase → Lovable)

All findings below were reproduced against the live production API on 2026-07-29.
Every claim includes the request used, so each item should be independently verifiable.

Working endpoints: `/order`, `/manual-order`, `/route`, `/hub`, `/customer`, `/organization`.

---

## P1 — Blocking

### 1. `/route` never returns; routes dataset is empty

**Reproduce**

```bash
# single day
curl "$API/route?start_date=2026-07-28&end_date=2026-07-28" -H "x-api-key: $KEY"
# -> HTTP 504 {"message": "Endpoint request timed out"}

# one week
curl "$API/route?start_date=2026-07-01&end_date=2026-07-07" -H "x-api-key: $KEY"
# -> HTTP 500 "Lambda Error"
```

Fails even for a **single-day** window, so this is not a payload-size problem.
`/hub` and day-chunked `/order` on the same gateway respond normally.

**Impact** — the `routes` table in our warehouse holds **0 rows**. No route
profitability, partner utilisation, or capacity reporting is possible. Open since
2026-07-13.

**Requested** — fix the Lambda (suspect an unbounded query or missing index).
Confirm a single-day request returns inside the 30s gateway limit.

---

### 2. `/order` returns two different price units in the same field

`charge.vatPrice` (and the whole `charge` object) is **integer cents** for most
orders but **decimal euros** for a distinct subset, with no flag to distinguish them.

**Reproduce** — same endpoint, same day:

```
vatPrice=16482    integer cents   origin=app    organizationName=MoveYou
vatPrice=27008    integer cents   origin=app    organizationName=Kirill
vatPrice=115.83   DECIMAL EUROS   origin=null   organizationName=null
vatPrice=85.35    DECIMAL EUROS   origin=null   organizationName=null
vatPrice=72.5     DECIMAL EUROS   origin=null   organizationName=null
```

Example order: `4d4806b5-b240-4eb3-9e60-849601de3d3e` (created 2026-07-07).

**Scale** — 297 of 6,820 platform orders (May–Jul 2026). July alone: 101 decimal
vs 235 integer. First appears **May 2026**, so it looks like a newer code path.

**Impact** — silently corrupts revenue. A consumer treating the value as cents
reads `840.7` as €84.07 instead of €840.70. 62 of the 297 have a single decimal
place and are therefore understated **10×**. Measured understatement across
May–Jul: **€8,856.81**.

**Requested** — emit integer cents everywhere (preferred, matches the documented
contract), or add an explicit `currencyUnit` field. Please do **not** leave this
inferred from the presence of a decimal point.

---

### 3. Orders carry no customer reference — `/customer` cannot be joined

`/order` returns 20 fields and **none** identifies the customer:

```
charge, commissionRate, content, created, deliveredAt, firstSchedule,
inTransitAt, orderId, orderState, orderType, orgId, organizationName,
origin, platformFee, review, routeId, schedule, serviceFee, stops, underwayAt
```

No `userId`, no email — the character `@` does not appear anywhere in the payload.

`/customer` exists and is well populated (5,681 records: `userId`, `firstName`,
`lastName`, `email`, `phoneNumber`, `company`) but contains **no order reference**
in return. We scanned all 5,681 records: zero UUIDs other than each record's own
`userId`.

**Ruled out as workarounds**

| Attempt | Result |
|---|---|
| `orgId` as customer key | 0 of 15 `orgId` values match any `userId`; all 15 match `/organization` |
| Manual-order `customer` object | Has `email`/`name`/`phoneNumber` but **no** `userId` |
| Email string matching | Only 8 of 43 manual customer emails exist in `/customer` (19%) |
| Filters on `/order` | `?userId=`, `?customerId=`, `?customer=`, `?user=`, `?email=` all ignored |
| Expansion on `/customer` | `?include=orders`, `?expand=orders`, `?withOrders=true` all ignored |
| Bridge endpoints | 13 candidates probed (`/booking`, `/gig`, `/userorder`, …) — all 403 |

**Impact** — no customer-level analytics are possible for the 6,820 platform
orders (62% of all orders): no repeat-purchase rate, lifetime value, new-vs-
returning split, or churn. Two complete datasets with no path between them.

**Requested** — add **`userId`** to `/order` and `/manual-order` responses,
matching `/customer.userId`, and accept it as a query filter on `/order`. This is
the single highest-value change on this list: one field unlocks everything above.
(Adding `orderIds` to the customer record would also work but is a worse design.)

---

## P2 — Data quality

### 4. The decimal-price cohort is missing several fields

The same 297 orders share a fingerprint that no normal order has:

| Attribute | Decimal cohort (n=297) | Normal orders (n=6,523) |
|---|---:|---:|
| `origin` populated | 0% | 4% |
| `organizationName` set | 0% | 100% |
| `content` items populated | 0% | 100% |
| `platformFee` = 0 | 100% | 0% |
| `routeId` set | 83% | 0% |

`content` is a placeholder rather than empty: `[{ "id": null, "count": null,
"title": null }]`. These are also the only orders using state `IN_STORAGE`, and
they skew heavily to `Rahti` (148) and `Kuljetus` (127).

**Requested** — confirm which flow creates these and populate
`organizationName`, `content` and `platformFee` consistently, or document them as
a distinct order class with defined semantics. Returning a placeholder object
rather than `[]` or `null` is misleading.

---

### 5. `/manual-order` provides no fee breakdown

Verified on all 125 July manual orders: `platformFee` and `serviceFee` are
**null in 100%** of cases. The only monetary value is `charge.charge`, a
VAT-inclusive **free-text string**.

Across full history, 2,501 of 4,172 manual orders hold a value that cannot be
parsed as a number, e.g.:

```
"79,90 €/h"
"213,85 € (sis. ALV)"
"119,90€ (sis alv) /h. Laskutus alkaa kun lähdemme toimipisteeltämme..."
"Ei perittävää maksua, muuttolaatikoiden vuokra kuuluu muuton kiinteään hintaan."
```

**Impact** — automated partner settlement is impossible for manual orders; ops
calculate it by hand in Airtable. Manual and platform revenue also sit on
different tax bases (gross vs net), which is easy to mix up.

**Requested** — expose `platformFee`, `serviceFee` and a numeric total in cents
alongside the free-text field. If the breakdown genuinely does not exist upstream,
please confirm that in writing so we can stop treating it as a defect. At minimum,
add a numeric `chargeTotalCents` and keep the text in a separate note field.

---

### 6. Unknown query parameters are silently ignored

Every unsupported filter returns the **full unfiltered result set** with HTTP 200:

```
/order?userId=<uuid>          -> 110 records (baseline: 110)
/order?email=x@y.z            -> 110 records
/customer?lastName=Ruohomaa   -> 5,682 records (all of them)
/customer?email=...           -> 5,682 records
/customer?start_date=...      -> 5,682 records
```

Only `/customer?userId=` genuinely filters.

**Impact** — a caller cannot distinguish "filter applied, no matches" from
"filter ignored". This produced incorrect conclusions during our investigation.

**Requested** — return `400` for unrecognised parameters, or document the
supported set explicitly.

---

### 7. Intermittent `500 Lambda Error` on `/order` for multi-day windows

```bash
curl "$API/order?start_date=2026-07-01&end_date=2026-07-29"   # -> "Lambda Error"
curl "$API/order?start_date=2026-07-07&end_date=2026-07-07"   # -> 200 OK
```

Historical chunks fail unpredictably — a backfill run hit `500` on a 2024-05-30
to 2024-06-28 window while adjacent chunks succeeded.

**Confirmed impact, 2026-07-30:** this is not just slow — it caused real,
months-long data loss. `backfill.json`'s 30-day chunking had zero retry
configured, and `Log Sync` hardcoded `status: 'success'` regardless of what
actually happened, so failed chunks silently never landed in Supabase. Re-ran
30-day `/order` requests for 5 historical months 3x each: **7 of 15 attempts
(~47%) failed with 500/504**, clustering at 25–29s (a gateway/Lambda timeout
ceiling, not randomness):

| Month | attempt 1 | attempt 2 | attempt 3 |
|---|---|---|---|
| 2025-11 | 200 | 200 | 200 |
| 2025-12 | 200 | 504 (29s) | 200 |
| 2026-01 | 200 | 504 | 504 |
| 2026-02 | 200 | 504 | 200 |
| 2026-04 | 500 | 504 | 200 |

245 orders across those 5 months were missing from Supabase until manually
re-backfilled day-by-day. Fixed on our side: `retryOnFail` on all Fetch nodes,
an `errorWorkflow` that alerts + logs `status: 'failed'` honestly, and
`scripts/backfill_missing_months.py` / `verify_sync_counts.py` for day-chunked
recovery and ongoing verification. None of that fixes the underlying Lambda
timeout, though — it just stops us from silently losing data to it.

**Impact** — we must fetch **day by day** (≈60 requests per month, both
endpoints) to sync reliably. Slow and needlessly heavy on the Lambda.

**Requested** — support multi-day windows reliably, or return `413`/`400` with a
documented maximum range so clients can chunk correctly.

---

## P3 — Model and documentation gaps

### 8. `stops` is unstructured and holds only one address

`stops` is a single free-text string, not a structured pickup/delivery pair:

```
"Ersintie 7, Espoo, Suomi"
"Nouto 1:\nAkatemiantie 18, 02700 Kauniainen\nOmakotitalo, katutaso"
"1: Maaherrantie 6 A, 00710 Helsinki --> 2: Hattelmalantie 2, 00710 Helsinki"
"Carrental halli"
```

- **59%** of rows (1,129 of 1,900 sampled) contain no postcode at all
- Only 19 of 1,900 contain two postcodes, using ad-hoc separators (`-->`, `->`, `1:`)
- Which leg is recorded is inconsistent — for one order it was the delivery
  address, for another the pickup

**Impact** — no geographic analysis, route optimisation, or address-based
reconciliation against our sales records is possible. We verified three sold gigs
by delivery address and none could be matched reliably.

**Requested** — structured `pickup` and `delivery` objects with
`street`, `postcode`, `city`, `lat`, `lon`. The Offers/Airtable layer already
holds these separately, so the data exists.

---

### 9. `origin` is sparse and contains an unrendered template

```
populated: 234 of 6,820 platform orders (3.4%)
values:    app (230), AVY#{id} (4), null (rest)
```

`AVY#{id}` is a **literal template placeholder** that was never interpolated —
the intended ID is missing. `origin` only starts appearing in July 2026, so
channel attribution is impossible before then, and manual orders never have it.

**Requested** — fix the `AVY#{id}` interpolation bug, backfill `origin` where the
source is known, and document the permitted value set.

---

### 10. `hubId` and `orgId` are absent on platform orders

```
platform (n=336):  hubId 0/336    orgId 0/336
manual   (n=125):  hubId 125/125  orgId 125/125
```

**Impact** — platform orders cannot be attributed to a hub. `organizationName`
is a free-text name with 82 distinct values including inconsistent duplicates
(`Eld Muutot` vs `Eld Muutot Oy`), so it is not a reliable key.

**Requested** — populate `hubId` and `orgId` on platform orders, matching
`/hub.hubId` and `/organization.organizationId`.

---

### 11. Manual orders never reach a terminal state

All 125 July manual orders are `CONFIRMED`; **0 are `DELIVERED`**, and
`deliveredAt` / `underwayAt` / `inTransitAt` are null throughout.

**Impact** — delivery metrics and on-time reporting silently exclude 38% of the
order base. Anyone filtering `orderState = DELIVERED` gets platform-only figures
without realising it.

**Requested** — drive manual orders through the same lifecycle, or document that
they are excluded from state tracking.

---

### 12. No unscheduled state; `/customer` has no pagination

**RESOLVED 2026-08-08 (scheduling half)** — confirmed via live probe: `/order`
supports `includeUnscheduled=true`, undocumented but real (verified by diffing
baseline vs. param responses for the same day — the param adds rows, never
removes them, and every added row has `firstSchedule: null`). Without it,
`/order` silently omits orders that haven't been scheduled yet (offer/"schedule
later" freight gigs, confirmed paid via Paytrail). Root-caused via a live
production incident: Supabase's daily gig count for 2026-08-07 was 10 vs. 14
real (Airtable) — traced to exactly this gap. A 45-day live-API diff
(2026-06-24–2026-08-07) found **96 hidden orders**, all `firstSchedule: null`.
Fixed 2026-08-08: `includeUnscheduled=true` added to the `Fetch Orders` node in
all order-sync workflows (`orders-hot/warm/cool`, `backfill`); historical gap
backfilled via `scripts/backfill_unscheduled_orders.py`. See `CHANGELOG.md` and
`docs/GOTCHAS.md`.

This also **partially explains issue #15** (Tokmanni/Rusta/Huutokaupat.com) —
see the correction note there. Still open: please confirm `includeUnscheduled`
is the intended/stable way to retrieve these, since it's undocumented — other
guessed parameter names (`unscheduled`, `scheduled=false`, `orderState=UNSCHEDULED`,
etc.) all returned `400`, so this isn't a general "any param works" case, `includeUnscheduled`
specifically is real. Originally: "of 1,900 orders sampled, only 1 lacks
`firstSchedule`" — that finding was itself a symptom of this same default-omission
behavior, not evidence unscheduled orders don't exist upstream.

**Pagination (still open)** — `/customer` returns all 5,681 records (1.1 MB) in
one response with no `limit`/`offset` and no date filtering. This will not scale.

---

### 13. Possible same-day indexing delay (needs confirmation)

At 13:33 Helsinki on 2026-07-29, `/order` returned **1** order created that day.
From 28 complete days, 40.7% of a typical day's orders are booked before 13:00,
and the Wednesday norm is 14–24 gigs/day. The two preceding days were 30 and 28.

We cannot tell whether this is an indexing delay or genuinely low volume. If
same-day orders surface late, please document the expected lag — it affects the
accuracy of any intra-month reporting.

---

### 14. `/manual-order` can return records with no `orderId` at all

```bash
curl "$API/manual-order?start_date=2024-01-03&end_date=2024-01-03" -H "x-api-key: $KEY"
# one record has no "orderId" field, empty charge.charge, empty orderType:
# { "customer": {"name": "...", ...}, "charge": {"paymentMethod": "Card/cash", "charge": ""},
#   "orgId": null, "orderType": "", ... no "orderId" key at all }
```

Found while reconciling 2024-01 as part of the sync-gap investigation (see #7)
— 1 record out of ~6,800 checked (2024-01 through 2026-07). `order_id` is our
warehouse's primary key, so this record structurally cannot be stored by any
sync implementation, old or fixed. Not the same bug as #7 — this row is
malformed at the source, not lost in transit.

**Impact** — one permanently un-syncable historical order (2024-01-03,
customer Hanna Leimu per the API's own data). Low volume, but worth knowing
`orderId` isn't guaranteed present on `/manual-order` before writing code that
assumes it.

**Requested** — backfill the missing ID if the underlying order is real, or
confirm these should be filtered/excluded upstream.

---

### 15. Confirmed partner channels (Tokmanni, Rusta, Huutokaupat.com) produce zero tagged orders

Internal config shows a deliberate per-hub origin-tag mapping for a Tokmanni
partnership — ~20 locations, e.g. `tokmanni-HER`, `tokmanni-VAR`, `tokmanni-MAN`,
`tokmanni-RUO`, `tokmanni-CIT`, `tokmanni-MYY`, `tokmanni-RED`, `tokmanni-KAA`,
`tokmanni-KON`, `tokmanni-KNL`, `tokmanni-ITI`, `tokmanni-JAR`, `tokmanni-KAI`,
`tokmanni-TIK`, `tokmanni-MAL`, `tokmanni-TAM`, `tokmanni-ARA`, `tokmanni-HYR`,
`tokmanni-POR`, `tokmanni-KER` (one path, `/RK`, currently shows as undefined).
Rusta is a separate confirmed direct-deal partner running Apukuski-specific
advertising. Huutokaupat.com (the online auction site) is a third confirmed
partnership that should also be tracked. Related to #9 (`origin` sparse) but
distinct — this is about *named, confirmed-live* partnerships, not general
sparsity.

**Reproduce**

Checked every text-bearing field (`origin`, `organizationName`, `content`,
`stops`, `manual_data`) across the full order history (~9,500+ orders, our
Supabase copy), case-insensitive, for all three plus four control terms to
validate the method:

| Term | `origin` | Elsewhere | Verdict |
|---|---|---|---|
| Tokmanni | 0 | 2 (coincidental: parking-lot landmark, packing-material description) | **Confirmed gap** |
| Rusta | 0 | 23 (coincidental: Rusta retail store as a pickup address) | **Confirmed gap** |
| Huutokaupat.com | 0 | 33 real (auction-lot descriptions, freight notes, pickup instructions — genuinely auction-sourced orders) | **Confirmed gap** |
| AVY | **5, correctly tagged** | 1 unclear/coincidental | Working as designed — positive control |
| Asuntosäätiö | 0 | 48, correctly captured in `manual_data` (own tracking mechanism, not `origin`) | Not an `origin` gap — different tracking path |
| Jysk | 0 | 61 (coincidental: JYSK retail store used as a pickup landmark) | Same pattern as Rusta/Tokmanni's coincidental mentions — no tracked partnership |
| Rakentajien Konevuokraamo / "RK" | 0 | 0 — checked the full company name too, not just the abbreviation | Genuinely absent everywhere |

AVY's clean result (correctly tagged, findable nowhere else) confirms this
method reliably distinguishes a working integration from a broken one — the
zero results for Tokmanni/Rusta/Huutokaupat.com aren't a search-methodology
gap.

**Then checked directly against the live API**, to rule out a sync gap: fetched
`/order` + `/manual-order` day-by-day for the last 45 days
(2026-06-23–2026-08-07, 770 records, with retry/backoff per the pattern in #7).
Identical result for every term above — same order IDs, same zero matches for
the three confirmed gaps. **This rules out a sync gap**: our pipeline
faithfully mirrors what the API returns; the absence is at the source, not
lost in transit.

**Correction 2026-08-08** — that live-API check predates the discovery of
`includeUnscheduled=true` (see issue #12): `/order` silently omits orders
without a `firstSchedule` unless that parameter is passed, so the 45-day check
above missed anything still unscheduled. Re-ran the same 45-day window with
`includeUnscheduled=true` and diffed against the baseline (unscheduled-only)
response: **Tokmanni and Rusta still show zero matches in the previously-hidden
set — their "confirmed gap" verdict is unchanged.** Huutokaupat.com is
different: **9 of the 96 orders hidden by the sync-gap bug are
Huutokaupat.com-tagged** (`content.title.category` = `"huutokaupat.com"`,
confirmed on real order records, e.g. two of the four orders missing from the
2026-08-07 daily report). That sync gap is now fixed (see issue #12) and the
historical gap backfilled, so these orders now exist in Supabase. This does
**not** change the core finding below — synced Huutokaupat.com orders still
have no `origin` tag — it only means the "zero" count for Huutokaupat.com was
partly a sync-gap artifact, not purely a tagging gap. Tokmanni/Rusta remain
fully unexplained by any known sync issue.

**Impact** — if these three partnerships are live and orders are actually
flowing through them, that volume is currently indistinguishable from
ordinary untagged `app` orders in every downstream system (Supabase,
reporting, the BI chatbot). Any revenue-share or attribution agreement tied
to these channels cannot be verified from API data at all right now.

**Requested** — confirm whether the Tokmanni/Rusta/Huutokaupat.com
origin-tagging integrations are actually wired into order creation and live.
If live, please explain why zero tagged orders have appeared in
`/order`/`/manual-order` over the last 45 days — check whether the tag is
being set but stripped before the order reaches these endpoints. If not yet
live, an ETA would help us know when this becomes measurable.

---

### 16. 46 orders bulk-created for one partner in one day, all with `platformFee: 0`

**Raised 2026-08-14.** 46 of that day's 55 platform orders (84%) were bulk-created
for a single organization, `NB-Palvelut`, in three machine-speed bursts, all with
`platformFee: "0"` — a fee value this partner's orders have essentially never had
before that day. Originally investigated as a possible Supabase sync-duplication
bug; **ruled that out** — every order matches a real Backoffice record 1:1 (ID-level
cross-check, zero orders on either side without a match except one still waiting on
the next hourly sync, normal lag). What's left is a real question about this specific
batch: was the platform fee correctly waived, or did something in however these
orders were created fail to calculate/attach it?

**Reproduce**

```bash
curl "$API/order?start_date=2026-08-14&end_date=2026-08-14&includeUnscheduled=true" \
  -H "x-api-key: $KEY"
```

Filter to `hubId == "89894f09-68ea-4ac1-bd58-f598328371f8"` and
`organizationName == "NB-Palvelut"` — 46 records, all sharing this fingerprint:

- `platformFee: "0"` (46/46)
- `origin: null` (46/46) — normal platform orders carry `app` or `n8n-offer`
- `content` is malformed: `[{"id": null, "count": "1", "title": "<a JSON-array
  re-encoded as a string, itself containing the real item list>"}]` — not a normal
  content array
- All 46 got a `routeId` and moved to `ROUTED` in the same tight timing pattern

Created in exactly 3 bursts:

| Window (UTC) | Orders | Span |
|---|---|---|
| 13:51:33 – 13:51:41 | 10 | 8s |
| 13:52:39 – 13:52:44 | 11 | 5s |
| 13:54:48 – 13:54:54 | 12 | 6s |
| 14:05:20 – 14:05:27 (routing pass) | 12 | 7s |

Example record (`orderId=7abf52cb-f029-4d08-9c57-731bd5a5b9a9`):
```json
{
  "orderState": "ROUTED",
  "orderType": "Rahti",
  "origin": null,
  "platformFee": "0",
  "commissionRate": 0.26,
  "routeId": "b9e93266-0c07-48e0-a876-ccdf86020663",
  "content": [{"id": null, "count": "1", "title": "\"[{\\\"id\\\":\\\"\\\",\\\"count\\\":2,...}]\""}]
}
```

**Historical context — this fee pattern is new for this partner.** Queried
`NB-Palvelut`'s full order history in our warehouse (187 orders since
2025-09-22): 103 have a real (`>0`) `platform_fee`, 38 are `null`, and **46 are
exactly `0` — and all 46 of those are from 2026-08-14.** Before that day, this
partner never had a `0` platform fee order. `commissionRate: 0.26` is present on
these records, matching the rate that produces a real fee on this partner's other
orders.

**Impact** — these 46 orders sum to **€23,093.82** in `vatPrice` — **93.4% of that
day's entire platform revenue** (€24,721.02 across all 55 orders). At each order's
own `commissionRate`, the platform fee that *should* apply to this batch is
approximately **€6,004.39**. If `platformFee: 0` is a bug rather than an intentional
waiver for this partner, that's the amount currently uncollected — not a
rounding-level issue, and it recurs on every order created through whatever path
produced this batch, not just this one day.

**Requested**
1. Confirm whether these 46 orders came through a bulk-import or automation path
   specific to `NB-Palvelut` (the malformed `content` field and machine-speed
   creation timing both point to some automated/bulk flow, not the normal app
   booking path).
2. Confirm whether `platformFee: 0` on this batch is intentional (a partner
   deal/waiver) or a calculation bug in whatever created these orders. If it's a
   bug, advise whether it's retroactively correctable or needs manual settlement.
3. Same question for `origin: null` and the malformed `content` field — expected
   for this creation path, or should they be populated the normal way?

---

## Summary

| # | Issue | Priority | Requested change |
|---|---|---|---|
| 1 | `/route` times out (504/500) | **P1** | Fix Lambda; single-day must respond |
| 2 | Dual price units in `charge` | **P1** | Integer cents everywhere, or `currencyUnit` |
| 3 | No customer reference on orders | **P1** | Add `userId` + query filter |
| 4 | Decimal cohort missing fields | P2 | Populate `organizationName`, `content`, `platformFee` |
| 5 | No manual fee breakdown | P2 | Add numeric fee fields / `chargeTotalCents` |
| 6 | Unknown params silently ignored | P2 | Return `400` |
| 7 | Intermittent 500 on date ranges | P2 | Support multi-day, or document max range |
| 8 | `stops` unstructured, single address | P3 | Structured pickup/delivery objects |
| 9 | `origin` sparse, `AVY#{id}` literal | P3 | Fix interpolation, backfill, document values |
| 10 | `hubId`/`orgId` null on platform | P3 | Populate both |
| 11 | Manual orders never `DELIVERED` | P3 | Unify lifecycle or document |
| 12 | No unscheduled state (**fixed client-side 2026-08-08** via `includeUnscheduled=true`); `/customer` no pagination | P1 → P3 (pagination only) | Confirm `includeUnscheduled` is stable/documented; add `limit`/`offset` |
| 13 | Possible same-day indexing lag | P3 | Confirm and document |
| 14 | `/manual-order` record with no `orderId` | P3 | Backfill ID or filter upstream |
| 15 | Tokmanni/Rusta/Huutokaupat.com partner channels: zero tagged orders | P2 | Confirm integrations are live; explain the gap |
| 16 | 46 bulk-created orders for one partner, all `platformFee: 0` (~€6,004 at stake) | **P1** | Confirm bulk-import path; confirm fee waiver vs. bug |

**If only three are actioned:** #3 (`userId`) unlocks all customer analytics,
#2 stops revenue being misreported, #1 recovers the entire routes dataset. #16 is
time-sensitive on top of those three — it's an active, recurring revenue question,
not backlog.
