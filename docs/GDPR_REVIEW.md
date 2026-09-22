# GDPR review — api_data_parser

**Purpose:** track where this service touches personal data and where it could leave the EU
or be copied outside its intended store. Living document — see `CLAUDE.md` for when to
update it.

**Owner:** Jussi. **Last full review:** 2026-09-21.

---

## How this repo differs from apukuski-bi-chatbot's review

That repo's central question is *"does personal data end up in a prompt sent to a model
API."* **This repo has no LLM in it at all** — it is an N8N/Python sync pipeline between the
Backoffice API and Supabase. Nothing here calls a model provider, so the data-residency
question that dominates the BI review does not arise in the same form.

The risk here is different and simpler: **this pipeline is the thing that copies personal
data into Supabase in the first place, and (since 2026-09-02) the thing that deletes it.**
So the questions that matter are: what gets copied, what gets retained, and does deletion
actually delete.

---

## Data flow map — what this pipeline moves

| Source → destination | Personal data present | Status |
|---|---|---|
| Backoffice `/order`, `/manual-order` → `orders` | `stops` — **since 2026-09-21 a structured array, not a flat string**: per-stop street address, **apartment number**, lat/long coordinates, and on some orders **contact name and phone**. Also `manual_data.customer` (name/email/phone), `review` (free text), `userId` | 🟡 Synced in full — see Open issues #1 and #5 |
| Backoffice `/hub`, `/route` → `hubs`, `routes` | None (hub/partner reference data) | ✅ Not a concern |
| Stripe → `payments_stripe` | **None** — `raw_payload` allowlisted at sync time; no cardholder/customer name/email/address | ✅ Verified live 2026-08-12/13 |
| Paytrail → `payments_paytrail` | **None now** — was storing `cardInfo` (BIN, last-4, country) verbatim until 2026-08-13; fixed with an allowlist and existing rows cleaned | ✅ Fixed + verified 2026-08-13 |
| ~~Supermetrics~~ → `ads_*`, `analytics_ga_*` | Aggregate only | ⚪ Being retired — GA4 leg switched off 2026-09-14, ads legs pending |
| **Windsor.ai** → `windsor.*` → `analytics_ga_daily_totals` view | **None** — verified live 2026-09-14, every column is a day-level aggregate (`sessions`, `totalusers`, `newusers`, `screen_page_views`, `bounce_rate`, `conversions`, `conversions_purchase`). No user ids, no client ids, no individual-level rows. | ✅ New sub-processor, see below |
| `orders` → **deletion backups** (`backups/deleted_orders/*.json`) | Full order rows, **including `stops`** | 🟡 See "Deletion backups" below — the reason this file exists |
| **Airtable `Palautteet`** → `core.feedback` → `public.customer_feedback` | **Email deliberately NOT synced** — `Sähköposti` holds a real address on 1,342 of 1,344 source records and is dropped at ingest by an allowlist. Free-text comments ARE synced, scrubbed for emails/phones/addresses/IBANs. Measured before deciding: across 1,339 staff comments, 0 emails, 0 phones, 0 addresses, 2 personal names. Live sync applied 1 redaction across 1,344 rows. No order key exists on the source, so this cannot be joined to `orders`. | ✅ New source 2026-09-22 |
| **Statistics Finland (Paavo WFS)** → `geo.paavo_area` | **None** — open public statistics about postcode *areas*, never about people. Verified against the live 2026 layer: every column is an area-level aggregate, and Statistics Finland itself withholds figures for areas too small to publish (91 of 3,018 areas). No inbound personal data; nothing about Apukuski is sent outward — the WFS request carries no customer data, only a layer name. | ✅ New source 2026-09-21 |

---

## Deletion backups — location, access, retention

`scripts/delete_approved_orders.py` hard-deletes phantom orders (rows Backoffice already
deleted; see `docs/STATUS.md` workstream G). Because that is irreversible, it writes the full
rows to a JSON backup first. **Those rows contain customer street addresses, so the backup is
a fresh copy of exactly the personal data the deletion is meant to erase.** Erasure is not
complete when the row merely leaves `orders`. Raised by the BI repo
(`apukuski-bi-chatbot/docs/GDPR_REVIEW.md` issue #8); defined here as requested.

| | |
|---|---|
| **Where** | `backups/deleted_orders/` in this repo's working tree. Overridable with `ORDER_DELETE_BACKUP_DIR`. **Not** the CWD — it previously defaulted there, which is the repo root and was committable. |
| **Git** | `backups/` and `deleted_orders_backup_*.json` are both in `.gitignore` (added 2026-09-02, verified a stray file in the repo root is ignored). These must never reach GitHub. |
| **Who can read** | Whoever holds the machine/account that runs the script — today, Jussi's laptop. Files are written `0600` and the directory `0700`, so not other local users. Not synced anywhere, not uploaded, not in git. |
| **How long** | **30 days**, then automatically deleted. Configurable via `ORDER_DELETE_BACKUP_RETENTION_DAYS`. |
| **Enforced by** | `prune_old_backups()`, which runs **on every invocation** of the delete script rather than on a separate schedule — so retention cannot silently lapse because a cleanup job was never wired up. Tested (`tests/test_delete_approved_orders.py`). |
| **Why 30 days** | Long enough to notice and reverse a wrong deletion (the realistic failure mode is someone spotting a bad month-end number), short enough that it is not a standing archive. If a restore has not been needed in a month, the backup's purpose has expired and it becomes pure liability. |

**Known limitation, stated plainly:** retention is enforced only when the script runs. If it
is never invoked again, the last backup sits until someone runs it or deletes the directory
by hand. A time-based cleanup independent of the script would close that gap — not built,
because the script is currently the only thing that creates these files and it is expected to
run whenever deletions are approved.

---

## New sub-processor: Windsor.ai (2026-09-14)

Windsor.ai replaces Supermetrics as the connector between GA4/ad platforms and Supabase. It
reads from Google/Meta/GA4 on our behalf and writes into the `windsor` schema.

**What it moves:** day-level aggregates only — verified against the live table, not inferred
from column names. No individual-level data reaches Supabase through it today.

**Two things to note, neither blocking:**
- **It is a new processor in the chain** and holds credentials to our GA4 and (soon) ad
  accounts. That is a vendor/DPA question rather than a pipeline one, but it belongs on the
  record: if Supermetrics had a DPA in place, Windsor needs the equivalent before it is the
  only route.
- **Planned expansion changes the picture slightly.** `analytics_ga_daily_geo` carries
  `city`/`region`/`country`. That is coarse and GA4-thresholded (small cities are suppressed
  by GA4 itself), so it stays aggregate — but when that feed is added, re-check that Windsor
  is not passing through a finer geo grain than the legacy feed did.

**Access:** `windsor_writer` can create and write **only inside the `windsor` schema** — it
cannot see `orders` or anything else in `public`. Confirmed by the grant setup in the
handover doc.

**Housekeeping:** the Windsor API key appeared in screenshots shared 2026-09-14. **Rotated
2026-09-16** — the exposed key is no longer valid.

## Open issues

### 🟡 #1 — `orders` retains full customer addresses indefinitely
`stops` (street addresses), `manual_data.customer` (name/email/phone) and `review` (free
text) are synced verbatim from Backoffice and kept for the life of the row. There is no
retention policy on `orders` at all — the table goes back to 2024-01.

This is not new and not caused by the deletion work, but it is the largest PII surface in
this repo and should have a defined retention position rather than an implicit "forever".
Needs a business/compliance decision, not a code change: how long does Apukuski need
order-level address history, and is anonymising older rows (keeping city/postcode, dropping
street) acceptable for the reporting that depends on it?

### 🟡 #2 — Deletion is gated on human approval, so erasure is not automatic
Phantom rows (deleted upstream, still here) are proposed by `orders-reconcile` and only
removed after a human approves. That gate is deliberate — it is what prevents a flaky API
from triggering mass deletion. But it means the window between "Backoffice deleted it" and
"we deleted it" is unbounded if nobody reviews the queue. **34 candidates have been pending
since 2026-09-02.** Worth an SLA on reviewing the queue, or the storage-limitation gap the
pipeline was built to close stays open in practice.

### ⚪ #3 — Backoffice `userId` now synced
`/order` began returning a real `userId` (confirmed 2026-08-24). It is a pseudonymous
identifier, not directly identifying, but it is the join key that would make customer-level
profiling possible. Tracking only; no action needed unless it starts being used that way.

### 🟡 #5 — `orders.stops` gained contact name, phone and apartment number (2026-09-21)
The Backoffice API changed `/order`'s `stops` from a flat address string to a structured array
around 2026-08. Because the scheduled syncs only touch a rolling 45-day window, ~6,300 older
orders were still holding the truncated string. `scripts/repair_stops_structure.py` refreshed
them from the API so that customer-area segmentation had coordinates to work with.

**That repair widened what Supabase holds.** Counted against the live table afterwards:

| Now present in `orders.stops` | Orders |
|---|---:|
| apartment number | 5,854 |
| phone number | 2,156 |
| contact name | 2,121 |
| email | 1 |

Roughly 2,100 orders gained a contact name and phone that were **not previously in Supabase at
all**. Street address + apartment + a moving date identifies a specific dwelling and household —
issue #9 in the BI repo's review already rates an address plus a moving date as higher-risk than
a customer name, and this makes that more true, not less.

**Nothing in this repo reads name or phone from `stops`.** The segmentation needs `address`,
`location` and `apartment` only. An allowlist sanitiser at sync time — the pattern already used
for `payments_stripe` and `payments_paytrail`, where an unexpected future field is excluded by
default — would strip name/phone/email and leave the pipeline fully functional with materially
less personal data retained.

**Decision 2026-09-21 (Jussi): left as-is for now**, recorded here rather than actioned. Revisit
if the retention question in #1 is taken up, since the two are the same decision at different
scopes.

**Update, same day — largely resolved for the BI consumer, not at source.**
`public.orders_reporting` (migration 043) removes `stops` from `bi_chatbot_readonly`'s reach
entirely, replacing it with a derived `housing_type` label. The BI repo's only live use of
`stops` was a housing-type regex, so serving the label costs them nothing (0 mismatches across
12,095 orders) and removes street addresses, apartment numbers, contact names and phone numbers
from that service. The same view redacts `manual_data`'s `customer`, `additionalInfo` and
`raw_charge_total` — **4,462 names, 3,613 emails, 4,036 phone numbers** that the BI repo had been
blocking in application code only. The data still sits in `orders` itself, so this narrows
exposure rather than closing #1.

### 🔴 #6 — Live Backoffice API key committed to a PUBLIC GitHub repo (found 2026-09-22)
**Not yet actioned. Logged at Jussi's direction 2026-09-22; the decision to defer is his.**

`oz6Dcg…Mdi4` — the live value of `BACKOFFICE_API_KEY`, verified identical to the one in `.env`
— is hardcoded in **ten tracked files**:

| | |
|---|---|
| Scripts | `backfill_order_origin.py:24`, `reconcile_airtable_financials.py:29`, `verify_sync_counts.py:30`, `backfill_missing_months.py:38` |
| Workflows | `orders-cool.json`, `orders-warm.json`, `backfill.json`, `routes-sync.json`, `reference-sync.json`, `paytrail-payment-callback-tracking.json` |

Present since commit `ecfad66`. **`jussi-chapeau/API_data_parser` is PUBLIC** (confirmed via
`gh repo view`, 2026-09-22).

**Why this belongs in a GDPR file and not only a security note:** the key is not a nuisance
credential. It authenticates `/order` and `/manual-order`, which return customer street
addresses, apartment numbers, contact names and phone numbers for every order in the business.
Anyone who has read this repository can retrieve the full order book. Under Art. 32 this is a
failure of "appropriate technical measures", and if exploited it is a reportable breach — the
32-hour clock would start from the moment misuse were detected, not from today.

**This also directly violates `CLAUDE.md`**, which requires workflow JSON to carry the
`SUPABASE_SERVICE_KEY`-style placeholder and injects secrets at import time, and states plainly:
*"Never put secrets in files — including docs, workflow JSON committed to git, or chat."* The
placeholder discipline was applied to the Supabase key and not to this one.

**What actually fixes it, in order:**
1. **Rotate the key in AWS.** Scrubbing the files does not help: the value is in git history and
   in every fork and clone. Until it is rotated it stays valid.
2. Update `.env` and the N8N credential.
3. Replace all ten occurrences with an env lookup / placeholder, and add a pre-commit or CI
   check so the next one fails loudly rather than shipping.

Steps 2–3 are ready to do on request; step 1 is not something this repo can perform.

### 🟡 #7 — Our own repair snapshot was BI-readable (found and closed 2026-09-22)
`scripts/repair_stops_structure.py` wrote `public.orders_stops_backup_20260921` before the
2026-09-21 repair — in-database rather than to a file, deliberately, because `stops` is PII and
a file would be a second copy outside the trust boundary. That reasoning held. What was missed:
**creating any table in `public` inherits Supabase's default privileges**, so the snapshot was
immediately readable by `bi_chatbot_readonly`, `anon` and `authenticated`.

Measured before revoking: 12,298 rows, 11,798 carrying stops text, containing **612 phone
numbers, 9,738 address-like strings and 8 email addresses**.

**This defeated migration 043 completely.** That migration removed `stops` from the BI
consumer's reach and served a `housing_type` label instead — while a verbatim copy of the same
column sat one table away, ungated. The pending revoke on `public.orders` (migration 044) would
not have helped either.

Revoked from every BI and PostgREST role in **migration 052**; only `postgres`/`service_role`
retain access. **The snapshot itself is retained** — the repair is one day old and a rollback
window is reasonable — but it is a fresh copy of exactly the data issue #1 is about and should
be dropped once the repair is trusted. Deleting it is the owner's call, not this repo's.

**Third occurrence of the same trapdoor** (033 for `windsor`/`core`, 043 for the reporting view,
052 here). Every new object in `public` reopens it. A standing assertion — "no BI or PostgREST
role may hold SELECT outside an allowlist" — would catch the next one; remembering has now
failed three times.

### ⚪ #8 — Other `public` tables readable by the BI role may hold personal data
Noted while auditing #7, not investigated and **not actioned** — these belong to other
workstreams: `leads`, `prospects`, `whatsapp_messages`, `orders_delete_candidates` are all
SELECT-able by `bi_chatbot_readonly`. `whatsapp_messages` in particular sounds like message
content. Worth the same column-level review `orders` received in migration 043.

### ⚪ #4 — Infrastructure regions
Supabase is confirmed **EU (Frankfurt, eu-central-1)** — seen directly in the project
dashboard 2026-08-12. N8N Cloud's processing region and the Backoffice API's own hosting are
not confirmed from this repo. Not asserting they are non-EU; just that this document cannot
confirm them.

---

## Mitigations already in place

- **`payments_stripe` allowlist** (2026-08-12): `raw_payload` is built from an explicit
  allowlist of transactional fields; customer email/name/address never enter Supabase.
- **`payments_paytrail` allowlist** (2026-08-13): same approach, added after finding
  `cardInfo` (BIN/last-4/country) being stored verbatim. Allowlist, not denylist —
  an unexpected future field from the provider is excluded by default. Existing rows cleaned.
- **Deletion pipeline** (2026-09-02): gives the system a delete path at all, closing the case
  where data the controller had already erased survived here indefinitely.
- **Backup retention** (2026-09-02): location, permissions, gitignore and a 30-day enforced
  window, as documented above.

---

## When to update this file

Update whenever a change:
1. Adds, removes or changes a data source this pipeline reads from or writes to.
2. Changes which fields are synced — especially anything added to a `raw_payload` or other
   verbatim-copy column.
3. Adds or changes a deletion, retention, anonymisation or backup mechanism.
4. Resolves or newly discovers an item in "Open issues".

Verify against live data before writing anything down here — a column name does not tell you
what it contains. Every claim in this file was checked against the real database or a live
API response on the date given.

---

## Review log

- **2026-08-12/13** — `payments_stripe` verified PII-free before being documented as safe;
  `payments_paytrail` found storing `cardInfo` verbatim during a cross-repo review, fixed
  with an allowlist and existing rows cleaned.
- **2026-09-02** — First GDPR review file for this repo, created at the BI repo's request
  (their issue #8) to define deletion-backup handling. Defined location/access/retention and
  implemented enforced pruning. Found and fixed along the way: **backups defaulted to the
  repo root and were not gitignored** — a stray backup containing customer addresses was
  committable. Also documented Open issue #1 (no retention policy on `orders` at all), which
  is larger than the backup question that prompted this file.
- **2026-09-14** — Windsor.ai added as a data source (GA4 leg), replacing Supermetrics.
  Verified against the live `windsor.ga4_daily_totals` table that it carries no
  individual-level data before recording it as safe. Supermetrics GA4 workflow deactivated.
  Also added `analytics_freshness` + a watchdog: unrelated to PII, but it closes a monitoring
  gap that let a data feed sit broken for five weeks unnoticed.
- **2026-09-16** — Windsor migration completed for GA4 (totals/source/geo), Google Ads and
  Meta; all three Supermetrics workflows deactivated. Added a durable `core` layer between
  the vendor's staging tables and reporting, after a misconfigured vendor task deleted a
  month of production data. Windsor API key (exposed in screenshots 09-14) rotated.
  No change to what personal data is carried: all feeds remain day-level aggregates, with
  geography at city/country grain — `region` was dropped, so the new feed is slightly
  *coarser* than the Supermetrics one it replaces.
- **2026-09-21** — Customer-area segmentation, stage 1. Added **Statistics Finland (Paavo)** as
  a reference data source (migration 036, `scripts/load_paavo.py`): 3,018 postcode areas with
  geometry and area-level statistics. Carries no personal data in either direction, and sends
  nothing about Apukuski outward. Loaded into a **new `geo` schema deliberately kept outside
  `core`**, because migration 022's `ALTER DEFAULT PRIVILEGES` would otherwise grant the BI
  consumer SELECT on any new `core` table automatically — verified after loading that `geo` has
  no grants beyond `postgres`.
  Also recorded, not fixed: **Open issue #5**, the contact name/phone/apartment that entered
  `orders.stops` via the 2026-09-21 structural repair. Quantified against the live table rather
  than estimated. Jussi's decision was to leave it for now.
  Still outstanding before the consumer-facing views are built: a documented
  **legitimate-interest basis** for area segmentation (a new purpose for existing data), and
  the narrowing of `bi_chatbot_readonly`'s SELECT on `public.orders` — until that lands, any
  k-anonymity claim about a segmentation view is defeated by reading the underlying table.
  Stages 2–3 completed the same day (migrations 037–042). Two things worth recording here:
  **`geo.stop_resolution` is a new store of location data**, but a deliberately minimal one —
  postal code and a salted address hash per stop, no coordinates, no address text, no contact
  details. Unlike `orders`, it *can* carry a retention policy, because nothing downstream needs
  the postcode once the area label is bound; dropping it after binding would make this feature a
  net reduction in retained personal data. Proposed, not yet implemented.
  Also: `ALTER DEFAULT PRIVILEGES IN SCHEMA core REVOKE SELECT ... FROM bi_chatbot_readonly`
  was applied (migration 039). It changes no existing grant, so it cannot break the BI repo —
  it only closes the trapdoor where a future `core` table becomes consumer-readable with no
  grant line in the migration for a reviewer to notice.
  **Legitimate-interest basis for area segmentation: confirmed by Jussi 2026-09-21**, recorded
  as a dated controller decision in the BI repo's `docs/GDPR_REVIEW.md` together with the design
  that makes it defensible (no individual profiled, suppression in a view rather than
  application code, Paavo is data about places). Open issue #2 in this file therefore no longer
  blocks the published views; the remaining gate is the migration 044 cutover.
- **2026-09-22 (later)** — Added **Airtable `Palautteet`** as a data source (migration 049,
  `scripts/sync_airtable_feedback.py`): 1,344 customer feedback records. Design decisions taken
  by Jussi and recorded here: customer **email dropped entirely** (nothing downstream needs it;
  the Airtable record id is the key), free-text comments **synced with scrubbing**, and the
  staff-opinion field **included** after measuring it rather than assuming — it contains 2
  personal names in 1,339 comments and no emails, phones or addresses. Residual risk is
  indirect rather than direct: with few partners per city a specific complaint could still point
  at a worker, which is why no worker identifier is carried alongside the text.
  The write path uses a `SECURITY DEFINER` RPC rather than exposing the `core` schema to
  PostgREST, and the RPC is explicitly revoked from `anon`/`authenticated` — verified false for
  both after applying. `public.customer_feedback` is granted to `bi_chatbot_readonly` only.
- **2026-09-22** — Found the live `BACKOFFICE_API_KEY` hardcoded in ten tracked files in a
  **public** repository (new issue #6), while investigating an unrelated Airtable question.
  Logged, not actioned, at Jussi's direction. Also added a `conversions_caveat` column to the
  GA views (migration 045): GA4's `conversions` counts `session_start` and `add_to_cart` as key
  events, so the BI portal and bot had been quoting a number that runs at 7–9 per session on
  every source. No personal data involved, recorded here because it is a data-integrity control
  of the same family as the suppression work.
- **2026-09-21 (later)** — `public.orders_reporting` added (migration 043); see issue #5.
  **A hole was opened and closed during this work, recorded because the class of it recurs:**
  creating a view in `public` inherits Supabase's default privileges, so `anon` and
  `authenticated` immediately held SELECT/INSERT/UPDATE/DELETE/TRUNCATE on it — and a Postgres
  view runs as its *owner* unless told otherwise, so it would have read `orders` with RLS
  bypassed (`orders` has RLS enabled with a policy). Fixed in the same migration with
  `security_invoker = on` and an explicit `REVOKE ALL ... FROM anon, authenticated, PUBLIC`,
  then re-verified: only `bi_chatbot_readonly` (SELECT) and `service_role` remain.
  **Every new object in `public` reopens this.** Migration 033 PART 2 closed the same trapdoor
  for `windsor`/`core`. Worth a standing assertion rather than remembering each time.
