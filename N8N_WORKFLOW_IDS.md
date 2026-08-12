# N8N Workflow IDs

Last verified: 2026-08-12

## Sync Workflows

| Workflow | ID | Schedule | Status |
|---|---|---|---|
| Orders Hot Sync (last 3 days) | 9hWlvNyCs8HmfZly | Every hour | Active |
| Orders Warm Sync (days 4-14) | CcOBd7IELOnbonYL | Every 6h | Active |
| Orders Cool Sync (days 15-45) | QKbM3UvkJ8Yjkhb1 | Daily 03:00 | Active |
| Routes Sync (last 45 days) | cgEcgz89U6Rp7UJH | Daily 04:00 | Active |
| Reference Data Sync (hubs weekly) | D62F3xpZ443ZFUwa | Weekly Mon 02:00 | Active |
| Backfill (manual, full history) | JH2On4vSuJidzbyU | Manual + webhook | Active |
| Ads Google Daily Sync (last 30 days) | CZbzvcmagNxvC1JN | Daily 05:00 | Active |
| Ads Meta Daily Sync (last 30 days) | vuQOMC0tnTaZkMTC | Daily 05:00 | Active |
| Analytics GA Daily Sync | j4gatZqXaw9tk55x | Daily 06:00 | Active |

## Payments Sync Workflows

Event-driven, not scheduled — see `docs/STATUS.md` workstream E for why (Paytrail has no
list/date-range API; only create-payment + webhook-callback).

| Workflow | ID | Trigger | Status |
|---|---|---|---|
| Paytrail Payment Callback Tracking | iXwwwZyEkD1ZsrYA | Webhook (Paytrail callback) | Active |

**Not owned by `api_data_parser`** — this workflow belongs to the offer/booking automation
(PDF receipts, emails, order creation via `/order/import`). We only added the 3
`Payments: *` nodes (a parallel branch off `Code in JavaScript1`, `continueOnFail: true` on
each so a bug there can't break the existing customer-facing flow) that write to
`payments_paytrail`. Tracked here as `n8n-workflows/paytrail-payment-callback-tracking.json`
because it now also feeds this repo's Supabase tables — don't assume ownership of the rest
of the workflow when editing.

## Monitoring Workflows

| Workflow | ID | Schedule | Status |
|---|---|---|---|
| Supabase Health Check | wY8x15hSqMBstbA8 | Every 15 min | Active |
| Sync Error Handler | 6hIScmlYADgaf16s | Error trigger | Active |

## Alerts
- Slack channel: #tech-alerts-sos
- Slack credential: GutThvCLCxxw08G3 (Slack account 2)
- Alerts on: Supabase down, any sync workflow failure

## Infrastructure
- N8N: https://apukuski.app.n8n.cloud
- Supabase: https://ybznbfezrdgzgptxkgul.supabase.co
- Backoffice API: https://qtml5qv6uk.execute-api.eu-central-1.amazonaws.com/production/
- Repo: jussi-chapeau/api_data_parser, branch claude/peaceful-tesla-tzc8kx
