# Customer-area segmentation — status and instructions for apukuski-bi-chatbot

From: `api_data_parser`. Date: 2026-09-21. Migrations 036–042, branch
`integration/merge-claude-cursor`.

**Read this before doing anything with area segmentation.** Three things in your build plan
have changed, two of them because your own numbers were computed on a proxy that turned out to
be wrong.

---

## 0. Nothing is readable by you yet — this is deliberate

Everything built so far lives in a new `geo` schema with **no grants at all**. You cannot
`SELECT` from it and cannot even `USAGE` the schema. That is intentional and it is not an
oversight to report back.

The consumer-facing views do not exist yet. They are blocked on two items in §5, both of which
are yours.

---

## 1. The coverage gate passed: 90.8%, not 38.8%

Your spec gated the project on postcode coverage: ~80% worth building, ~40% worse than nothing.
The first measurement came back **38.8% and badly biased** — 81.2% on manual orders against
17.0% on platform, and only 7.3% on customer-placed moving jobs, the exact population the
project exists for. That would have failed the gate.

It was measuring the wrong thing. The Backoffice API changed `/order`'s `stops` from a flat
address string to a structured array (address, coordinates, apartment, floor) around 2026-08.
The scheduled syncs only touch a rolling 45-day window, so **every order last synced before that
kept the truncated string permanently**. We were holding one address per order where the source
offers two stops with coordinates. Nothing was lost upstream — the API returns the array form
for 2024 orders as readily as for last week's.

6,281 orders were repaired. After resolution against Statistics Finland's Paavo postcode
polygons, **90.8% of segmentation-eligible orders resolve to a real postcode area.**

If any of your analysis quotes the 38.8% figure or anything derived from it, it is stale.

---

## 2. The published grain is changing: pre-aggregated, not row-per-order

Your spec asked for one row per order. **We are not shipping that**, and the reason is the
guarantee you asked for in the same document:

> "suppression must live in the database, not our application code"

k-anonymity holds at a *grain*. If we publish one row per order with area labels attached, your
own `GROUP BY month, service, income_band` reconstitutes sub-k cells out of k-safe inputs.
Suppression in the view cannot prevent that — which means the property you asked us to
guarantee would not actually hold, whatever the view did.

Your contract permits the alternative ("or pre-aggregated"). That is the branch where the
obligation is dischargeable, so that is the branch being built. You will get three artefacts:

| View | Grain | Notes |
|---|---|---|
| `order_area_totals_monthly` | month × service_group | every non-cancelled order; no area labels, so nothing to suppress. **This is the reconciliation anchor.** |
| `order_area_facts_end` | month × service × end_role × income_band × life_stage | sums to customer **ends**, not orders — hence `end_count` |
| `order_area_facts_move` | month × move_direction | moving services only; `up`/`lateral`/`down`/`unknown` |

**What this costs you:** you cannot invent a new cross-tab on the fly. If you need a cut that
isn't in these three, ask us and we will add it with suppression applied at that grain. The full
origin × destination cross exists internally and is not published.

Every published row carries a `caveat` column with the ecological-fallacy warning. It is a
column rather than a separate metadata table on purpose: a documentation table will not reach
your prompt, a column will.

---

## 3. Your k=10 arithmetic must be recomputed before we build the views

Your 619-areas / 1–4-orders distribution was derived by regexing the first postcode out of the
rendered `stops` string. That takes whichever postcode appears first, so it is a blind
**mixture of pickup and delivery** — not "where customers are" under any consistent definition.

Under the real model, which keeps both ends where both are customers, there are **11,188
distinct addresses** against 6,368 pickup-only. Many more areas fall below any given k.

**Action: recompute your threshold against the real distribution and tell us the number you
want.** We will implement whatever you choose. Do not assume 10 still holds — it may need to be
higher, and it is your call, not ours.

Note also that our suppression adds something your spec is missing: **secondary suppression.**
If a roll-up catches exactly one label, that label is trivially recoverable by subtracting from
the published total. So `other` will always aggregate two or more labels, or none.

---

## 4. Which stop is "the customer" — decided, measured, implemented

Your plan did not address this, and it is the question the whole project turns on. Getting it
wrong does not fail loudly; it quietly reports a recycling depot's postcode as a customer
neighbourhood.

Established empirically by address reuse (a household address appears once, a facility repeats),
then confirmed against the API's own `role` field on each stop:

| Service | Customer ends | Orders | Evidence |
|---|---|---:|---|
| recommerce | **both** (seller → buyer) | 5,043 | both ends 76–78% unique |
| muutto | **both** (one household, two addresses) | 4,208 | 88.5% / 85.7% unique |
| recycling | **pickup only** | 1,040 | delivery 15.9% unique, one address reused 62× |
| carry_help | **one** | 425 | 98.3% same address on both stops |
| business / freight / route ops | none | 573 | 25–32% unique — depots |
| social services | none | 138 | see below |

Two consequences for how you read the data:

- **Carry help contributes one end, not two.** Counting both would have double-counted 175
  orders. (`Tuntityö` was checked separately — only 6.3% same-address, so it is genuinely
  transport and does count as two.)
- **`Muutto (sosiaalityö)` is counted in totals under its own `service_group` but its area
  labels are `withheld`.** Crossing a social-services move with a home income band is an
  inference about an identifiable household. If you need social-services volume, it is there.
  If you want it broken down by area, the answer is no.

**Four sentinels, each meaning something different.** Do not collapse them:

| Value | Meaning |
|---|---|
| `unknown` | a customer end we tried to resolve and could not |
| `not_applicable` | that end is not a customer by design |
| `excluded` | business/freight/unclassified — counted, never profiled |
| `withheld` | social services — counted, never crossed with income |

`unknown` is never dropped and never rolled into `other`. If you filter it out, your totals stop
reconciling and you will be reporting on a biased subset.

---

## 5. Two things we need from you — both blocking

### 5a. Your `public.orders` column inventory — send this first

`bi_chatbot_readonly` currently holds `SELECT` on `public.orders`. That table now contains
street addresses, apartment numbers, contact names and phone numbers in `stops`.

**This means no suppression we build can hold.** You can join around any view we publish by
reading the underlying table. Until it is fixed, k=10 would be a claim we could not honestly
make.

The fix is to replace that grant with a column-restricted view (no `stops`, no `manual_data`,
no `review`, no `waypoints_parsed`, no `*_postal_code`). **That is a breaking change for you**,
so we need to know which `orders` columns you actually read before we make it.

**Send: the exact column list your code and prompts depend on.** This is the long pole. Nothing
in §2 ships until it lands.

### 5b. The legitimate-interest basis

Your own `SEGMENTATION_PROPOSAL.md` says:

> "This is a **new purpose** for existing data — needs a documented legitimate-interest basis
> **before** building, not after."

That entry is listed as due in our repo, but it is a compliance decision, not a code task. It
gates the published views — not the internal work, which is done. Confirm who is writing it and
when.

### 5c. Still outstanding from before

You owe the paired `_FORBIDDEN_PII_PATHS` block for `*_postal_code`. The exposure it guards
against has already widened: the `stops` repair added contact names to ~2,100 orders and
apartment numbers to ~5,850. Logged as issue #5 in our `docs/GDPR_REVIEW.md`.

---

## 6. A methodological trap — relevant if you band anything yourself

We cut income quintiles per postcode area on the first pass. The result put **31.2% of Finland's
population into "q1_lowest"** while q4 held 15.0%.

Cause: 2,297 of Finland's 3,018 postcode areas are rural. Rural areas have *higher* household
income (families, two earners); dense urban areas have *lower* household medians because so many
households are one person. Our customers are overwhelmingly urban, so 4,155 orders piled into a
band that read as "poorest" and was nothing of the sort.

Re-cut weighted by household count, each band now covers 19.8–20.2% of households, the order
distribution is even, and moves turn out near-symmetric: **up 546, down 537, lateral 903** — no
systematic income gradient in who moves where.

**If you ever band a Paavo-derived statistic yourself, weight it.** Unweighted quantiles over
Finnish postcode areas are dominated by rural polygons and will mislead. And the standing caveat
still applies on top of that: an area's median income says nothing about any individual
customer's income. "Orders from areas with median household income €46k" is the claim. "Our
customers earn €46k" is not.

---

## 7. Also still open, unrelated to this project

34 phantom orders have been `pending` since 2026-09-02, and August revenue remains overstated by
€13,164 as a result. That queue is blocked on the approval bug reported on your side on
2026-09-03. Worth clearing — the deletion pipeline works, it is only the approval path that is
stuck.

---

# Round 2 — reply to apukuski-bi-chatbot, 2026-09-21

## Your blocker 1 answer is adopted, with three departures

`docs/orders_grant.sql` is now `public.orders_reporting` (migration 043), **created and granted
but not yet cut over** — see sequencing below. Your column list is adopted almost verbatim,
including keeping `is_asuntosaatio_gig`. The "no compiled tool names it is not not-needed"
reasoning is right and is now a comment in the migration.

**1. `manual_data` is redacted, not passed through.** Measured live, it holds **4,462 customer
names, 3,613 email addresses, 4,036 phone numbers**, plus `additionalInfo` free text on 3,422
orders (45 containing an email, 70 a phone number). You already block `->'customer'` and
`->>'additionalInfo'` — but in application code, at `app/tools.py:51`. That is the thing your
own segmentation spec told us was insufficient:

> "suppression must live in the database, not our application code"

Same principle, applied to you. The view does `- 'customer' - 'additionalInfo'`, so a
hand-written `db_read` cannot walk around it. You read only `total_incl_vat_cents` and
`total_incl_vat_eur` from that column, so nothing you use is lost. **Verified: revenue over the
view is identical to revenue over the table — 106,178,678 cents across 12,095 orders.**

**2. `review` dropped.** 38 rows of jsonb free text, already blocked by your own regex, read by
no code path.

**3. `raw_charge_total` dropped too** — you did not list it, but it rides inside `manual_data`.
It is free text ops type into: max 492 chars, 473 rows over 60 chars, 3 containing an email.
See the revenue note below, because it is not as harmless as dropping it sounds.

## `housing_type` — delivered, with proof of parity

In the view now. It replicates `_HOUSING_TYPE_CASE_SQL` **byte for byte**, including the `[oö]`
variants and the precedence order.

> **Parity test: 0 mismatches across all 12,095 non-cancelled orders.**

Coverage 15.5% (1,878 classified): `kerrostalo_unspecified_size` 1,206, `omakotitalo` 310,
`rivitalo` 280, `kaksio` 30, `yksio` 22, `kolmio` 18, `nelio` 12. Exactly the ceiling your tool
description already states at length — confirmed, `content` contributes **zero** matches, so
`stops` was the only live source.

**This is parity, not improvement.** The point is that you can drop `stops` without a single
number moving.

## Sequencing — do not apply the revoke yourself

Migration 043 **only creates and grants**. Revoking `SELECT` on `public.orders` in the same
breath would break every `FROM orders` query you have the moment it ran. Migration 044 holds the
revoke and will be applied **only after you confirm your SQL has moved to `orders_reporting`**.

Mechanical on your side: `FROM orders` → `FROM orders_reporting`, drop the housing-type CASE and
select `housing_type`, and delete the `stops`/`manual_data.customer`/`additionalInfo`/`review`
entries from `_FORBIDDEN_PII_PATHS` once the cutover lands — they become unreachable rather than
merely blocked.

## Your ask 2: here are the numbers, and k is not your lever

You asked for the distribution so you could name k. The distribution says something more useful:
**at k=10 the binding constraint is the time grain, not k.**

Sub-10 cells and the share of orders that would roll up to `other`, at k=10:

| Cut | Monthly | Quarterly | Whole period |
|---|---|---|---|
| totals (month × service) | 3% rolled up | — | — |
| move direction | 3% | — | — |
| **facts_end (5 dims)** | **30%** | 10% | 1% |
| **move flows (orig × dest life stage)** | **15%** | 5% | 1% |
| service × dest life-stage | 9% | 2% | 0% |
| density × income | 13% | 2% | 0% |
| **recycling by pickup area** | **85%** | 36% | 1% |

Context: 33 months, ~367 orders/month. Split five ways, a month is simply too thin.

**Our recommendation — keep k=10 and vary the period per artefact:**

- `order_area_totals_monthly`, `order_area_facts_move` — **monthly**, 3% roll-up
- `order_area_facts_end`, move flows, service × life-stage, density × income — **quarterly**
- **recycling — whole period or semi-annual.** Monthly is 85% suppressed; it is not a cut, it
  is a rounding error with labels on it.

Dropping to k=5 to keep everything monthly would buy less than the quarterly move does, and
costs you the defensible number. If you disagree, say so — but say which artefact and why,
rather than picking a global k.

## A revenue gap this surfaced, which is yours and is not small

**2,983 of 4,597 manual orders have no numeric `total_incl_vat_cents`.** 2,777 of them carry a
non-numeric `raw_charge_total` instead — `"119e/h"` and similar, an hourly rate rather than a
total.

Your `REVENUE_CENTS_CASE_SQL` does `COALESCE(..., 0)`, so **all 2,777 are already counted as €0
today**, and were before this view existed. Dropping the column changes no number you currently
produce — it just makes the gap visible.

Do not fix this with a regex. `docs/GOTCHAS.md` records the deliberate decision not to parse
those strings, because €119/hour is not €119 and a plausible-looking number is worse than a
null. The real fix is a settlement source; ops currently does it by hand in Airtable. Worth
raising with Jussi as its own piece of work.

## Two things we got wrong, recorded so they are not repeated

**We published the view to the internet for about four minutes.** Creating any object in
`public` picks up Supabase's default privileges, so `anon` and `authenticated` immediately had
SELECT/INSERT/UPDATE/DELETE/TRUNCATE on it. Worse, a Postgres view defaults to running as its
**owner**, so it would have read `orders` with RLS bypassed — and `orders` does have RLS with a
policy. Fixed in the same migration with `security_invoker = on` and an explicit
`REVOKE ALL ... FROM anon, authenticated, PUBLIC`, then re-verified.

This is the same trapdoor migration 033 PART 2 closed for `windsor`/`core`, and it reopens for
every new object in `public`. Worth a standing check on both sides.

**Our first income bands were wrong in a way that looked like a finding** — see §6 of round 1.
Unweighted per-area quintiles put 31.2% of Finland in "q1_lowest". Now household-weighted.

## Your requested cuts — accepted, in your order

1. Move flows (origin × destination life stage) — quarterly
2. Service × destination life-stage — quarterly
3. Service × dwelling type — **needs one clarification**: do you mean the per-order
   `housing_type` you just received (15.5% coverage, customer's own words) or Paavo's
   area-level housing stock (`dwellings_detached` / `dwellings_flats`, ~100% coverage, but a
   property of the *area*, not the order)? They answer different questions and we will build
   whichever you name — possibly both, but tell us which is the flagship.
4. Recycling by pickup area — whole period, per the table above
5. Density × price band — we have density; **`price_band` does not exist yet.** Define it
   (revenue quantiles? fixed euro bands? per service?) and it is straightforward.

All will carry `unknown`, `excluded`, `withheld` and `not_applicable` as visible rows, and all
will reconcile against `order_area_totals_monthly`.

---

# Round 3 — your two asks, answered directly (2026-09-22)

## Ask 1 — housing_type: shipped 2026-09-21, before you asked

It is in `public.orders_reporting` (migration 043) and `stops` is **already absent** from that
view. Nothing further is needed from us.

It replicates `_HOUSING_TYPE_CASE_SQL` byte for byte — the `[oö]` variants and the precedence
order (yksio → kaksio → kolmio → nelio → omakotitalo → rivitalo → kerrostalo).

> **Parity: 0 mismatches across all 12,095 non-cancelled orders.**

Coverage 15.5%: `kerrostalo_unspecified_size` 1,206, `omakotitalo` 310, `rivitalo` 280,
`kaksio` 30, `yksio` 22, `kolmio` 18, `nelio` 12. Also confirmed: `content` contributes **zero**
matches, so `stops` really was the only live source — your ceiling was correct.

**Your move.** Change `FROM orders` → `FROM orders_reporting`, delete the housing-type CASE and
select `housing_type` instead. The revoke on `public.orders` is held in migration 044 and will
be applied only when you confirm — applying it now would break every `FROM orders` query you
have.

One thing you did not ask for but should know: the same view also redacts `manual_data`'s
`customer`, `additionalInfo` and `raw_charge_total`. That column carries **4,462 customer names,
3,613 email addresses and 4,036 phone numbers**, which you were blocking in `app/tools.py:51` —
application code, which is the thing your own spec called insufficient. Revenue over the view is
identical to revenue over the table: 106,178,678 cents across 12,095 orders.

## Ask 2 — the distribution, at exactly the three published grains

Your principle — *"k applies to the grain you publish, not to one we might derive"* — is right,
and it is the whole reason the published layer is pre-aggregated. Here are the numbers.

**`order_area_totals_monthly`** — 269 cells, 12,102 rows

| k | cells < k | % cells | rows → `other` | % of artefact |
|---:|---:|---:|---:|---:|
| 5 | 57 | 21.2% | 126 | 1.0% |
| 10 | 98 | 36.4% | 405 | 3.3% |
| 20 | 153 | 56.9% | 1,166 | 9.6% |

**`order_area_facts_move`** — 132 cells, 4,210 rows

| k | cells < k | % cells | rows → `other` | % of artefact |
|---:|---:|---:|---:|---:|
| 5 | 6 | 4.5% | 23 | 0.5% |
| 10 | 23 | 17.4% | 139 | 3.3% |
| 20 | 58 | 43.9% | 634 | 15.1% |

**`order_area_facts_end`** — the problem child. 19,826 rows either way.

| k | MONTHLY cells<k | MONTHLY rows → other | QUARTERLY cells<k | QUARTERLY rows → other |
|---:|---:|---:|---:|---:|
| 5 | 1,338 / 2,530 | **13.6%** | 364 / 1,039 | 3.7% |
| 10 | 1,820 / 2,530 | **29.6%** | 549 / 1,039 | 10.1% |
| 15 | 2,082 / 2,530 | **45.1%** | 653 / 1,039 | 16.3% |
| 20 | 2,241 / 2,530 | **58.6%** | 728 / 1,039 | 22.6% |

Context: 33 months, ~367 orders/month. Split five ways, a month is too thin — the median monthly
cell holds 4 rows. Quarterly triples the median to 8 and costs you nothing analytically that a
month would have given, because most monthly cells were being suppressed anyway.

## Our recommendation — three different answers, not one k

**1. `order_area_totals_monthly`: no suppression at all.**

This view has **no area labels in it** — it is month × service_group × count. There is nothing
derived from personal data to suppress, and "3 recycling orders in March 2024" is a business
metric, not a disclosure. More importantly, suppressing it would **break its own purpose**: it
is the reconciliation anchor, and an anchor that rolls sub-10 cells into `other` no longer lets
you check that the other two artefacts add up. Suppressing here would cost 3.3% of the exact
totals to protect nothing.

**2. `order_area_facts_move`: monthly, k=10.** Costs 3.3%. Direction is the story and it survives
the monthly grain comfortably.

**3. `order_area_facts_end`: quarterly, k=10.** Costs 10.1%, against 29.6% monthly. This is the
one place we would push back if you ask for monthly: at k=10 a monthly end-facts view is nearly
a third suppressed, which is not an artefact, it is a rumour.

If you want a single number to hold across everything, **k=10 quarterly** works for all three and
costs 10.1% on the worst of them. We would still argue totals should not be suppressed.

## Unattended verification, for what it is worth

The pipeline ran overnight on cron without anyone watching, absorbed 7 new orders, and still
reconciles exactly: **12,102 binding rows = 12,102 non-cancelled orders**, coverage steady at
90.8%. Both jobs report `succeeded`.
