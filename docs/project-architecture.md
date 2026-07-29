# Apukuski API Data Parser — Project Architecture & Status

## Purpose

This project builds an automated data pipeline that syncs order, route, and hub data from the Apukuski Backoffice API (AWS Lambda) into a Supabase PostgreSQL database on a rolling schedule. The Supabase database serves as the single source of truth for internal analytics, a Lovable-built status dashboard, and any other internal tooling that needs structured order data.

---

## System Architecture

```
┌─────────────────────────────┐
│  Backoffice API             │
│  AWS API Gateway + Lambda   │
│  eu-central-1               │
│  /order  /manual-order      │
│  /route  /hub               │
└────────────┬────────────────┘
             │ HTTPS
             ▼
┌─────────────────────────────┐
│  N8N Cloud                  │
│  apukuski.app.n8n.cloud     │
│  8 workflows (see below)    │
│  Scheduled + manual trigger │
└────────────┬────────────────┘
             │ REST API (service key)
             ▼
┌─────────────────────────────┐
│  Supabase                   │
│  PostgreSQL (t4g.micro)     │
│  Project: ybznbfezrdgzgptxkgul │
│  Tables: orders, routes,    │
│          hubs, sync_log     │
└────────────┬────────────────┘
             │ anon key (read-only)
             ▼
┌─────────────────────────────┐
│  Lovable Dashboard          │
│  Sync status + Run Now UI   │
│  (connected to Supabase)    │
└─────────────────────────────┘
             │ via Supabase Edge Functions
             ▼
┌─────────────────────────────┐
│  N8N (manual trigger)       │
│  Lovable → Edge Function    │
│  → N8N webhook/exec API     │
└─────────────────────────────┘
```

---

## Infrastructure

| Component | Details |
|---|---|
| Backoffice API | `https://qtml5qv6uk.execute-api.eu-central-1.amazonaws.com/production/` |
| N8N | `https://apukuski.app.n8n.cloud` (cloud-hosted) |
| Supabase | `https://ybznbfezrdgzgptxkgul.supabase.co` (Pro, t4g.micro) |
| GitHub repo | `jussi-chapeau/api_data_parser`, branch `claude/peaceful-tesla-tzc8kx` |
| Slack alerts | `#tech-alerts-sos` channel |

---

## Database Schema

All tables live in Supabase PostgreSQL. Migration file: `supabase/migrations/001_create_tables.sql`.

### `orders`
Primary data table. Covers both standard API orders (`is_manual=false`) and manual orders (`is_manual=true`).

- **Primary key:** `order_id` (text)
- Key columns: `organization_name`, `org_id`, `hub_id`, `order_state`, `order_type`
- Timestamps: `created_at`, `first_schedule`, `underway_at`, `in_transit_at`, `delivered_at`
- Financials: `platform_fee` (integer cents), `service_fee` (integer cents), `commission_rate` (float)
- JSONB blobs: `schedule`, `content`, `charge`, `review`, `manual_data`
- `route_id` — FK to routes table
- `synced_at` — when N8N last wrote this row

### `routes`
One row per courier route/run.

- **Primary key:** `route_id` (text)
- Key columns: `date`, `hub_id`, `partner`, `internal_cost` (cents)
- `order_ids` (jsonb array), `warehouse_order_ids` (jsonb array)
- `total_sales` (float)

### `hubs`
Reference data, rarely changes.

- **Primary key:** `hub_id` (text)
- Key columns: `hub_name`, `tz` (timezone string e.g. `Europe/Helsinki`)
- JSONB blobs: `opening_hours`, `hub_location`, `service_info`

### `sync_log`
Audit trail written by every N8N workflow run.

- `workflow` (text) — which workflow ran
- `started_at`, `completed_at` (timestamptz)
- `rows_upserted` (integer)
- `status` — `success` or `error`
- `error_message` (text)

---

## N8N Workflows

All workflows live in `n8n-workflows/` as importable JSON files. Supabase credentials are injected at import time via `sed` (never committed to git — see Security section).

### Sync Workflows

| Workflow | N8N ID | Schedule | Coverage |
|---|---|---|---|
| Orders Hot Sync | `9hWlvNyCs8HmfZly` | Every hour `0 * * * *` | Last 3 days |
| Orders Warm Sync | `CcOBd7IELOnbonYL` | Every 6h `15 */6 * * *` | Days 4–14 |
| Orders Cool Sync | `QKbM3UvkJ8Yjkhb1` | Daily `30 3 * * *` | Days 15–45 |
| Routes Sync | `cgEcgz89U6Rp7UJH` | Daily `0 4 * * *` | Last 45 days |
| Reference Data Sync | `D62F3xpZ443ZFUwa` | Weekly Mon `0 2 * * 1` | All hubs |
| Backfill (manual) | `JH2On4vSuJidzbyU` | Manual only | 2024-01-01 → today-46d |

### Monitoring Workflows

| Workflow | N8N ID | Schedule | Purpose |
|---|---|---|---|
| Health Check | `wY8x15hSqMBstbA8` | Every 15 min | Pings Supabase sync_log, alerts Slack on failure |
| Error Handler | `6hIScmlYADgaf16s` | Error trigger | Catches failures from any sync workflow, alerts Slack |

### Data Flow Inside Each Sync Workflow

```
Schedule Trigger
  → Set Date Range (Code node — calculates start_date/end_date)
  → Fetch /order (HTTP Request to Backoffice API)
  → Fetch /manual-order (HTTP Request to Backoffice API)
  → Transform Orders (Code node — normalise fields, convert timestamps)
  → Upsert to Supabase /rest/v1/orders (HTTP Request, Prefer: resolution=merge-duplicates)
  → Log to sync_log (HTTP Request to Supabase)
```

### Key Data Transformations (in N8N Code nodes)

- **Unix timestamps** → multiply by 1000 → `new Date(ts * 1000).toISOString()` (Backoffice API returns float seconds)
- **Platform fees (`/order`)** → API values are **excl. VAT** (net). Store top-level `platformFee` / `serviceFee` as integer cents in `platform_fee` / `service_fee`. Keep full `charge` breakdown JSON as-is.
- **Manual totals (`/manual-order`)** → API provides **no fee breakdown**. Only usable price is `charge.charge`, a euro string **including VAT**. Use as-is (accepted interim, confirmed with backend/ops 2026-07-23). Do not reverse-engineer net fees or margin from this field.
- **`/order` items** → `is_manual=false`, `manual_data=null`, `platform_fee`/`service_fee` from API
- **`/manual-order` items** → `is_manual=true`, `platform_fee=null`, `service_fee=null`, and:
  ```
  manual_data = {
    customer,
    additionalInfo,
    serviceFeeApplied,
    payment_method: charge.paymentMethod,
    total_incl_vat_eur: parseFloat(charge.charge),
    total_incl_vat_cents: Math.round(parseFloat(charge.charge) * 100),
    price_basis: "gross_incl_vat",
    source_field: "charge.charge"
  }
  ```
- All writes use `Prefer: resolution=merge-duplicates` header (Supabase upsert on PK conflict)

### Financial fields: platform vs manual

| | Platform (`is_manual=false`) | Manual (`is_manual=true`) |
|---|---|---|
| Source endpoint | `/order` | `/manual-order` |
| Price fields | Full `charge` breakdown + top-level fees | `charge.charge` total only |
| Tax basis | Fees excl. VAT (net) | Total **incl. VAT** (gross) |
| Partner / margin calc via API | Possible when breakdown present | **Not available** — ops calculates in Airtable |
| Lovable display rule | Use `platform_fee` / `service_fee` / `charge` | Use `manual_data.total_incl_vat_cents` (or raw `charge.charge`) |

Details and bug history: `data/AWS_API_charge_object_bug_report.md`.

---

## Supabase Edge Functions

Two Edge Functions act as a proxy between Lovable (frontend) and N8N, working around CORS restrictions.

### `n8n-trigger-sync`
**File:** `supabase/functions/n8n-trigger-sync/index.ts`  
**Purpose:** Lovable calls this to manually trigger a workflow execution.  
**Input:** `{ workflowId: string }`  
**Action:** POSTs to N8N `/executions` endpoint.

### `n8n-update-schedule`
**File:** `supabase/functions/n8n-update-schedule/index.ts`  
**Purpose:** Lovable calls this to change a workflow's cron schedule.  
**Input:** `{ workflowId: string, cronExpression: string }`  
**Action:** GETs the workflow JSON from N8N, updates the `scheduleTrigger` node's cron expression, PUTs it back.

Both functions require env vars:
- `N8N_API_URL` — `https://apukuski.app.n8n.cloud/api/v1`
- `N8N_API_KEY` — N8N cloud API key

---

## Lovable Dashboard

A Lovable app connected directly to Supabase (anon key, read-only). Features built:

- **Sync status overview** — shows last run time, rows upserted, status for each workflow (reads `sync_log`)
- **Run Now buttons** — calls Supabase Edge Function `n8n-trigger-sync` to manually kick a workflow
- **Schedule controls** — calls `n8n-update-schedule` to change cron frequency
- **Order counts** — live counts from `orders` table

The Lovable app uses the Supabase anon key (safe for frontend). Sensitive operations go through Edge Functions which hold the N8N API key server-side.

---

## Security

- **Supabase service key** is NEVER committed to git. Stored as the placeholder `SUPABASE_SERVICE_KEY` in workflow JSON files. Injected via `sed` when importing workflows to N8N.
- **GitHub push protection** is active on the repo and will block any commit containing the real key.
- **N8N API key** is stored in `.claude/settings.json` (gitignored) for local CLI Claude access. Also set as an Edge Function env var in Supabase.
- **Supabase anon key** is safe for frontend use — Lovable uses this directly.

---

## What Has Been Done

### Infrastructure
- [x] Supabase project created (Pro plan, t4g.micro — upgraded from t3.nano after stability issues)
- [x] Database schema created (`orders`, `routes`, `hubs`, `sync_log`)
- [x] All 8 N8N workflows imported and configured
- [x] N8N project folder: "API data parser & working DB" inside Apukuski workspace
- [x] Supabase credentials injected into all workflows (not the placeholder)

### Data Pipeline
- [x] Backoffice API URL confirmed: `https://qtml5qv6uk.execute-api.eu-central-1.amazonaws.com/production/`
- [x] Orders sync working: ~9,500+ orders loaded (2024-01-01 to present)
- [x] Hubs sync working: 98 hubs loaded
- [x] Routes sync: **not yet working** — `/route` Lambda returns an error (backend team needs to fix)
- [x] Backfill workflow: ran successfully for historical data

### Monitoring
- [x] Health check workflow running every 15 min
- [x] Slack alerts configured to `#tech-alerts-sos`
- [x] Error handler workflow connected to all sync workflows

### Sync Outage Fix (29 July 2026)
Every order sync had been failing since ~24 July. Three stacked defects in the
shared `Fetch Orders` + `Fetch Manual Orders` → `Merge` → `Transform` → `Upsert` chain:

1. **Dead Merge parameter.** Nodes are `typeVersion 3` but carried the v2 key
   `combinationMode`. v3 ignores it, falls back to match-by-fields, and errors with
   `You need to define at least one pair of fields in "Fields to Match"`. Every other
   Merge node in the n8n account already used the current key — these four were the
   only stragglers.
2. **Wrong merge mode (silent data corruption).** `mode: combine` pairs items
   *positionally*, so output truncated to the shorter input (527 → 142) and merged
   platform-order fields into manual-order records. Correct mode is **`append`**.
   Verified afterwards: 0 manual rows carry `charge.vatPrice` or `platform_fee`.
3. **Bulk nodes lacked `executeOnce`.** `Upsert Orders` / `Log Sync` build their body
   from `$input.all()`, so one request already holds every row — but n8n ran them once
   per item, re-serialising the whole array each time. At ~500 items this produced
   `possible out-of-memory issue`.

`orders-cool` additionally routes upserts through a **Loop Over Items** node
(batch 100), because a single ~500-row request exceeded what the micro instance
would accept (Cloudflare 522).

Repairing this involved repeated sync runs that overloaded Supabase and took the
project down for ~25 minutes. When re-running syncs after a fix, trigger **one**
workflow at a time and confirm it succeeds before the next.

Post-fix verification (all match live Backoffice API):

| Window | Supabase | API truth |
|---|---|---|
| June 2026 | 553 (424 platform / 129 manual) | 553 / 424 / 129 |

### Stability Fix (earlier, July 2026)
A critical issue caused repeated Supabase crashes:
- **Root cause:** Duplicate workflows (2x Routes Sync, 2x Orders Hot) running simultaneously caused lock contention
- **Secondary cause:** `reference-sync` and `routes-sync` had literal `SUPABASE_SERVICE_KEY` placeholder instead of the real key, generating 401 errors on every write
- **Fix applied:** Deleted 4 duplicate workflows, deactivated all workflows while Supabase recovered, staggered cron schedules to prevent simultaneous writes
- **Status:** Workflows being re-activated one by one after cleanup

### Dashboard
- [x] Lovable dashboard built with sync status, Run Now buttons, schedule controls
- [ ] Edge Functions (`n8n-trigger-sync`, `n8n-update-schedule`) need to be deployed to Supabase

### Documentation
- [x] `docs/supabase-api.md` — API reference for colleagues
- [x] `N8N_WORKFLOW_IDS.md` — all workflow IDs and schedules
- [x] This document

---

## Pending / Known Issues

| Issue | Priority | Owner |
|---|---|---|
| `/route` Lambda returns error / timeout — routes table empty | High | Backend team |
| Deploy Edge Functions to Supabase (`supabase functions deploy`) | Medium | DevOps |
| Re-activate all 6 canonical workflows after stability fix | High | Done via CLI Claude |
| Lovable "Run Now" buttons need webhook trigger nodes added to workflows | Medium | N8N config |
| N8N transform: populate `manual_data.total_incl_vat_*` from `charge.charge` | Done in repo JSON | Re-import/update live N8N workflows |
| `/manual-order` full charge breakdown | Low (accepted interim) | Backend / later |
| Install Claude Code on MacBook-Pro-2 (`sudo npm install -g @anthropic-ai/claude-code`) | Low | Local setup |

---

## Repository Structure

```
api_data_parser/
├── n8n-workflows/
│   ├── orders-hot.json          # Hourly sync, last 3 days
│   ├── orders-warm.json         # 6h sync, days 4-14
│   ├── orders-cool.json         # Daily sync, days 15-45
│   ├── routes-sync.json         # Daily sync, last 45 days
│   ├── reference-sync.json      # Weekly hub sync
│   ├── backfill.json            # Manual full-history backfill
│   ├── health-check.json        # 15-min Supabase health check
│   └── fetch-api-url.json       # Utility: get API URL from CloudFormation
├── supabase/
│   ├── migrations/
│   │   └── 001_create_tables.sql
│   └── functions/
│       ├── n8n-trigger-sync/index.ts
│       └── n8n-update-schedule/index.ts
├── docs/
│   ├── project-architecture.md  # This file
│   └── supabase-api.md          # API reference for data consumers
└── N8N_WORKFLOW_IDS.md          # Workflow IDs and schedules
```

---

## How to Work on This Project

### Adding a new sync workflow
1. Create the JSON in `n8n-workflows/` following the existing pattern
2. Use `SUPABASE_SERVICE_KEY` as the placeholder in auth headers
3. Import to N8N via the UI or API, injecting the real key with `sed`
4. Add the workflow ID to `N8N_WORKFLOW_IDS.md`

### Changing a cron schedule
- Via N8N UI directly, or
- Via the `n8n-update-schedule` Edge Function (used by Lovable)

### Querying data
See `docs/supabase-api.md` for full query reference and code examples.

### Accessing N8N programmatically
N8N cloud blocks API calls from most external environments (egress policy). Options:
- Use Claude Code CLI locally (configured in `.claude/settings.json`)
- Use `curl` from a local terminal
- Use the N8N web UI directly
