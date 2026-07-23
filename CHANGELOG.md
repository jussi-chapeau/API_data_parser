# Changelog

One entry per deploy to production — date, what shipped, why it matters. Detail in `git log`.

## 2026-07-23

- **Manual-order VAT total transform** — N8N Transform Orders now writes
  `manual_data.total_incl_vat_*` from `charge.charge`; platform/manual financial semantics
  documented. Enables consistent Lovable display for manual order totals.

- **Claude/agent setup integrated** — Root `CLAUDE.md`, `docs/GOTCHAS.md`, `docs/DECISIONS.md`,
  `docs/README.md` added; template archived under `docs/archive/`.

## 2026-07-13

- **Project architecture docs** — `docs/project-architecture.md`, `docs/supabase-api.md` added
  for pipeline and consumer reference.

- **Routes sync blocked** — `/route` Lambda timeout documented; upstream fix pending.

## 2026-06

- **Initial pipeline** — N8N workflows syncing orders, hubs to Supabase; tiered hot/warm/cool
  schedules; health check + Slack alerts to `#tech-alerts-sos`.
