# IDEAS — tool roadmap

Built-in tools (v0.1): `db_read`, `bi_orders_report`, `bi_revenue_report`,
`backoffice_get_hubs`, `search_knowledge`, `flag_uncertainty`.

## Quick wins

### 1. bi_sync_health
One call: last sync per workflow, error rate, data freshness lag vs now.
Source: `sync_log` + max(`orders.synced_at`).

### 2. bi_gig_summary
Standard gig count report: platform + manual, created vs delivered,
per week/month. Encodes gotchas from `vault/BI.md` so LLM can't mix metrics.

### 3. bi_hub_scorecard
Per hub: order count, revenue, manual share, avg commission_rate.
Source: Supabase SQL join orders + hubs.

### 4. bi_weekly_slack_report
Cron via n8n → POST /chat "generate weekly report" → bridge notify #bi.

## Medium

### 5. bi_origin_breakdown
Orders by `origin` (app vs integrations) — platform only, July 2026+.

### 6. bi_manual_pricing_coverage
How many manual rows have `total_incl_vat_cents` vs raw-only charges.

### 7. bi_route_capacity
When routes table populates: orders per route, partner utilization.

## Cross-chief

### 8. ask_bi tool for Aada/Veera
"Onko kapasiteettia ensi viikolla?" before promising delivery dates.

## Adding a tool

1. Write JSON schema + async function in `app/tools.py`
2. Register in `TOOLS_SCHEMA` and `TOOL_IMPLEMENTATIONS`
3. Document usage in `vault/TOOLS.md`
4. Prefer fixed SQL/report logic over free-form LLM math
