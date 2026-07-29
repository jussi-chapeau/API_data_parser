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
`BACKOFFICE_API_ISSUES.md` item 2.

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
