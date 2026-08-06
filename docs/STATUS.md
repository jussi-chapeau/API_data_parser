# STATUS — Current Work & Session Handoff

This is the **shared handoff doc** between Claude Code, Cursor, and any other agent working
this repo. Whoever ends a session updates this file with what changed and what's still open;
whoever starts a session reads it first, before assuming what's done vs. pending.

Update this file, don't create a new one — one canonical status doc avoids drift.

**Last updated:** 2026-08-06

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
  - `Supermetrics export` → header row 3, 14,039 rows, **but data ends 2026-05-27** — ~10
    weeks stale relative to today despite the file being freshly exported today. **Need to
    check the live sheet** — either the Meta Supermetrics refresh is broken/paused, or Meta's
    reporting has a longer lag than expected. Don't assume this gap is normal.
- **Google Ads** (`Export report Google Ads - Dasboard.xlsx`) — **messy, 3 different candidate
  data tabs, no single clean source**:
  1. `Export to Apukuski Dashboard` — tidy, campaign-day grain w/ budget/status fields, header
     row 13 (rows 7-8 are an unrelated "Total: Account" summary block to skip), 44,000 rows,
     **but stale — data stops 2025-12-30** (~7 months behind)
  2. `Supermetrics` — tidy, campaign-day grain w/ conversion value, header row 17, 3,826 rows,
     **also stale — stops 2026-04-20** (~3.5 months behind)
  3. `Export to Apukuski Dashboard - ` (note trailing space in tab name) — **wide/pivoted**:
     one row per day, columns are `<campaign_id>|<campaign name> (<metric>)` baked per
     campaign. Most current (May 1 – Jul 31 2026) but only 3 months deep, and the schema
     itself breaks/needs re-mapping every time a campaign is added or removed. Bad shape for
     programmatic ingestion as-is.

  None of the 3 alone gives both full history and current data in a stable schema. Likely
  explanation: 1 and 2 are abandoned/superseded Supermetrics queries from earlier dashboard
  iterations that stopped refreshing; 3 is the one still being refreshed but was built as a
  pivot table for human dashboard viewing, not for automation.

  **Recommendation (needs a decision, not a guess):** get whoever owns the Supermetrics
  connector to add one new tidy long-format Google Ads query — `Date, Campaign ID, Campaign
  name, Impressions, Clicks, Cost, Conversions, Conversion value` — mirroring the Meta tab's
  shape, refreshed going forward. Backfill history from tabs 1/2 as a one-time best-effort
  load (schemas differ slightly between them and from the new query, so backfill needs its
  own mapping, separate from the ongoing sync). Ingesting tab 3 as-is is possible but every
  campaign add/remove would require an N8N workflow change — fragile long-term.

**Decisions made 2026-08-06:**
- [ ] **Google Ads:** go with new tidy Supermetrics query (Date, Campaign ID, Campaign name,
      Impressions, Clicks, Cost, Conversions, Conversion value), refreshed going forward.
      Backfill separately from tabs 1/2 as best-effort. **Blocked:** need someone to actually
      create this query in Supermetrics before the N8N workflow/migration for Google Ads can
      be finalized — GA and Meta are not blocked by this and can proceed first.
- [ ] **Meta:** Jussi is investigating why the live sheet's Supermetrics refresh appears
      stuck at 2026-05-27 — fix owned by him, not blocking other B work. Once fixed, confirm
      the live sheet is current before building the Meta N8N workflow.
- [x] Google Sheets + Docs credential path decided — service account, see instructions below
      *(TODO once actually created in N8N: mark done, note credential name)*
- [ ] Decide: full history backfill vs. recent cutoff (Meta/GA can go back to 2025-01-01 cleanly;
      Google Ads full history is blocked on the schema decision above)
- [ ] Supabase migration for marketing tables — table list may need to grow beyond the original
      5 (`ads_google_campaign_daily`, `ads_meta_placement_daily`, `analytics_ga_daily_totals`,
      `analytics_ga_daily_source`, `analytics_ga_daily_geo`) once Google Ads shape is settled —
      the extra fields in the real sheets (budget, status, conversion value, lost-impression-share)
      weren't in the original plan
- [ ] Build N8N workflow(s): Sheets read → parse (locale numbers, per-tab header offsets) → upsert → sync_log
- [ ] Backfill historical marketing data

#### Google Sheets + Docs credential setup (one service account, covers both)

Docs write access is for a future workstream, not yet scoped — set the service account and
API enablement up now, defer sharing individual Docs until that work starts.

1. Google Cloud Console → new or existing project (can reuse this same project for workstream
   A's Maps Geocoding API key — one project, multiple APIs enabled is fine).
2. APIs & Services → Library → enable **Google Sheets API**, **Google Docs API**, and
   **Google Drive API** (Drive is needed for n8n's file picker to browse by name; without it
   you can still target files by ID/URL directly).
3. IAM & Admin → Service Accounts → Create Service Account (e.g. `n8n-marketing-sync`). No
   project IAM role needed — access comes from per-file sharing, not IAM.
4. On the service account → Keys → Add Key → JSON. Download it — treat like the Supabase
   service key: never commit it, never paste it in docs/chat, N8N's credential store only.
5. Share access per file, least-privilege:
   - Each of the 3 marketing Sheets → Share → add the service account email
     (`n8n-marketing-sync@<project-id>.iam.gserviceaccount.com`) → **Viewer**.
   - Docs: nothing to share yet — do this later, per-doc, with **Editor**, when that
     workstream starts.
6. In N8N: Credentials → New → the Google service-account credential type (shared across
   Sheets/Docs/Drive nodes in n8n) → paste the service account email + private key from the
   JSON. Name it something identifiable, e.g. "Google Service Account — Apukuski Marketing".
7. Verify: add a Google Sheets node, pick this credential, point it at one of the 3 sheets by
   URL, run a manual "Read Rows" test before wiring up the real workflow.
8. When Docs write work starts later: same credential, just share the specific target Doc(s)
   with Editor access and add a Google Docs node — no Cloud Console changes needed.

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
