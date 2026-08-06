# STATUS — Current Work & Session Handoff

This is the **shared handoff doc** between Claude Code, Cursor, and any other agent working
this repo. Whoever ends a session updates this file with what changed and what's still open;
whoever starts a session reads it first, before assuming what's done vs. pending.

Update this file, don't create a new one — one canonical status doc avoids drift.

**Last updated:** 2026-08-06

**Two ad-hoc production items closed out today, outside the lettered workstreams below** —
full detail in `CHANGELOG.md` (2026-08-06 entry) and `docs/GOTCHAS.md`, not duplicated here:
a ~2-day silent Supabase-key outage across 8 workflows (found while answering a gig-count
question, now fixed), and the new `orders.is_asuntosaatio_gig` flag + value-correction logic.

---

## Active workstreams

### A. Stops/address structured parsing
- [ ] Get Google Maps Geocoding API key → add to `.env`
- [ ] Small test batch (~50–100 orders) to validate parsing + measure real throughput/cost before full run
- [ ] Supabase migration: add `pickup_city`, `pickup_postal_code`, `delivery_city`, `delivery_postal_code`, `waypoints_parsed` (jsonb), `stop_building_details` (jsonb) to `orders`
- [ ] Update Transform Orders node in all 4 N8N workflows to parse+geocode stops going forward
- [ ] Historical backfill script + run (~11,500 geocoding calls across 11,120 orders)

### B. Marketing data ingestion (Google Ads / Meta / GA)

**Sample files read 2026-08-06** (all three are Supermetrics-generated exports, all Sheets
presumably, not native platform exports — matters for what N8N is actually pulling from).
These are sample snapshots dropped in `data/` for inspection, not the live sheets themselves.

- **GA** (`Export Google Analytics -_ Apukuski Dashboard.xlsx`) — clean, 3 usable tabs, all tidy
  long-format, all current through 2026-08-03:
  - `Daily metrics sheet` → header at **row 13**, data starts col L (11 blank lead columns) — maps to `analytics_ga_daily_totals` (580 rows)
  - `Traffic sources` → header row 1 — maps to `analytics_ga_daily_source` (5,297 rows)
  - `Geo data` → header row 1 — maps to `analytics_ga_daily_geo` (11,350 rows)
  - `SupermetricsQueries` tab in all 3 files is Supermetrics metadata, not data — ignore
- **Meta** (`Export Meta - Dashboard.xlsx`) — one usable tab, tidy, placement-level:
  - `Supermetrics export` → header row 3, 14,039 rows, data ends 2026-05-27. **Resolved
    2026-08-06:** not a broken refresh — Meta ad spend has been paused since May 2026, so
    there's simply no new data to sync. No fix needed here.
- **Google Ads** (`Export report Google Ads - Dasboard.xlsx`) — **messy, 3 different candidate
  data tabs, no single clean source**:
  1. `Export to Apukuski Dashboard` — tidy, campaign-day grain w/ budget/status fields, header
     row 13 (rows 7-8 are an unrelated "Total: Account" summary block to skip), 44,000 rows,
     **but stale — data stops 2025-12-30** (~7 months behind)
  2. `Supermetrics` — tidy, campaign-day grain w/ conversion value, header row 17, 3,826 rows,
     **also stale — stops 2026-04-20** (~3.5 months behind)
  3. `Export to Apukuski Dashboard - Asset Groups` (sheet tab shows as `Export to Apukuski
     Dashboard - `, trailing space) — **wide/pivoted by Asset Group**, not Campaign
     (Supermetrics query confirmed via UI 2026-08-06: dimension `Date`, pivot dimensions
     `assetGroupId`/`assetGroupName`, range A1:AJ93, `last3months`, filter
     `Impressions > 0`, refresh currently trigger-only not scheduled). Asset Groups only
     exist on Performance Max campaigns — this query likely **excludes standard Search
     campaigns entirely** (the `[P]`/`[S]`-prefixed rows visible in tabs 1/2 look like Search
     campaigns). Most current (May 1 – Jul 31 2026) but narrow scope, only 3 months deep, and
     the schema breaks/needs remapping every time an asset group is added or removed. Bad
     shape and possibly incomplete scope for programmatic ingestion as-is — do not replace,
     leave running for its existing dashboard use.

  None of the 3 alone gives both full history and current data in a stable schema. Likely
  explanation: 1 and 2 are abandoned/superseded Supermetrics queries from earlier dashboard
  iterations that stopped refreshing; 3 is the one still being refreshed but was built as a
  pivot table for human dashboard viewing, not for automation.

  **Decided 2026-08-06:** create a new tidy Campaign-grain query (covers both Search and
  PMax uniformly, unlike the Asset-Group pivot). Spec given to Jussi to set up in Supermetrics:
  - Dimensions (not pivot): `Date`, `Campaign ID`, `Campaign name`
  - Pivot dimensions: **none** — this is what keeps output long/tidy
  - Metrics: `Impressions`, `Clicks`, `Cost`, `Conversions`, `Total conversion value` (skip
    `CPC`/`CostPerConversion` — derived ratios, compute downstream instead of storing)
  - Filters: none (keep zero-impression days — a missing row should mean "sync failed," not
    "no impressions")
  - Date range type: `last 30 days` refreshed daily (Google Ads attributes conversions
    retroactively for weeks; rolling window + N8N upsert lets corrections land automatically)
  - Output: new tab in the same Google Ads workbook, e.g. `Supermetrics - Ads Daily (tidy)`
  - Schedule: explicit daily refresh (existing Asset Groups query is trigger-only — don't
    replicate that for the automation-facing query)
  - **Once created:** update this doc with the actual tab name/range, then unblock the N8N
    workflow + migration work.
  - Backfill history from tabs 1/2 remains a separate one-time best-effort load — schemas
    differ from each other and from the new query, so needs its own mapping.
  - **2-year backfill query:** same shape as above, separate one-off query, custom date range
    `2024-08-06` → yesterday, output to its own tab (e.g. `Supermetrics - Ads Backfill 2yr`),
    no recurring schedule. Load into Supabase via a one-off `scripts/backfill_marketing_google_ads.py`
    (mirrors existing `scripts/backfill_*.py` pattern) once the migration exists — not an N8N
    workflow, this only runs once. Delete/pause the backfill query+tab after it's verified loaded.

**Considered and rejected 2026-08-06:** Supermetrics writing directly to Supabase, skipping
Sheets + N8N. Supabase/plain Postgres isn't a supported Supermetrics destination (their
warehouse connectors: AlloyDB, S3, Azure SQL/Storage/Synapse, BigQuery, Databricks, GCS,
Fabric, Redshift, SFTP, Snowflake). The AlloyDB connector is generic Postgres-wire and might
technically reach Supabase, but that's unsupported/untested by Supermetrics — not worth
building production sync on. Also wouldn't remove the transform step (raw landing shape ≠
target schema, no `sync_log` write) — the job just relocates, doesn't disappear. **Staying
with Sheets → N8N**, consistent with the rest of the pipeline's `sync_log`-based pattern.

**DECIDED 2026-08-06 — Google Ads uses Supermetrics API direct, not Sheets.** Tested live in
Supermetrics Hub → API queries against the real account. Supermetrics has a real REST API
(`docs.supermetrics.com/apidocs`) — GET/POST JSON, static API key (header or param), params
`ds_id`/`ds_accounts`/`start_date`/`end_date`/`fields`/`max_rows`. Output is inherently
row-based (no pivot-dimensions concept), so the Asset-Group-pivot problem that broke the
Sheets approach doesn't apply here. N8N can HTTP-Request this directly — same pattern already
used for the Backoffice API. No Sheets tab, no Sheets credential needed for Google Ads
marketing data. Ongoing sync and historical backfill are the same call with different
`start_date`/`end_date`, not two separately configured Sheets queries.

**Test results (query: `ds_id=AW`, dims `Date`/`CampaignID`/`Campaignname`, metrics
`Impressions`/`Clicks`/`Cost_eur`/`ConversionValue`/`Conversions`, no filter):**
- Confirmed on existing Supermetrics subscription, not the 14-day trial — no expiry pressure
- `last_7_days` → 21 rows, 200 OK, 4.31s
- `last_12_months` → **2,693 rows** (true count, confirmed un-truncated at `max_rows=10000`),
  200 OK, 1.54s, 239.7 KB
- Extrapolated 2-year backfill: ~5,000–6,000 rows — comfortably one call with
  `max_rows=50000`, no pagination/date-chunking needed
- `max_rows` dropdown options: 100/500/1000/5000/10000/50000/100000/1000000 — confirms no
  practical ceiling for this account's scale
- Metric field is plain `Conversions` (not `All conversions` — a different, larger metric in
  Google Ads that includes non-primary conversion actions; deliberately not used)
- `Conversions` values are fractional (e.g. 2, 3.5, 2.43) — normal Google Ads attribution
  behavior, not a data bug; Supabase column should be numeric/decimal, not integer
- Confirmed field names for schema mapping: `Date`, `CampaignID`, `Campaignname`,
  `Impressions`, `Clicks`, `Cost_eur`, `ConversionValue`, `Conversions`
- Query URL contains the API key in plaintext — handle like the Backoffice/Supabase keys,
  store only in N8N's credential store, never in git/docs/chat

**Superseded by this:** the Sheets tidy-query + separate 2-year-backfill-query plan above —
don't build those, this API path replaces them for Google Ads.

**DECIDED 2026-08-06 — Meta also uses Supermetrics API direct** (`ds_id=FA`), same reasoning
as Ads. Tested at **Placement grain** (matches the planned `ads_meta_placement_daily` table),
dims `Date`/`Campaign ID`/`Campaign name`/`Placement`, metrics `Cost (EUR)`, `Impressions`,
`Unique link clicks`, `Website conversions`, `Website conversion value`, `Website purchases`
(skipped derived `CPC` and ambiguous `Schedule (total)`, same reasoning as Ads).

**Test results:**
- `last_12_months` with `max_rows=10000` → returned exactly 10,000 rows at 49.75s — truncated,
  same trap as the first Ads attempt
- `last_12_months` with `max_rows=100000` → **18,641 rows (true count)**, 200 OK, 8.63s,
  2117.8 KB — confirmed un-truncated
- Extrapolated 2-year backfill: ~35,000–40,000 rows — still a single call, `max_rows=100000`
- Placement grain generates far more rows/day than Ads' campaign-only grain, as expected —
  worth remembering if a future source needs this level of detail
- Confirmed field names: `Date`, `adcampaign_id`, `adcampaign_name`, `placement`, `cost_eur`,
  `impressions`, `unique_action_link_click`, `offsite_conversions`, `offsite_conversion_value`,
  and one more (`offsite_co...`) cut off in the captured URL for Website purchases — confirm
  exact key from the Raw tab when building the transform
- Same API-key-in-URL handling applies — never in git/docs/chat

**GA stays on Sheets** — deliberate choice, not tested via API. Native Google-to-Sheets
integration for GA is already free/clean/current, no reason to add API complexity where
nothing is broken.

**Meta refresh "stuck at 2026-05-27" is resolved, not a bug** — Meta ad spend has been paused
since May 2026, so there's genuinely no new data. No fix needed.

**Still open:**
- [x] Supermetrics API key added to N8N as a Header Auth credential ("Supermetrics API",
      Name: `Authorization`, Value: `Bearer API_...`) — covers both Ads (`ds_id=AW`) and
      Meta (`ds_id=FA`)
- [x] `max_rows` set: `5000`/`10000` for the ongoing rolling syncs (Ads/Meta), `50000`/`100000`
      for the one-off 2-year backfill scripts (Ads/Meta) — confirmed safe headroom from testing
- [x] Meta's Website purchases field confirmed: `offsite_conversions_fb_pixel_purchase`
- [x] Supabase migration written and **applied**: `supabase/migrations/003_create_marketing_ads_tables.sql`
      — both `ads_google_campaign_daily` and `ads_meta_placement_daily` confirmed to exist via
      `information_schema.tables`. GA's 3 tables are a separate migration, not needed yet
      since GA stays on Sheets.
      **Caught and fixed a real bug applying this:** `scripts/apply_supabase_migration.py`'s
      statement splitter filtered out any statement chunk that merely *started* with `--`,
      which silently dropped `ads_google_campaign_daily` entirely (the migration's leading
      comment block sat directly in front of it). First run reported `"applied": 1` looking
      like success — only caught it by independently checking `information_schema.tables`
      rather than trusting that count. Fixed the script to strip full-line comments before
      splitting, not after; created the missing table directly, then reran to confirm
      `"applied": 2`. Worth remembering for any future migration with a leading comment block.
- [x] N8N workflows drafted: `n8n-workflows/ads-google-daily.json`,
      `n8n-workflows/ads-meta-daily.json` — rolling `last_30_days` sync, same
      trigger/fetch/transform/upsert/log shape as `reference-sync.json`, but using a proper
      N8N credential reference for the Supermetrics key instead of hardcoding it (unlike
      `reference-sync.json`'s Backoffice call).
      **Imported via N8N API 2026-08-06** (discovered `.env` already has `N8N_API_URL`/
      `N8N_API_KEY` — programmatic access works in this environment, contrary to CLAUDE.md's
      general "often blocked from cloud sandboxes" note):
      - Ads Google Daily Sync (last 30 days) — id `CZbzvcmagNxvC1JN`
      - Ads Meta Daily Sync (last 30 days) — id `vuQOMC0tnTaZkMTC`
      - Both confirmed **inactive** on import (deliberate — want a manual test run first,
        not auto-scheduled yet) with all 6 nodes intact.
      - `GET /credentials` isn't supported by n8n's public API (create-only, by design) — could
        not look up the real Supermetrics credential ID to patch into the JSON before import.
        **Still needs a manual step:** open the "Fetch ... Data" node in each workflow and
        reselect the "Supermetrics API" credential from the dropdown — the placeholder ID in
        the JSON won't resolve on its own.
      - Also still unverified: whether n8n auto-splits the `/keyjson` endpoint's bare array
        response into one item per row, the way it already does for the Backoffice `/hub`
        array — flagged in both Code nodes, check on first real run (webhook or manual execute,
        not the schedule — they're inactive).
- [x] Backfill scripts written: `scripts/backfill_marketing_google_ads.py`,
      `scripts/backfill_marketing_meta.py` — one-time 2-year pulls, not N8N workflows,
      mirroring the existing `scripts/backfill_*.py` pattern.
- [x] **Google Ads dry-run: clean.** Full 2-year range (`2024-08-05` to `2026-08-05`), single
      call, 4,821 rows, no failures, no chunking needed.
- [x] **Meta dry-run: found a real platform data limit, not a bug.** First attempt (single
      2-year `/keyjson` call) got a genuine server-side 500 (not a client timeout — the
      identical 12-month range returns in ~9s, well under our 180s timeout). Added chunking
      (`CHUNK_DAYS=90`) to isolate the cause: chunks from **2025-07-31 onward all succeeded**
      (18,690 rows total incl. the near-zero paused period), chunks from **2024-08-05 to
      2025-07-30 all failed identically**, confirmed down to a single 1-week probe
      (2025-01-01 to 2025-01-07) that failed the same way with zero rows involved — ruling out
      row-count/size as the cause. This matches a known Meta Ads Insights API behavior:
      granular breakdowns like `placement` often have a much shorter retention window
      (~13 months here) than aggregate campaign-level data, regardless of how far back the
      account's history goes. **Practical conclusion: Meta backfill can only reach back to
      ~mid-2025, not the planned 2 years — this is a platform ceiling, not something to keep
      retrying or patch around.**
- [x] **Both backfills run for real and verified 2026-08-06.** Neither `SUPABASE_SERVICE_KEY`
      nor `SUPABASE_URL` were in `.env` (only `SUPABASE_ACCESS_TOKEN`) — added a `--via sql`
      fallback to both scripts (Management API + Cloudflare UA fix, mirroring
      `backfill_missing_months.py`) rather than block on a new secret. Decision on Meta's
      shortened window: backfilled what's actually available (`--start 2025-07-31`) rather
      than chasing the Supermetrics-support question — can revisit later if a deeper Meta
      history turns out to matter.
      - `ads_google_campaign_daily`: **4,821 rows**, `2024-08-05` to `2026-08-05` — verified
        via `information_schema`-adjacent count query, matches the dry-run exactly.
      - `ads_meta_placement_daily`: **18,690 rows**, `2025-07-31` to `2026-05-07` (max date
        stops there, not the query's `2026-08-05` end — expected, matches the confirmed
        ad-spend pause, not a bug). 5 chunks, 0 failures, matches dry-run exactly.
- [x] **Credential reselection + first live test run, both workflows, 2026-08-06.** Ads ran
      fully green end-to-end (90 items through every node — confirms both the credential
      reselection worked and the `/keyjson` auto-split assumption was correct). Meta's Fetch
      node also succeeded but returned 0 items (expected — `last_30_days` window falls
      entirely within the ad-spend pause), so n8n correctly skipped the downstream nodes
      rather than stalling; confirmed via `GET /executions` API (status `success`), not just
      the canvas view, since the canvas briefly showed unchecked downstream nodes in a way
      that looked stuck but wasn't. Meta's Transform/Upsert logic itself was already proven
      correct via the backfill script's 18,690-row real write, so this is considered validated
      despite not exercising nonzero data live in n8n.
      Placeholder `SUPABASE_SERVICE_KEY` text in both workflows' Upsert/Log Sync node headers
      (never substituted since import went via API, not the usual sed-based flow) — patched
      via N8N API once the real key was in `.env`.
- [x] **Both workflows activated 2026-08-06** and added to `N8N_WORKFLOW_IDS.md`:
      `CZbzvcmagNxvC1JN` (Ads Google Daily Sync), `vuQOMC0tnTaZkMTC` (Ads Meta Daily Sync),
      both daily 05:00.

**Workstream B — Ads/Meta ingestion is now live.** GA remains on its existing Sheets read
(deliberate, unchanged). Only remaining open thread for B is the new tidy Google Ads
Supermetrics *Sheets* query — superseded by the API approach and no longer needed.

**Decisions made 2026-08-06 (historical, superseded above):**
- ~~Google Ads: on hold, testing Supermetrics API direct-to-N8N first~~ — **done, API approach
  confirmed and live.**
- ~~Meta: Jussi investigating stuck Sheets refresh~~ — **resolved, ad spend paused since May
  2026, not a bug; moot now that Meta also runs API-direct.**
- [x] Google Sheets + Docs credential path decided — service account, see instructions below
- [x] ~~Decide: full history backfill vs. recent cutoff~~ — done (Ads 2yr, Meta ~13mo capped
      by platform limit, GA full history via Sheets)
- [x] ~~Supabase migration for marketing tables~~ — done, all 5 tables live
      (`ads_google_campaign_daily`, `ads_meta_placement_daily`, `analytics_ga_daily_totals`,
      `analytics_ga_daily_source`, `analytics_ga_daily_geo`)
- [x] ~~Build N8N workflow(s)~~ — done, all 3 workflows live and verified
- [x] ~~Backfill historical marketing data~~ — done for Ads/Meta (Python scripts); GA didn't
      need a separate backfill, the daily Sheets-read workflow already pulls full history

#### Google Sheets + Docs credential setup (one service account, covers both)

**Done 2026-08-06:** GCP project "Apukuski BI tool" (reused existing, not a new project),
Sheets + Drive APIs enabled, service account
`apukuski-bi-dashboard@apukuski-bi-tool.iam.gserviceaccount.com` created, JSON key generated.
GA Google Sheet ("Export Google Analytics -> Apukuski Dashboard") shared with it as Viewer.

Two real bugs caught and fixed getting the key into `.env` and N8N:
- `GOOGLE_SERVICE_ACCOUNT_EMAIL` was silently empty on the first pass (only the private key
  got pasted) — my own verification check was flawed (confirmed the line existed, not that it
  had a value). Caught by cross-checking against the Google Sheets sharing dialog, which
  showed the real email in plaintext (not sensitive, just an identifier).
- The pasted `GOOGLE_SERVICE_ACCOUNT_PRIVATE_KEY` had a stray trailing comma (`...END PRIVATE
  KEY-----\n",` — copied along with the JSON field's trailing comma). Would have broken PEM
  parsing. Fixed directly in `.env`.
- Also, separately: the `googleApi` N8N credential schema's `inpersonate`/`httpNode` fields
  use JSON Schema `if/properties` conditionals that vacuously pass when the field is *absent*
  (not just when falsy) — omitting them entirely triggered spurious
  `required: delegatedEmail/httpWarning/scopes` errors; fixed by sending them explicitly as
  `false`.
- N8N public API has no PATCH for credentials (405) — fixed the broken first credential by
  DELETE + recreate rather than update.

**Verified working, not just assumed:** signed a real JWT with the private key and exchanged
it for a Google OAuth token directly (`oauth2.googleapis.com/token`) — succeeded, confirming
the key is cryptographically valid end-to-end, not just accepted by n8n's schema. Final N8N
credential: type `googleApi`, id `gm81SgSEs5KKRqZx`, name
"Google Service Account (Sheets/Drive/Docs)".

**Live sheet verified 2026-08-06** (`GA_SHEET_ID` now in `.env`, spreadsheet
`16fd6omC0tZmb_6fmJdggXqvQ2KCi8-SYoJzZqL-KcSM`): fetched real spreadsheet metadata + header
rows via the Sheets API (not just trusting the xlsx snapshot). All 4 tabs match exactly —
`Daily metrics sheet` (header row 13, data from column L, 11 blank lead columns, confirmed
live), `Traffic sources` (header row 1), `Geo data` (header row 1), `SupermetricsQueries`
(metadata, ignore). Row counts slightly higher than the snapshot (expected daily growth).

**One real finding that changes the transform:** with `valueRenderOption=UNFORMATTED_VALUE`,
date cells come back as **Google Sheets serial numbers** (e.g. `45658` = 2025-01-01, epoch
1899-12-30), not date strings. The transform step must convert
`new Date(Date.UTC(1899, 11, 30) + serial * 86400000)` (or equivalent) before writing to a
DATE column — passing the raw serial through would either error or silently store garbage.
All other values (Sessions, Conversions, etc.) came back as clean native numbers, confirming
no locale-string parsing is needed here, consistent with the earlier xlsx inspection.

Docs write access is for a future workstream, not yet scoped — API enablement is already in
place, defer sharing individual Docs until that work starts. When that starts: same
credential, just share the specific target Doc(s) with Editor access and add a Google Docs
node — no Cloud Console changes needed.

#### GA Supabase tables + N8N workflow — built 2026-08-06

- Migration applied: `supabase/migrations/004_create_ga_analytics_tables.sql` —
  `analytics_ga_daily_totals` (PK `date`), `analytics_ga_daily_source` (PK
  `date, source_medium`), `analytics_ga_daily_geo` (PK `date, city, region, country`).
  Verified via `information_schema.tables`, not just trusted from the apply output.
- Credential: `googleApi` id `vwfaClhDT4B21w9V`, created with `httpNode: true` +
  `scopes: spreadsheets.readonly drive.readonly` so it's usable from a generic HTTP Request
  node via `authentication: predefinedCredentialType` — different requirement than the native
  Sheets node would need, learned from the credential schema's conditional validation.

**First version (id `LRoZnIJjculgTB6F`, deleted) crashed on first test run** — 1 fetch
(`values:batchGet`, all 3 ranges, ~3MB response) fanned out to 3 parallel Transform/Upsert
branches. `Upsert GA Totals` died with `NodeCrashedError` ("n8n may have run out of memory").
n8n's stored execution data for the crashed run was all synthetic
`isArtificialRecoveredEventItem` placeholders, not real values — couldn't diagnose from that,
so reproduced the exact same Sheets fetch independently instead: response really is ~3MB,
and the `Daily metrics sheet` data itself is completely clean (consistent 10-column rows, no
anomalies), ruling out malformed data as the cause. Most likely real cause: each Upsert node
sent its **entire row set in one unbatched POST** (up to 11,350 rows for Geo data) — the same
class of problem already documented in `docs/project-architecture.md` for `orders-cool.json`
needing a batch-100 Loop Over Items node. Combined with the shared 3MB payload being cloned
across 3 parallel branches, this is very likely what exceeded the instance's memory.

**Second version (id `bBKco1x9Z8ulxLZV`, deleted) also failed, immediately** — redesigned as
3 self-contained Code nodes doing their own JWT-signed fetch via `this.getCredentials`/
`this.helpers.httpRequest`. Real error this time (not a crash, a clean `TypeError`):
`this.getCredentials is not a function`. This n8n instance (1.123.67 Cloud) runs Code nodes
through the newer isolated **JS Task Runner** architecture, which doesn't expose
`getCredentials` the way older in-process Code node sandboxes did. Confirms credentialed
calls need to go through native nodes, not Code nodes, on this instance — but also confirmed
the JWT-signing JS itself was correct (extracted and run locally against real Node.js v24
with the actual key, real Google OAuth token exchange succeeded independent of n8n).

**Third version (current, id `j4gatZqXaw9tk55x`) — back to native nodes for credentialed I/O,
fixed the real batching problem directly.** In the *first* crash, `Fetch GA Data` (the native
HTTP Request node with `predefinedCredentialType`/`googleApi`) had a green checkmark and no
error — only `Upsert` (the unbatched one) failed. So native-node Google auth was never
actually broken; conflating that with the batching problem led to an unnecessary Code-node
rewrite that broke on an unrelated missing API. This version: 3 separate native `Fetch`
nodes (own range each, no shared 3MB payload/fan-out clone), 3 native `Transform` Code nodes
(pure JS only — `$input.first()` and `return [...]`, nothing needing credentials or helpers),
each **emitting one output item per 100-row batch** instead of one item with all rows. The
`Upsert` nodes reference `$json.rows` (the current item) instead of `$input.all().map(...)` —
n8n's default behavior is to run a node once per input item, so this alone makes each POST
request naturally batch-sized without any Split-In-Batches loop node or its cyclic-graph
topology risk. `Log Sync` runs once per batch too (matches the existing precedent of the
Python backfill scripts logging once per chunk, not one aggregate row).

(Housekeeping: the private key briefly touched a temp file in `/tmp` instead of the session
scratchpad during local Node.js testing of the (now-abandoned) Code-node JWT approach —
caught and deleted immediately, along with the test script.)

**Second test run of the third version: real progress, one small bug.** `Fetch`/`Transform`
(6 batches for `Daily metrics sheet`, matching 580 rows / 100 correctly) /`Upsert` (6 items,
all empty — expected, `Prefer: return=minimal`) all completed — **confirms the per-item
batching design actually works.** `Log Sync Totals` then failed with `JSON parameter needs to
be valid JSON` — `$json.rows.length` in its expression was reading from its own immediate
input (`Upsert`'s empty output), not the batch data. Fixed by referencing the named upstream
node directly: `$('Transform GA Totals').item.json.rows.length` (n8n's cross-node
item-paired reference), same fix applied to all 3 Log Sync nodes. **Pushed via `PUT
/workflows/{id}` in place** — confirmed PUT works for workflows (unlike credentials, which
only support POST create + DELETE) — no more delete+recreate needed for future workflow
fixes, only for credential fixes.

**Realized I could self-test after all:** no execute-workflow API endpoint exists, but the
workflow has a Webhook Trigger — activating the workflow makes its production webhook live,
and `curl -X POST` to that URL triggers a real execution I can then inspect via
`GET /executions`. Used this to iterate the remaining two bugs without further manual UI
clicks:

- **Bug 3:** `analytics_ga_daily_source`'s real data proved `conversions` can be fractional
  (`696.21`) — same GA4 attribution-modeling behavior already handled correctly for Google
  Ads, missed applying the same caution to GA's migration. Fixed via
  `supabase/migrations/005_fix_ga_conversions_numeric.sql` (widened `conversions` on all 3
  GA tables, plus `begin_checkout_count`, from `INTEGER`/plan to `NUMERIC(12,4)` — safe
  widening on tables that already had real data). Verified via
  `information_schema.columns`, not just the apply output.
- **Bug 4:** Sheets returns `""` for some blank numeric cells, not `null` — my `?? null`
  fallbacks only caught actual null/undefined, not empty string, so `""` reached Postgres as
  an invalid numeric literal. Fixed by sanitizing each row array (`v === '' ? null : v`)
  before destructuring, in all 3 Transform nodes, rather than patching each field
  individually.
- Both fixes pushed via `PUT /workflows/{id}` in place, re-tested via the webhook each time.

**Confirmed fully working 2026-08-06**, verified against real row counts (not just execution
status): `analytics_ga_daily_totals` 580 rows, `analytics_ga_daily_source` 5,297 rows,
`analytics_ga_daily_geo` 11,350 rows — **exact match** to the live sheet's real row counts,
date range `2025-01-01` to `2026-08-03` across all three. Workflow id `j4gatZqXaw9tk55x`,
**already active** (from testing via the real production webhook). Added to
`N8N_WORKFLOW_IDS.md`.

### C. Backoffice API follow-ups
- [ ] **Blocked on Backoffice team:** confirm `backfill_route_gsi2pk.py` has run in production
      (needed to confirm `/route` fix — this is the same `/route` Lambda issue tracked in
      `project-architecture.md`'s history)
- [ ] **Blocked on product:** unscheduled orders count toward reported gigs/revenue? Slack question
      sent, awaiting answer
- [ ] If approved: add `includeUnscheduled=true` to all Fetch Orders nodes + backfill the
      historical unscheduled-order gap (63 in July 2026, 28 in June 2026)
- [ ] Update `docs/BACKOFFICE_API_ISSUES.md` with live re-verification: #9 (AVY) confirmed fixed,
      #6 (param validation) confirmed shipped, #13 (same-day lag) confirmed/explained,
      #2/#4 (decimal-cents) confirmed **still broken** despite docs claiming otherwise

### D. Deferred security fix
- [ ] Hardcoded Backoffice API key committed in workflow JSON + `scripts/reconcile_airtable_financials.py`
      — flagged during the sync-gap work (around `cfce55d`), never rotated/removed.
      Violates CLAUDE.md's "never put secrets in files" rule — should be prioritized over feature work.
      **Scope widened 2026-08-06:** `scripts/backfill_missing_months.py` (line 38) also
      hardcodes the same Backoffice key — found while using it as a reference for the new
      marketing backfill scripts. Same fix needs to cover this file too, not just the two
      originally named.

---

## Known doc inconsistency (needs a decision, not a guess)

`CLAUDE.md`'s "Future direction" section lists Edge Function deployment as **not yet done**.
`docs/project-architecture.md`'s Pending/Known Issues table and `CHANGELOG.md` both indicate
`n8n-trigger-sync` / `n8n-update-schedule` **are deployed** and were exercised as recently as
2026-08-06 (N8N key rotation). CLAUDE.md needs a manual edit to drop the stale line — flagging
here instead of auto-editing CLAUDE.md.

---

## Legacy items carried from `project-architecture.md` (unverified as of 2026-08-06)

- `/manual-order` full charge breakdown — low priority, accepted as out of scope (also in CLAUDE.md)
- Re-import updated workflow JSON (webhook trigger nodes) to live N8N — status unconfirmed
- Install Claude Code on MacBook-Pro-2 — low priority, local machine setup only
