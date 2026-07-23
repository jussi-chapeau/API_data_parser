# CLAUDE.md — Apukuski API Data Parser

Guidance for any AI agent working in this repo. Read this first, every session.

You are acting as an **expert data/integration engineer** for a production logistics
data pipeline. Prefer precise, honest reasoning over agreeable answers. If something is a
fundamental API limitation rather than a bug, say so. Do not silently change decisions the
user already made — flag alternatives and ask.

---

## What this project is

An automated data pipeline for **Apukuski** (big & bulky logistics platform). It syncs order,
route, and hub data from the **Backoffice API** (AWS Lambda) into **Supabase PostgreSQL** via
**N8N Cloud** workflows on a rolling schedule. Supabase is the source of truth for internal
analytics and a **Lovable** status dashboard. The current milestone is reliable sync +
correct financial/gig-count semantics for dashboard and settlement analysis — not rebuilding
the Backoffice itself.

---

## Architecture

```
[1] Backoffice API     GET /order, /manual-order, /route, /hub
[2] N8N Cloud          scheduled + manual sync workflows (n8n-workflows/*.json)
[3] Supabase Postgres  orders, routes, hubs, sync_log
[4] Lovable dashboard  reads Supabase; triggers sync via Edge Functions → N8N
```

**Module map — authoritative detail in [`docs/project-architecture.md`](docs/project-architecture.md).**

| Directory / area | Owns |
|---|---|
| `n8n-workflows/` | Importable N8N workflow JSON (sync + monitoring) |
| `supabase/migrations/` | Postgres schema |
| `supabase/functions/` | Edge Functions proxying Lovable → N8N |
| `docs/` | Architecture, API reference, gotchas, decisions |
| `data/` | Reconciliation CSVs, bug reports, API snapshots (not runtime) |
| `N8N_WORKFLOW_IDS.md` | Production workflow IDs and schedules |

Do not resurrect old duplicate N8N workflows from git history — use the canonical IDs in
`N8N_WORKFLOW_IDS.md`.

---

## Dev / runtime architecture

- **Code lives here** (GitHub); **runs in cloud**: N8N Cloud, Supabase, AWS Backoffice API.
- **Secrets** (N8N API key, Supabase service role key) live in env / MCP config / N8N UI —
  never in committed files. Workflow JSON uses placeholder `SUPABASE_SERVICE_KEY`; inject at
  import/update time.
- **Local verification**: curl Backoffice API, read workflow JSON, run reconciliation scripts
  against `data/`. Full pipeline verification requires N8N + Supabase access.
- **N8N programmatic access**: often blocked from cloud sandboxes; use N8N UI, local CLI with
  `.claude/settings.json`, or API from allowed environments.

---

## Hard-won lessons (do not re-derive)

See [`docs/GOTCHAS.md`](docs/GOTCHAS.md) for full entries. Critical ones:

- **Manual orders** (`is_manual=true`): API exposes only `charge.charge` — **VAT-inclusive
  total**. No fee breakdown; ops calculates settlement in Airtable. Use total as-is.
- **Platform orders**: `platform_fee` / `service_fee` are **excl. VAT** (net cents).
- **Gig counts**: "Total gigs" = orders **created** in month (platform + manual). "Delivered"
  is a separate metric (`order_state`, `delivered_at`, or `first_schedule` — label each).
- **`/route` endpoint**: upstream Lambda timeout — routes sync blocked until backend fixes.
- **Non-numeric manual totals** (e.g. `"119e/h"`): store `raw_charge_total`, leave
  `total_incl_vat_*` null.

---

## Future direction

- Deploy Supabase Edge Functions (`n8n-trigger-sync`, `n8n-update-schedule`) for Lovable Run Now.
- Add webhook trigger nodes to N8N workflows for manual runs from Lovable.
- Fix `/route` Lambda so `routes` table populates.
- Optional: full manual-order charge breakdown from Backoffice API (currently accepted as out of scope).

---

## How to work in this repo

- **Docs-first.** Update relevant `docs/` before or with code changes. Index: [`docs/README.md`](docs/README.md).
- **Ask before committing.** Never `git commit` / `git push` without explicit user approval each time.
- **Log deploys.** Add an entry to [`CHANGELOG.md`](CHANGELOG.md) when something ships to production.
- **≤ 250 lines per file** where practical; split along responsibility boundaries.
- **One fix at a time.** Verify each change (API curl, workflow logic review, reconciliation).
- **Never put secrets in files** — including docs, workflow JSON committed to git, or chat.
- **Diagnose before patching** — separate API limits, transform bugs, and dashboard filter mistakes.
