# Changelog

One entry per deploy to production — date, what shipped, why it matters. Detail in `git log`.

**Versioning started 2026-08-08.** This project (`api_data_parser`: AWS Backoffice API →
Supabase → Lovable/BI) is versioned independently from the **BI Chatbot** project — they
ship on different schedules from different repos. When announcing a release anywhere shared
(Slack, the BI bot channel), always name **which project** shipped: "api_data_parser
vX.Y.Z" vs. "BI Chatbot vX.Y.Z". Scheme (SemVer-ish, no public API so read loosely):
- **MAJOR** — a schema or behavior change downstream consumers (BI Chatbot, Lovable) must
  react to (new required filter, changed column meaning, breaking migration).
- **MINOR** — new data source, new column, new capability; backward compatible.
- **PATCH** — bug fix, backfill, ops/credential change; no new capability.

Versions before 2026-08-08 are reconstructed retroactively from `git log` for continuity,
not tagged at the time.

## v1.13.0 — 2026-09-23

- **Adopted the Backoffice `/route` contract** — spec stored at
  `docs/api/BACKOFFICE_ROUTE_ENDPOINT.md`, migration 056, `scripts/sync_routes.py`.
  - **`/route` is no longer blocked.** `CLAUDE.md` and `GOTCHAS.md` both recorded it as dead on
    an upstream Lambda timeout. Measured: 1 day 1.1 s, 8 days 0.2 s, **30 days 0.2 s, zero
    failures**. The backend fixed it at some point without anyone here noticing, so the sync
    stayed written off. Both docs corrected.
  - **The unit trap it leaves behind is worse, because it is silent.** `internalCost` is
    **EUROS** while `totalSales` is **CENTS** — the only euro field on the API. Proven two ways:
    the values carry decimals (3985.97, 786.3) so they cannot be integer cents, and read as
    euros the cost is 82% of sales, which is a plausible partner cost, while read as cents it is
    0.8%, which is not. `routes.internal_cost` was an `integer` with no unit in its name sitting
    next to cents, so subtracting them was wrong by 100× *and* the integer type rounded the
    decimals away (2515.43 € stored as 2515). Now stored as `internal_cost_cents`.
  - **Two corrections sent back to the backend team.** Their spec says "the Supabase routes table
    holds 0 rows" and uses that to argue dropping `internalCost` is safe — it holds **25 rows**.
    And neither `internalCostCents` nor `?include=stops,orders` is in production yet; `include`
    returns 400. The sync reads **both** contract shapes and records which it saw in
    `contract_version`, so the cutover will be observable rather than assumed.
  - **`anon` and `authenticated` held INSERT/UPDATE/DELETE/TRUNCATE on `public.routes`** — the
    `public`-schema default-privilege trapdoor for the **fourth** time this week (033, 043, 052,
    now 056). Revoked.
  - `public.route_economics` published to the BI role, carrying a `route_cost_caveat`: internal
    cost is free text staff copy from a partner invoice with **VAT status unverified**, so the
    difference against VAT-inclusive `total_sales_cents` is not a margin. No per-order cost
    allocation — the backend declined to invent a rule and neither does this repo.
  - Backfilled 25 routes, 2026-08-05 → 2026-09-21.

## v1.12.0 — 2026-09-22

- **GA4 `conversions` is not a conversion count, and now says so** (migration 045). The BI bot
  reported "2,604 sessions, 19,076 conversions" for Google Ads — 7.3 per session — faithfully
  from data whose underlying metric is meaningless. Measured over 35 days site-wide: 49,511
  "conversions", of which **`add_to_cart` 49,447 and `session_start` 8,316**, both marked as GA4
  key events. `session_start` as a key event makes the metric circular; `add_to_cart` at 5.9 per
  session is the price calculator, not carts. Meanwhile **`purchase` fired 4 times against 538
  real orders**. Every source reads 7–9 per session *including direct*, which is the tell.
  - Added a `conversions_caveat` column to `analytics_ga_daily_source` and
    `analytics_ga_daily_totals`. A column, not a `COMMENT`, because the consumer is an LLM whose
    column descriptions come from a hardcoded dict in its own repo — a database comment would
    never reach its prompt. Columns appended, so no existing ordinal moves.
  - The real fix is unmarking those key events in the GA4 admin UI; no migration can do it.
  - Also found: key events named `tarjouspyyntö_muutolle_lomake_lähetys`, `ph_one_lead`,
    `varaus__sivu` and `oppaan_lataus_vahvistus` are **configured but have never fired** — the
    lead tracking this business would actually want is absent.
- **Landing-page feeds** (migration 046) — `windsor`/`core`/`public` for two new grains:
  `analytics_ga_daily_landing` (where paid sessions actually land, with `begin_checkout_count`)
  and `ads_google_landing_daily` (where campaigns are configured to point). Separate tables
  because landing page is a dimension: adding it to an existing table would change its grain and
  silently multiply every metric. `Landing page`, not `+ query string`, to keep gclid/UTM noise
  out of the key — and Windsor caps "Columns to Match" at 3, which both keys use exactly.
  Tables created by us and handed to `windsor_writer` per migration 016. **Inert until two
  Windsor destination tasks are created;** field mapping is documented in the migration header.
- **Customer feedback synced from Airtable** (migration 049, `scripts/sync_airtable_feedback.py`).
  1,344 records from `Palautteet`. The table the link pointed at (`Feedback`) is empty — it has
  an OrderID field and looks like the intended schema, but nothing was ever written to it, so
  **feedback has no order key and cannot be joined to `orders`**: analysable by date, partner and
  city only. Email dropped at ingest (1,342 of 1,344 records carried one); free text scrubbed,
  which applied exactly 1 redaction across the whole table — matching the pre-build measurement.
  281 test rows (`Testi lähetys…`, half of all filled Partner values) excluded via a lookup
  table, and spelling variants collapsed (ELD/Eld Muutot Oy, Vilhi/Vilhi Oy, Saarela ±Oy).
  Ships a `feedback_caveat` column because the response bias is the real trap: **CSAT is 5 for
  88% of responses and only 57 sit below 4**, so a mean of 4.8 measures who answered, not how
  the service went. Writes through a SECURITY DEFINER RPC rather than exposing `core` to
  PostgREST, revoked from anon/authenticated.
  Notable for marketing: `Mistä löysit meidät?` gives **self-reported attribution** — Google 578,
  word-of-mouth 80, Muuttomaailma 75, repeat customers 51. Word-of-mouth plus repeat is ~12% of
  respondents, which GA4 structurally cannot attribute.
- **GA4 age/gender feeds** (migration 048), chosen over Google Ads demographics on measured
  coverage, not preference: Google Ads' demographic views carry only **€1,044 of €9,504 spend
  (11.0%)** and 49.1% of those clicks are `UNDETERMINED` — about 5.6% effective. GA4 covers
  **92.0% of sessions** with 33.4% resolving to a real bucket, roughly six times better.
  - **Age and gender are separate pulls, not crossed.** A single crossed table would have fit
    Windsor's 3-column match cap exactly and saved a task slot, but testing it showed coverage
    falling from 92% to 70.8%, only 17.0% fully known, and **the 35-44 band suppressed
    entirely** by GA4's own thresholding. Not worth one slot.
  - Long-format view (`dimension`/`bucket`) so `unknown` — 67% of age, 60% of gender — is a row
    that cannot be dropped by accident rather than a missing column. Carries a
    `demographic_caveat`: GA4 only resolves users signed in to Google with ad personalisation
    on, so the known remainder skews older and more Android. The apparent age profile (55-64 and
    65+ are 57% of *known* ages, for a moving company) most likely describes who Google can
    identify, not who moves house — the same trap as the unweighted income quintiles in 042.
  - Inert until two Windsor tasks exist; field mapping is in the migration header.
- **Schema-contract drift check now derives its own scope** (migration 047). It had a hardcoded
  array of five view names inside the view body while expectations lived in
  `core.schema_contract` — so registering a new view produced 19 false "COLUMN REMOVED" rows,
  and, far worse, **a registered-but-unlisted view would have been silently unchecked while the
  monitor reported clean.** Same failure shape as `analytics_freshness` monitoring the wrong
  table after a rename (025, then 026 again). Scope is now `SELECT DISTINCT view_name FROM
  core.schema_contract`, so registering a view is the only step needed to monitor it. 7 views
  covered, up from 5; verified with a canary that it still detects a real break.

## v1.11.0 — 2026-09-21

- **Repaired `orders.stops` for 6,281 orders.** The Backoffice API changed `/order`'s `stops`
  from a flat address string to a structured array (address, coordinates, apartment, floor)
  around 2026-08, but the scheduled syncs only touch a rolling 45-day window — so every order
  last synced before the change kept the truncated string permanently. Nothing was lost at
  source; the API still returns the array form for 2024 orders. `scripts/repair_stops_structure.py`
  refreshes `stops` only, day by day with backoff, and only ever widens (array from API, and not
  already an array) so an unreadable day is skipped rather than blanking a row.
  **This is why postcode coverage was measured at 38.8% and is actually 90.4%** — we were
  measuring what we had stored, not what the source offers.
- **Added Statistics Finland (Paavo) as a reference source** — migration 036 and
  `scripts/load_paavo.py`. 3,018 postcode areas with geometry and area-level statistics, loaded
  into a new `geo` schema. PostGIS 3.3.7 enabled in `extensions`, not `public`, to avoid
  widening the PostgREST surface by ~1,000 functions.
  - **`-1` is Paavo's "withheld" marker, not a missing value** — 74 areas for personal median
    income, 188 for household. Loaded verbatim they would have sorted into the *bottom income
    band* and been reported as our poorest customer areas. Mapped to NULL and flagged
    `paavo_suppressed`; 0 is left alone because 17 areas really are uninhabited.
  - `geo` is deliberately **outside `core`**: migration 022's `ALTER DEFAULT PRIVILEGES` grants
    `bi_chatbot_readonly` SELECT on any table added to `core`, with no grant line in the
    migration for a reviewer to spot. Verified after loading that `geo` has no grants beyond
    `postgres`.
  - Verified: 3,018 areas, 0 invalid geometries, 0 missing geometry, CRS read from the WFS
    response rather than assumed, and a point-in-polygon test resolving Helsinki central station
    to 00100.
- **Service taxonomy as data** (migrations 037–038). Platform orders use 8 tidy `order_type`
  values; manual orders are free text with 811 distinct ones. Classification lives in
  `geo.service_rule` (first match by priority, catch-all at 9999) with one review row per
  *distinct string* rather than per order, so a correction is an INSERT and not a migration.
  Reading the real tail rather than guessing at it cut unclassified from 607 orders to **292,
  190 of which have an empty `order_type`** — deliberately left unclassified, along with driver
  availability notes and test junk, rather than absorbed into a bucket that looks resolved.
- **Stop resolution** (migration 039). `geo.stop_resolution` stores a postal code and a salted
  address hash per stop — no coordinates, no address text, no contact details. Platform stops
  resolve by point-in-polygon; manual orders fall back to a 5-digit postcode validated against
  the Paavo postcode universe. Uses the API's own `role` field (`pickup`/`delivery`) rather than
  inferring from array position, and `ST_Intersects` rather than `ST_Contains` so addresses on a
  postcode boundary don't silently become `unknown`.
- **Facility detection by address frequency** (migration 040). The top reused addresses are used
  91, 76 and 73 times, delivery-only, across two or three services — which per-service rules
  miss and frequency catches. Threshold set at 11 uses (32 addresses, ~4% of stops), deliberately
  conservative: an attempt to separate depots from large apartment blocks by counting distinct
  apartment values **was measured and did not discriminate**, so the ambiguous 6–10 band is left
  in rather than discarding real customers.
- **Per-order binding** (migration 041) with four distinct sentinels — `unknown` (tried, failed),
  `not_applicable` (that end isn't a customer), `excluded` (business/freight), `withheld`
  (social-services moves, counted but never crossed with an income band). Ends are deduplicated
  on address, so carry help (98.3% same address on both stops) contributes one customer end
  without a special case. **12,095 binding rows = 12,095 non-cancelled orders**, exactly.
- **Income bands fixed to be household-weighted** (migration 042). The first pass cut quintiles
  per *area*, which put **31.2% of Finland's population in "q1_lowest"** — 2,297 of 3,018 Paavo
  areas are rural, and rural areas have higher *household* income while dense urban areas look
  poor because so many households are one person. Our customers are urban, so 4,155 orders piled
  into a band mislabelled as poor. Caught by the output looking implausible, not by review.
  Re-cut weighted by household count (each band now 19.8–20.2% of households), the distribution
  is even and the move vector is near-symmetric (up 546 / down 537 / lateral 903).

**Coverage: 90.8% of segmentation-eligible orders now resolve to a real postcode area** —
against the 38.8% first measured, which was measuring what we had stored rather than what the
source offers.

- **`public.orders_reporting`** (migration 043) — narrows what `bi_chatbot_readonly` may read,
  built from the BI repo's own column audit. Created and granted; the revoke on `public.orders`
  is held back to migration 044 so the cutover doesn't break every `FROM orders` query at once.
  - **`manual_data` is redacted in the database**, not by application regex: it holds 4,462
    customer names, 3,613 emails and 4,036 phone numbers. The BI repo already blocked those
    paths in `app/tools.py`, which is exactly what their own spec called insufficient. Revenue
    over the view is identical to revenue over the table — 106,178,678 cents, 12,095 orders.
  - **`stops` replaced by a `housing_type` label**, which was their ask and the best trade here:
    it removes street addresses from that service's reach entirely. Replicates their
    `_HOUSING_TYPE_CASE_SQL` byte for byte — **0 mismatches across all 12,095 orders**, same
    15.5% coverage ceiling.
  - **Caught and fixed a hole we opened doing it:** creating any object in `public` inherits
    Supabase's default privileges, so `anon` and `authenticated` immediately had full DML on the
    view — and a Postgres view defaults to running as its *owner*, which would have read `orders`
    with RLS bypassed. Fixed with `security_invoker = on` and an explicit `REVOKE ALL`. Same
    trapdoor migration 033 closed for `windsor`/`core`; it reopens for every new `public` object.
- **Suppression sizing:** at k=10 the binding constraint turns out to be the time grain, not k.
  Monthly rolls up 30% of the five-dimension end facts and 85% of recycling; quarterly gives 10%
  and 36%; whole-period ~1%. Recommendation to BI is to keep k=10 and vary the period per
  artefact rather than lower k.

- No consumer-facing change yet. The published segmentation views remain gated on a documented
  legitimate-interest basis (now confirmed by Jussi and recorded in the BI repo) and on the
  migration 044 cutover — see `docs/GDPR_REVIEW.md` and `docs/SEGMENTATION_HANDOVER_TO_BI.md`.

## v1.10.0 — 2026-09-17

- **Fixed five column types the migration silently changed**, reported by apukuski-bi-chatbot.
  `analytics_ga_daily_source.engaged_sessions`, `ads_google_campaign_daily.impressions/clicks`
  and `ads_meta_placement_daily.impressions/link_clicks` had widened `integer` → `numeric`,
  breaking casting code downstream. Cause: Windsor staging is numeric throughout and a
  `UNION ALL` resolves to the wider type; I cast some columns and missed these. **The cutover
  was verified on names, order and row counts — not types**, which is exactly the gap a by-eye
  check leaves.
- **Added `public.schema_contract_drift`** so this class of break is detectable. Compares every
  contract view against a frozen record of column name, ordinal position and type
  (`core.schema_contract`). Empty = contract holds. Freshness monitoring could never have
  caught this: the data was fresh, only the shape changed.
- **GA4 conversion-type breakdown live** (migrations 032–034) via an N8N pull from Windsor's
  connector API, since the plan caps destination tasks at 5 and all five are in use. It shows
  **95% of "conversions" are `add_to_cart` (44,112) and `session_start` (7,329)**, both flagged
  as key events; genuine intent events total ~757. The fix is in GA4's config, not the
  pipeline.
- **Revoked stray `anon`/`authenticated` grants** on the `windsor` and `core` schemas — 7 tables
  each. Unusable today (no schema USAGE) but RLS is deliberately off on those tables, so one
  config change away from public exposure.

## v1.9.0 — 2026-09-16

- **GA4 source + geo restored** (migrations 028–031), bringing back `bi_website_report`'s
  breakdowns and the dashboard traffic-source chart, dead since 2026-08-10. **The Supermetrics
  migration is now complete** — five feeds live on Windsor, all three Supermetrics workflows
  off, everything reading through the durable `core` layer.
- **Geo validated exact** against legacy for March–July (3,217 / 5,492 / 6,248 / 6,020 /
  6,304 sessions). Needed two shape changes: `region` dropped (Windsor caps match columns at
  3, and an unrequested dimension is aggregated away rather than colliding on GA4's `(not set)`
  bucket), and the key reduced to `(date, city, country)`.
- **Source needed a dimension change, and it shifts the numbers.** GA4 rejects
  attribution-scoped `source_medium` alongside session metrics, so the feed uses
  `session_source_medium` — which reads **~11% lower**. Boundary deliberately placed at
  2026-08-10/11, where the old sync died, so the change coincides with the feed change rather
  than creating a step mid-history. Documented on the view.
- **Corrected the handover's GA4 purchase finding.** It reported 1 purchase against 254 orders
  (0.4%) and concluded tagging was broken — but that came from `conversions_purchase`, which
  isn't a real GA4 field and returns zeros. With valid fields: **148 against 3,052 = 4.8%**.
  Still too sparse to build a funnel on, so the practical conclusion holds; the magnitude
  didn't.
- Windsor API key (exposed in screenshots 09-14) rotated.

## v1.8.0 — 2026-09-16

- **Meta cut over to Windsor** (migration 026). Boundary is **2026-03-31, not 03-16**, and the
  reason matters: Windsor's Meta feed is exact for April, May, August and September, but March
  was short by exactly €146.06 / 42,524 impressions — precisely the "Waitlist" campaign, which
  ran 2026-03-05..03-30 and appears nowhere in Windsor. That's Meta's Marketing API excluding
  deleted campaigns, not a config error; the day after it stopped, both feeds agree exactly.
  Cutting at 03-16 would have silently erased €146 of real spend. **Windsor under-reports any
  historical period containing since-deleted campaigns — legacy is the only record.**
  Supermetrics workflow `vuQOMC0tnTaZkMTC` deactivated. All three Supermetrics syncs are now
  off.
- **Fixed a live double-counting defect I introduced** (migration 027). Each contract view is
  `legacy WHERE date <= B` UNION ALL `core` — but only the legacy half was bounded. That was
  accidentally safe until core accumulated earlier history than Windsor originally had, at
  which point the halves overlapped: **93 duplicated dates in `analytics_ga_daily_totals`, 98
  duplicated keys in `ads_meta_placement_daily`**, double-counting every SUM over the affected
  range. Caught because Meta's 2026-03-31 spend read €72.02 against a true €36.01. Both sides
  of all three unions are now explicitly bounded; duplicates verified zero on the natural key.
- **The rename/OID trap struck a second time** — renaming the Meta legacy table silently
  repointed `analytics_freshness` at it again, exactly as migration 025 warned. Rebound.

## v1.7.0 — 2026-09-16

- **Durable `core` layer between Windsor and reporting** (migrations 022/023). Windsor treats
  its destination tables as a rolling window it owns — it deletes rows outside
  `Backfill data for`, replaces rows wholesale, and rewrites whatever table a task points at.
  With the contract views reading staging directly, a misconfigured task **deleted a month of
  production GA4 data** (2026-08-17..09-15) earlier the same day; it was recoverable only
  because a snapshot had been taken speculatively that morning.
  Now: `windsor.*` (volatile) → `core.*` (**merge-only, never deleted**) → `public.*` views,
  refreshed every 30 min by pg_cron. Both safety properties were tested live rather than
  assumed: deleting a row from staging leaves core intact, and a NULL from staging does not
  overwrite a real value (`COALESCE(EXCLUDED.col, core.col)` — which matters because a
  narrowed field list silently sends NULLs). The view switch was invisible to the consumer:
  715 rows, zero gaps, every spot value unchanged.
- **All three Windsor sources live** — GA4 (#564), Google Ads (#577), Meta (#578), each to its
  own table. Along the way: two duplicate GA4 tasks, both ad tasks silently pointed at the GA4
  table, and Windsor's undocumented **3-column cap on Columns to Match**, which forced Meta
  from placement grain down to `(date, campaign_id, publisher_platform)` — keeping the
  Facebook/Instagram split, dropping position/device detail that nothing reports on
  (migration 021).
- **Google Ads cut over to Windsor** (migration 024) after validating **six complete months
  exact** on spend, clicks and impressions — March €2,377.42, April €5,027.19, May €7,659.81,
  June €7,806.23, July €6,472.26, August €9,080.44. `ads_google_campaign_daily` is now a view
  (legacy ≤ 2026-03-15 + core ≥ 03-16): 4,947 rows, 2024-08-05..2026-09-16, no duplicate
  keys. Supermetrics workflow `CZbzvcmagNxvC1JN` deactivated.
- **Fixed a silent monitoring failure I introduced in the same change** (migration 025).
  Postgres views bind to object OIDs, not names, so renaming the legacy table silently
  repointed `analytics_freshness` at the frozen legacy table — it would have reported the same
  date forever and never alerted. Caught via a one-day discrepancy. Documented in GOTCHAS:
  after any rename-and-replace-with-a-view, recreate every dependent view.

## v1.6.0 — 2026-09-14

- **Ended a five-week GA4 reporting outage.** The GA4 sync stopped producing new data on
  2026-08-10; every nightly run still reported `success`. `public.analytics_ga_daily_totals`
  is now a **view** stitching Supermetrics history (587 days, ≤ 2026-08-10) to the live
  Windsor.ai feed (≥ 2026-08-15) — 617 rows through 2026-09-13, **zero code change in
  apukuski-bi-chatbot**, which keeps reading the same table name. Verified by connecting as
  the real `bi_chatbot_readonly` role, not just by checking grants.
  `supabase/migrations/014_ga4_windsor_cutover.sql`.
- **Deactivated `Analytics GA Daily Sync` (j4gatZqXaw9tk55x)** — first step of dropping
  Supermetrics. It wrote the table that is now a view; a `UNION ALL` view is not
  auto-updatable, so leaving it active would have turned a silent no-op into a nightly
  failure.
- **New `Data Freshness Watchdog` (G0y7frdNMt3EU9cL), daily 07:00.** Alerts on `MAX(date)` —
  how recent the *data* is — not on `synced_at` or job exit status. **This is the fix for the
  root cause, not the symptom:** the Supermetrics job re-stamped `synced_at` on old rows
  nightly, so the BI bot's staleness warning reported "fresh" for 35 days. Live-tested: it
  correctly flagged Meta as 12 days stale. New `public.analytics_freshness` view exposes
  `data_age_days` alongside `false_freshness_days` so the gap stays visible.
- **GA4 feed fully restored and validated.** Backfilled Windsor to 2026-07-01 (75 days, no
  gaps), which closed the 08-11..08-14 hole, replaced the partial 08-10 row (7 sessions ->
  236), and created the first overlap between the old and new feeds. **Validated on 40
  overlapping days: sessions diverge 0.02%, users 0.00%** — Windsor reproduces Supermetrics.
  View boundary moved to 2026-06-30 (migration 017); contract view now 622 rows,
  2025-01-01..2026-09-14, zero gaps, verified as `bi_chatbot_readonly`.
- **`windsor_writer` now owns its staging tables** (migration 016). Windsor issues
  `ALTER TABLE ... ADD COLUMN` for unmatched fields, which needs ownership — without it a
  single wrong field name silently took the whole feed down in a retry loop while the vendor
  UI reported "Connection successful".
- **Fixed two real bugs in `scripts/apply_supabase_migration.py`'s statement splitter**,
  both found while applying 014: a semicolon inside a single-quoted string (a `COMMENT` body)
  split a statement in half; and the fix for that initially broke apostrophes in inline `--`
  comments, which collapsed migration 011 from 10 statements to 1. Both covered by cases now,
  regression-checked against all 14 migrations.

## v1.5.0 — 2026-09-03

- **`orders-reconcile` now reconciles both directions.** It only ever detected surplus rows
  in Supabase; nothing watched for rows *missing* from Supabase. That blind spot let one May
  2026 order sit missing for three months, because a count-based check reported "difference
  of 1" which reads as ordinary sync lag. Backoffice-only rows are now re-fetched and
  upserted immediately — additive, so no approval gate; gating it would recreate the exact
  "nobody noticed" failure. Supabase-only rows still require human approval, unchanged. Both
  counts are logged every run so zero-drift is visible rather than assumed — via a new
  `sync_log.details` JSONB column (migration 013), *not* `error_message`, which stays
  reserved for real failures. An abort stops both directions.
- **Backfilled the one missing May order** (`e8578d18…`, 2026-05-24), through the new repair
  path rather than a one-off script.
- **New `scripts/verify_order_parity.py`** — per-month ID-level diff. Replaces
  count-comparison as the parity check, since counts cannot say which row differs or in which
  direction.
- **New `scripts/order_transform.py`** — shared transform so repaired rows are byte-identical
  to normally-synced ones. Note this is now the third copy of that logic (Python module, the
  four sync workflows' `Transform Orders`, and `Reconcile`); `tests/test_order_transform.py`
  exists specifically to catch divergence between them.
- Tests: 42 passing (was 22).

## v1.4.1 — 2026-09-02

- **Fixed: revoking a deletion approval was silently ignored.** `orders_delete_approvals` is
  append-only for the BI bot by design, so a revocation is a later contradicting row rather
  than an edit — but `delete_approved_orders.py` treated "an approval row exists" as
  authority and would delete anyway. Reachable through ordinary use (recording an approval
  does not clear the candidate from the queue, so the BI bot can legitimately record a
  `rejected` for the same order before the next run). New view
  `orders_delete_approvals_current` (migration 012) resolves latest-wins in SQL,
  `approved_at DESC, id DESC`. The `id` tiebreak is load-bearing: `approved_at` is
  transaction time, so `executemany` batches share a timestamp — proven live. No UPDATE grant
  added; the table stays append-only and only the read changed. A current `rejected` blocks
  deletion and leaves the candidate `pending`. Reported by the BI project.
- **GDPR: deletion backups now have a defined home, permissions and retention.** They contain
  full order rows including customer street addresses, so they are a fresh copy of the data
  the deletion erases. **They previously defaulted to the CWD — the repo root — and were not
  gitignored, so a backup of customer addresses was committable.** Now
  `backups/deleted_orders/` (gitignored, `0700`/`0600`), 30-day retention pruned on every
  run so it cannot lapse. Documented in this repo's new `docs/GDPR_REVIEW.md`, which also
  records a larger pre-existing gap: `orders` has no retention policy at all.
- Tests: 22 passing (was 13).

## v1.4.0 — 2026-09-02

- **Phantom-order deletion pipeline: detection + containment.** The orders sync was
  upsert-only with **no delete path at all**, so anything Backoffice deleted survived here
  forever — inflating BI counts and retaining customer addresses past the controller's own
  deletion (GDPR Art. 5(1)(e)). August 2026 overstated by 34 gigs / EUR 16,520 this way.
  New: `scripts/reconcile_phantom_orders.py` (proposes, never deletes),
  `scripts/delete_approved_orders.py` (the only path that deletes, and only from the
  reviewed list, re-verified against the live API immediately beforehand),
  `n8n-workflows/orders-reconcile.json` (daily 04:30), and
  `supabase/migrations/011_phantom_order_deletion.sql`.
  **Abort rails are the core of the design** — an unreadable day, a day returning zero
  upstream ids while Supabase holds rows, >50 candidates / >10% of the window, or a stalled
  sync log aborts the run writing nothing, because at the point of computation a stalled
  sync is indistinguishable from mass upstream deletion.
  `tests/test_reconcile_abort_rails.py`: 13 tests, all passing.
  Acceptance test passed exactly as specified: August → exactly 34 candidates, June → 0,
  July → 0. **Live**: migration 011 applied (2 tables, 5 indexes, 3 RLS policies), workflow
  `ur0vJrdAnHaCNhcF` active daily 04:30. End-to-end verified — the N8N run independently
  reproduced the Python implementation's exact result (34 candidates, all `pending`, all
  2026-08-14/NB-Palvelut) with `orders` untouched. **Nothing is deleted yet and nothing will
  be** until the BI approval path is wired up and a human approves; the 34 sit as `pending`
  by design. See `docs/STATUS.md` workstream G for the remaining open questions.
- **Root cause logged upstream, not fixed here** — `BACKOFFICE_API_ISSUES.md` **#17**: the
  route import has no idempotency key, so reruns mint fresh `order_id`s (4 reruns on
  2026-08-14). Also documents why the 6 remaining August *duplicate* groups can only be
  fixed in Backoffice: they still exist upstream, so deleting them here just gets them
  re-upserted by the hourly sync.
- **Fixed `scripts/apply_supabase_migration.py`'s statement splitter** — it split on every
  `;`, which tears `DO $$ ... $$` blocks apart and fails with "unterminated dollar-quoted
  string". Found while applying 011, whose RLS grants live in a DO block. Now dollar-quote
  aware; would otherwise have broken every future migration containing a DO block, function
  or trigger. Regression-checked against an existing migration.
- **Sync hardening — deployed and live-verified on all 4 order-sync workflows.**
  `sync_log` is now trustworthy, which the abort rails depend on: `rows_upserted` comes from
  PostgREST's `Content-Range` (DB-confirmed) instead of the count of rows merely
  transformed, and a new `Log Sync Failed` node writes real `status='failed'` rows —
  previously `'success'` was hardcoded and **100% of rows claimed success**. Also: explicit
  `?on_conflict=order_id` instead of PostgREST's implicit PK fallback, and Transform Orders
  now de-duplicates by `order_id` before the POST (a repeated id in one batch raises
  `ON CONFLICT DO UPDATE command cannot affect row a second time` and fails the *whole*
  batch).

## v1.3.0 — 2026-08-24

- **New excl-VAT price columns on `orders`**, requested by apukuski-bi-chatbot while
  reconciling revenue against the Master P&L sheet (VAT-exclusive throughout — they were
  re-deriving this downstream with fragile string-parsing). New columns:
  `base_price_cents`, `services_price_cents`, `recycling_surcharge_cents`,
  `total_excl_vat_cents` (the sum — välitetty myynti excl. VAT, deliberately not
  liikevaihto, which needs a commission-rate computation the sync layer doesn't own).
  Re-verified the underlying formula fresh against 973 real orders (not just re-citing
  the original June validation): 771/772 exact. Backfilled for all existing platform
  orders directly from the already-stored `charge` JSONB — no Backoffice API calls
  needed. Manual orders: `null`, no source data exists to compute this from.
  `supabase/migrations/010_add_excl_vat_price_components.sql`.
- **Fixed: `platform_fee`/`service_fee` had no decimal-cohort handling** — silently
  storing decimal-euro values (a known API quirk, `BACKOFFICE_API_ISSUES.md` #2) as
  1/100th their real amount. Fixed at the sync layer and backfilled retroactively for
  existing orders — **this changes historical `platform_fee`/`service_fee` values** for
  the affected orders (confirmed via a fresh check: 20.7% of recent platform orders, well
  above the original ~4% estimate — not a rounding difference). See `docs/STATUS.md`
  workstream F and `docs/REVENUE_DEFINITIONS.md`.

## v1.2.3 — 2026-08-13

- **GDPR fix: `payments_paytrail.raw_payload` was storing card BIN/last-4/country
  verbatim.** Found during a cross-repo GDPR review with apukuski-bi-chatbot — Paytrail's
  `GET /payments/{id}` response includes a `cardInfo` object that was never stripped,
  unlike `payments_stripe`'s explicit sanitizer. Fixed with an allowlist of known-safe
  fields (not a denylist), and cleaned the 3 already-synced rows retroactively. See
  `docs/STATUS.md` workstream E and `apukuski-bi-chatbot/docs/GDPR_REVIEW.md`.

## v1.2.2 — 2026-08-12

- **Added discount/promo-code tracking to `payments_stripe`**, requested by the BI chatbot
  after it hit a gap trying to compute the `upsell_discount_used` funnel stage.
  `supabase/migrations/009_add_stripe_discount_fields.sql`: `discount_coupon_code` +
  percent/amount-off + applied cents (flat columns for the common case) plus a `discounts`
  JSONB column (full detail, multi-discount edge case). Confirmed live: Stripe's bare
  `session.discounts` only gives an opaque promo-code object ID, not the human-readable
  code — needed `expand[]=total_details.breakdown` too, confirmed it works alongside the
  existing fee/refund expand in the same request. Verified against real data (5 of 100
  sampled sessions had a real discount) and re-synced — all landed with correct
  codes/percentages/amounts.

## v1.2.1 — 2026-08-12

- **Fixed a real production outage caused by v1.2.0's Stripe sync, same day.**
  `payments-stripe-daily`'s Upsert node lacked `executeOnce: true` — n8n HTTP Request nodes
  run once per input item by default, so it fired ~90+ near-simultaneous duplicate
  full-array upserts (322 rows, re-sent whole on every one of those runs). Caused real
  Postgres lock contention (confirmed via Supabase's own logs: `ShareLock` waits,
  `canceling statement due to statement timeout`) that cascaded into a full outage —
  Database/PostgREST/Auth/Storage all unhealthy, Cloudflare 522s. Found the same bug latent
  (not yet triggered) in `ads-google-daily`, `ads-meta-daily`, `analytics-ga-daily` — present
  since v1.0.0's ship date, just at lower volume. All 4 fixed and verified same day.
  `orders-hot/warm/cool.json`/`backfill.json` were already correctly protected from an
  earlier lesson; it didn't carry forward to the workflows built after. See
  `docs/GOTCHAS.md` for the full mechanism.

## v1.2.0 — 2026-08-12

- **Payments sync live: `payments_paytrail` and `payments_stripe`.** Requested by
  apukuski-bi-chatbot for upsell-discount tracking, cost/margin reporting, and orders
  reconciliation (previously no queryable payment source existed anywhere).
  `supabase/migrations/008_create_payments_tables.sql`. Paytrail has no list/date-range
  API — confirmed live — so the sync is event-driven: extended the existing "Paytrail
  Payment Callback Tracking" N8N workflow (not owned by this repo) with a parallel,
  fault-isolated branch, rather than the periodic-pull pattern used elsewhere. Confirmed
  live that Paytrail's `reference` field isn't reliably `orders.order_id` — it's a real
  order_id UUID for direct settlement payments, but a short Airtable OfferID for the
  "schedule later" freight flow; resolved via an Airtable lookup when needed, `null` when
  it can't be, never guessed. No historical backfill possible for Paytrail (no transaction
  list exists to backfill from) — data accumulates from ship date forward. Stripe, by
  contrast, has a real paginated list endpoint, so it follows the standard periodic-pull
  pattern (`payments-stripe-daily`, rolling 30 days); `metadata.order_id` on checkout
  sessions is a direct, reliable UUID join key, and a single nested `expand[]` call gets
  fee/refund data Paytrail doesn't expose at all. Verified end-to-end: 322 rows with real
  order_ids and fee data. GDPR: no cardholder PII, customer email/name/address synced from
  either provider — amounts, statuses, timestamps, and the order-reference key only. See
  `docs/STATUS.md` workstream E. **See v1.2.1, same day** — this release's Stripe sync
  caused a real production outage, fixed same day.

## v1.1.0 — 2026-08-08

- **Fixed a permanent, project-lifetime sync gap: unscheduled orders never synced to
  Supabase.** Backoffice `/order` silently omits orders without a `firstSchedule` (offer /
  "schedule later" freight gigs — real, paid orders, confirmed via Paytrail) unless the
  undocumented `includeUnscheduled=true` parameter is passed; our `Fetch Orders` nodes never
  passed it. Found via a daily gig-count mismatch (10 synced vs. 14 real for 2026-08-07,
  reported in the BI Chatbot's Slack channel). Verified live: a 45-day baseline-vs-param
  diff found 96 hidden orders, all `firstSchedule: null`; other guessed parameter names all
  `400`'d, confirming this one specifically is real. Fixed in `orders-hot/warm/cool.json` +
  `backfill.json`; historical gap backfilled via `scripts/backfill_unscheduled_orders.py`.
  **Partly explains** `docs/BACKOFFICE_API_ISSUES.md` issue #15's Huutokaupat.com "confirmed
  gap" finding — 9 of the 96 hidden orders were Huutokaupat.com-tagged; Tokmanni/Rusta are
  unaffected, their gap stands. See `docs/GOTCHAS.md`.

## v1.0.0 — 2026-08-06

- **Marketing data ingestion** — Google Ads, Meta Ads, and GA now live in Supabase:
  `ads_google_campaign_daily`, `ads_meta_placement_daily`, `analytics_ga_daily_totals/source/geo`.
  Ads/Meta via the Supermetrics API direct (not Sheets — avoided a pivot/staleness problem in
  the existing Sheets-based dashboard queries); GA via its existing native Sheets export. 3 new
  N8N workflows (`Ads Google Daily Sync`, `Ads Meta Daily Sync`, `Analytics GA Daily Sync`), all
  active. Historical backfill: Ads full 2yr, Meta ~13mo (a real Meta Ads Insights API retention
  limit, not fixable), GA full history via the Sheets read itself. Full build/debug trail in
  `docs/STATUS.md` workstream B.
- **Fixed a ~2-day silent production outage, and the monitoring chain that should have caught
  it** — Supabase API key rotated at some point without updating the 8 workflows using it
  (Orders Hot/Warm/Cool, Routes Sync, Reference Data Sync, Backfill, and the monitoring
  workflows themselves), so no order/route/hub data synced from 2026-08-04 16:00 UTC onward.
  Found while answering a routine gig-count question. Fixing the key alone wasn't enough —
  two more independent bugs were also silently blocking alerts: `Sync Error Handler`'s
  `callerPolicy` rejected calls from workflows in a different N8N project
  (`workflowsFromSameOwner` → `any`), and `Supabase Health Check`'s `Healthy?` node crashed on
  strict type validation before ever reaching the Slack alert (`strict` → `loose`). All three
  fixed and independently verified (live re-trigger for the key fix, a real scheduled run for
  the type-validation fix). See `docs/GOTCHAS.md`.
- **`orders.is_asuntosaatio_gig` flag + value correction** — Asuntosäätiö B2B moves (tenant
  signing-bonus deals) are recorded as €0 by the source system; Asuntosäätiö pays outside the
  order record. New column flags these (mention anywhere in the order + recorded value = €0,
  manual orders only) and overrides the value to the real flat rate (€400 excl. VAT / €502 incl.
  VAT), preserving the original €0 for audit. Applied in N8N Transform Orders across all 4
  order-sync workflows going forward; backfilled 24 existing rows
  (`supabase/migrations/006_add_asuntosaatio_gig_flag.sql`). See `docs/GOTCHAS.md`.

## v0.4.0 — 2026-07-30

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

## v0.3.1 — 2026-07-29

- **N8N API key rotated** — New key issued in N8N Cloud; updated in local
  `.claude/settings.json` (gitignored) and Supabase Edge Function secrets via
  `scripts/deploy-edge-functions.sh`. Old key revoked after verifying
  `n8n-trigger-sync` / `n8n-update-schedule` still authenticate.

## v0.3.0 — 2026-07-24

- **`orders.origin` column** — Migration `002_add_origin_to_orders.sql`; N8N Transform Orders
  maps platform order source (`app`, `AVY#{id}`). Manual orders store null. Backfill script:
  `scripts/backfill_order_origin.py`.

## v0.2.0 — 2026-07-23

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

## v0.1.1 — 2026-07-13

- **Project architecture docs** — `docs/project-architecture.md`, `docs/supabase-api.md` added
  for pipeline and consumer reference.

- **Routes sync blocked** — `/route` Lambda timeout documented; upstream fix pending.

## v0.1.0 — 2026-06

- **Initial pipeline** — N8N workflows syncing orders, hubs to Supabase; tiered hot/warm/cool
  schedules; health check + Slack alerts to `#tech-alerts-sos`.
