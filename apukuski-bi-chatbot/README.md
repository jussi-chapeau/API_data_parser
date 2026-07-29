# apukuski-bi-chatbot

**Bertta** — Apukuskin Business Intelligence AI for Slack.

Queries **Supabase** (synced order/route/hub data from the `api_data_parser`
pipeline) with optional **live Backoffice API** fallback. Same architecture
as other Apukuski chiefs: FastAPI + OpenRouter tool loop + vault prompts.

## Structure

```
main.py                 FastAPI: /chat, /health
app/
  config.py             Environment variables
  llm.py                Vault system prompt + tool loop
  tools.py              db_read, bi_orders_report, bi_revenue_report, …
vault/                  SOUL, IDENTITY, COMPANY, BI, TOOLS (agent context)
docs/
  INTEGRATION.md        Railway, Slack bridge, read-only DB
  data/supabase-api.md  Schema reference (sync from api_data_parser)
IDEAS.md                Tool roadmap
```

## Quick start

```bash
cd apukuski-bi-chatbot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill OPENROUTER_API_KEY + DATABASE_URL
set -a && source .env && set +a
uvicorn main:app --reload --port 8080

curl -X POST localhost:8080/chat -H "Content-Type: application/json" \
  -d '{"message":"Montako tilausta luotiin kesäkuussa 2026?","session_id":"dev"}'
```

## Production

See `docs/INTEGRATION.md` — Railway deploy, read-only Postgres role,
`#bi` Slack routing via apukuski-slack-bridge.

## Design principles

1. Numbers only from tools — never from model memory (`vault/SOUL.md`).
2. Always state date basis: `created_at` vs `first_schedule`.
3. Never mix platform (excl VAT fees) and manual (incl VAT total) semantics.
4. Prefer `bi_orders_report` (Supabase) over live Backoffice unless freshness requires it.
5. Read-only everywhere.

## Related repos

| Repo | Role |
|---|---|
| `api_data_parser` | N8N → Supabase sync pipeline, schema migrations |
| `apukuski-slack-bridge` | Routes Slack channels to chief services |
| This repo | BI chatbot service |
