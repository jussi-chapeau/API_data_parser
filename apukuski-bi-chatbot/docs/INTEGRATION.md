# INTEGRATION — apukuski-bi-chatbot production setup

## 1. Railway deploy

```bash
cd apukuski-bi-chatbot
git init && git add -A && git commit -m "apukuski-bi-chatbot v0.1"
# Create GitHub repo apukuski/apukuski-bi-chatbot, push, connect Railway
# Procfile starts: uvicorn main:app --host 0.0.0.0 --port $PORT
```

Set env vars from `.env.example` in Railway dashboard.

## 2. Supabase read-only database role (recommended)

Bertta connects via `DATABASE_URL` to Supabase Postgres (pooler, session mode).

```sql
-- Run in Supabase SQL editor
CREATE ROLE bi_chatbot_readonly WITH LOGIN PASSWORD 'CHANGE-ME-STRONG';
GRANT CONNECT ON DATABASE postgres TO bi_chatbot_readonly;
GRANT USAGE ON SCHEMA public TO bi_chatbot_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO bi_chatbot_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT ON TABLES TO bi_chatbot_readonly;
```

Connection string format:
```
postgresql://bi_chatbot_readonly:PASSWORD@db.ybznbfezrdgzgptxkgul.supabase.co:5432/postgres
```

Or Supabase pooler (transaction mode) — use session mode for prepared statements.

The SQL guard in `db_read` is a second layer; the DB role is the primary safety.

## 3. Backoffice API (optional)

For `bi_revenue_report` and `backoffice_get_hubs`:

```
BACKOFFICE_API_URL=https://qtml5qv6uk.execute-api.eu-central-1.amazonaws.com/production/
BACKOFFICE_API_KEY=...
```

## 4. Slack: #bi channel

Route `#bi` → this service in `apukuski-slack-bridge` (same pattern as #aspa, #partners):

```
CHIEF_BI_URL=https://<railway-url>
```

Bridge POSTs to `/chat` with `session_id` per thread.

## 5. Training service: /opeta bi

```
TRAINING_SERVICE_URL=https://...
CHIEF_NAME=bi
```

Register `bi` in training-service chief list and slack-bridge `/opeta` command.

## 6. Smoke tests

```bash
BI_URL=https://<railway-url>
curl $BI_URL/health

curl -X POST $BI_URL/chat -H "Content-Type: application/json" -d \
  '{"message":"Montako tilausta on Supabasessa yhteensä?","session_id":"smoke"}'

curl -X POST $BI_URL/chat -H "Content-Type: application/json" -d \
  '{"message":"Onko viimeisin sync onnistunut?","session_id":"smoke"}'
```

## 7. Keeping schema docs in sync

When `api_data_parser` changes Supabase schema or pricing semantics, copy
updated `docs/supabase-api.md` into this repo's `docs/data/` and adjust
`vault/BI.md` if needed.

## Known limits (v0.1)

- In-memory sessions — lost on restart (Redis optional later)
- `routes` table may be empty while `/route` Lambda timeout persists upstream
- Manual order revenue depends on `manual_data.total_incl_vat_cents` backfill
