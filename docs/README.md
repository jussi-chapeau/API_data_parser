# docs/ — Apukuski API Data Parser

Structured reference for this project. Each file owns **one topic**. These docs are the
**source of truth for architecture and behavior** — keep them in sync with code in the same change.

| Doc | What's in it |
|-----|--------------|
| [project-architecture.md](project-architecture.md) | Pipeline, N8N workflows, Supabase schema, Lovable, security, status |
| [supabase-api.md](supabase-api.md) | PostgREST query reference for consumers |
| [GOTCHAS.md](GOTCHAS.md) | Hard-won lessons — read before touching pricing, sync, or metrics |
| [DECISIONS.md](DECISIONS.md) | Chronological design decisions and why |

Related material outside `docs/`:

| Location | What's in it |
|----------|--------------|
| [`../CLAUDE.md`](../CLAUDE.md) | Agent behavior rules (read every session) |
| [`../N8N_WORKFLOW_IDS.md`](../N8N_WORKFLOW_IDS.md) | Production N8N workflow IDs |
| [`../data/AWS_API_charge_object_bug_report.md`](../data/AWS_API_charge_object_bug_report.md) | Manual vs platform charge object analysis |
| [`../CHANGELOG.md`](../CHANGELOG.md) | Deploy log (skim version) |

## The one-line summary

Backoffice API → N8N → Supabase Postgres → Lovable dashboard, keeping Apukuski order/route/hub
data synced for analytics and ops tooling.
