# Revenue definitions — Apukuski

Confirmed with the business 2026-07-29. Read this before producing any revenue
figure, and use the Finnish terms — "revenue" alone is ambiguous here.

---

## The two numbers are not interchangeable

| Term | What it is | Notes |
|---|---|---|
| **Välitetty myynti** | Total the customer pays | Brokered/mediated sales. **Not** Apukuski revenue. |
| **Liikevaihto** | Välitetty myynti − partner share | Apukuski's actual turnover. |

Reporting välitetty myynti as "revenue" overstates the business by roughly 3×.

---

## Liikevaihto formula

Per gig, all components **excl. VAT** (net):

```
Liikevaihto = basePrice × commissionRate    (commission / Apukuskin tuotto)
            + platformFee                   (alustamaksu)
            + servicesPrice                 (palvelumaksu)
```

Partner share:

```
Kumppanille = basePrice × (1 − commissionRate)
```

### Where the components live

| Airtable (settlement) | Backoffice API `charge` | Unit |
|---|---|---|
| `Myynti (alv 0%)` | `basePrice` | cents, net |
| `Alustamaksu (sis alv)` ÷ 1.255 | `platformFee` | cents, net |
| `Palvelumaksu (sis alv)` ÷ 1.255 | `servicesPrice` | cents, net |
| `Komissiokanta` | `commissionRate` | fraction (0.20–0.30) |
| `Kumppani (alv 0%)` | — | derived |
| `Apukuskin tuotto (alv 0)` | — | derived |
| `Apukuski Share` | — | tuotto + alustamaksu + palvelumaksu, gross |

### Validation

Derived from the May 2026 Airtable settlement export (372 rows), not assumed:

```
Kumppani (alv 0) == Myynti (alv 0) × (1 − komissiokanta)   365/365 exact
API platformFee  == Alustamaksu / 1.255                      77/77 exact
May blended take rate                                        30.38%
```

**Re-verified fresh 2026-08-24** against 973 real platform orders from the prior two
months (not just June): `vatPrice / (1 + vatPercentage/100) == basePrice + platformFee +
servicesPrice + recyclingSurcharge` held 771/772 exact (excluding the decimal-cohort rows,
handled separately — see below). One correction found in the same pass: an older doc
(`data/AWS_API_charge_object_bug_report.md`) suggested `basePrice == workPrice +
hubDrivePrice` — checked directly, **only 667/772 (86%) match**. Don't use that
decomposition for anything; use `basePrice` directly, which is the one validated against
`vatPrice` at 99.9%.

### Synced columns (added 2026-08-24, migration 010)

`base_price_cents`, `services_price_cents`, `recycling_surcharge_cents`, and a computed
`total_excl_vat_cents` (= the four component columns summed — **välitetty myynti excl.
VAT, not liikevaihto**) are now normalized columns on `orders`, sourced from `charge`.
Previously these lived only inside the raw `charge` JSONB, forcing every consumer to
parse it themselves. Requested by apukuski-bi-chatbot while reconciling against the
Master P&L sheet. `total_excl_vat_cents` is null for manual orders (no source data exists
to compute it — see "Known gaps" below) and null for platform orders where `basePrice` or
`platformFee` itself is missing (mostly older, pre-Oct-2025 orders — see next section).

**Fixed in the same pass**: `platform_fee`/`service_fee` previously used a naive
`Math.round(parseFloat(...))` with no decimal-cohort handling (see "Two price units"
below) — silently storing a decimal-euro value like `"84.07"` as `84` cents (€0.84)
instead of `8407` cents (€84.07). Both the live sync and a one-time backfill of all
existing platform orders now correctly branch on whether the value is an integer (already
cents) or has a real fractional part (euros, needs ×100). This changed historical values
for the decimal-cohort orders — if you'd already cached/reported `platform_fee`/
`service_fee` figures, they may shift for those specific orders.

Take rate exceeds the bare commission rate because alustamaksu and palvelumaksu
sit on top of the commission. Observed: platform ~33–35%, manual ~29%.

---

## VAT

- Rate is a uniform **25.5%** (`vatPercentage` = 25.5 on all sampled orders)
- `charge.vatPrice` is the **VAT-inclusive** customer total
- `net = vatPrice / 1.255`, and `net = basePrice + platformFee + servicesPrice
  + recyclingSurcharge` reproduces `vatPrice` exactly (verified 424/424, June 2026)
- `serviceFee` is a separate field and was 0 across all sampled orders — do not
  confuse it with `servicesPrice`
- Report liikevaihto **excl. VAT** by default

---

## Recognition basis: sold date

**Liikevaihto is booked by sold date** (`created` / `created_at`), not delivery date.
Confirmed by the business 2026-07-29.

So a gig sold in July but delivered in August belongs to **July**. Filter revenue
on `created_at`, never on `first_schedule` or `delivered_at`.

### Do not filter revenue by `order_state`

`order_state` is unusable for revenue recognition:

- **0%** of manual orders ever reach `DELIVERED` — they stay `CONFIRMED` forever
  (0 of 129 in June, 0 of 123 in July). Filtering `DELIVERED` silently drops
  22–30% of liikevaihto and yields platform-only figures.
- By state, only 21–29% of monthly liikevaihto looks "delivered", which is a
  tracking artefact. Measured against `firstSchedule`, June was 97.7% performed.

Exclude `CANCELLED` only.

---

## Pass-through items

- `recyclingSurcharge` (jätemaksu) is included in välitetty myynti but **excluded
  from liikevaihto** — it is a pass-through to the waste facility, tracked
  separately in Airtable via `Jätemaksu maksettu`.

---

## Known gaps in the calculation

**Manual orders are approximated.** `/manual-order` returns no fee breakdown —
`platformFee` and `serviceFee` are null in 100% of cases, leaving only
`charge.charge` as a VAT-inclusive free-text string. We therefore apply
`commissionRate` to the whole net total, with no alustamaksu or palvelumaksu.
Ops calculate the true settlement by hand in Airtable. Manual is ~27% of
liikevaihto, so this is the largest source of error.

**Zero-commission orders may be missing.** May's settlement contained 16 orders at
`komissiokanta 0.00` (€11,138 pass-through, no take). No 0.00 rate appears in the
June/July API data, so if that rate is assigned during settlement rather than at
order time, API-derived take rates are slightly optimistic.

**Two price units in `charge`.** 297 platform orders (May–Jul 2026) return decimal
euros instead of integer cents. Any parser must branch on the presence of a
decimal point or revenue is understated — 10× for single-decimal values. See
`BACKOFFICE_API_ISSUES.md` item 2. **Now handled at the sync layer** (2026-08-24,
`parseChargeAmountCents` in Transform Orders) for `platform_fee`/`service_fee`/
`base_price_cents`/`services_price_cents`/`recycling_surcharge_cents` — downstream
consumers reading these columns no longer need their own branching logic. Re-checked the
proportion fresh against the last 2 months: **20.7%** of platform orders (201/973), well
above the original ~4% estimate — worth a look at `BACKOFFICE_API_ISSUES.md` #16
(NB-Palvelut bulk-order pattern), which shares several of the same fingerprints
(`platformFee`-cohort behavior, malformed `content`) and may be inflating this.

---

## Reference figures (excl. VAT, by sold date)

| | June 2026 actual | July 2026 forecast |
|---|---:|---:|
| Gigs | 550 | ~499 |
| Välitetty myynti | €75,551 | €81,947 |
| **Liikevaihto** | **€25,638** | **€25,934** |
| Kumppanille | €49,913 | €56,013 |
| Take rate | 33.9% | 31.6% |

July liikevaihto is flat (+1.2%) despite välitetty myynti rising 8.5%, because
manual grew from 23% to 32% of sales and carries a lower take.
