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
- [ ] Confirm exact tab names + ranges — three source exports were dropped in `data/` on 2026-08-06
  (`Export Google Analytics - Apukuski Dashboard.xlsx`, `Export Meta - Dashboard.xlsx`,
  `Export report Google Ads - Dasboard.xlsx`, untracked) — may unblock this immediately
- [ ] Set up Google Sheets read credential in N8N
- [ ] Decide: full history backfill vs. recent cutoff
- [ ] Supabase migration for 5 new tables: `ads_google_campaign_daily`, `ads_meta_placement_daily`,
      `analytics_ga_daily_totals`, `analytics_ga_daily_source`, `analytics_ga_daily_geo`
- [ ] Build N8N workflow(s): Sheets read → parse (locale numbers) → upsert → sync_log
- [ ] Backfill historical marketing data

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
