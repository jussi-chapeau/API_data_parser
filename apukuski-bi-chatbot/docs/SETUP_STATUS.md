# Setup status (2026-07-29)

## Done from cloud agent

| Step | Status | Notes |
|---|---|---|
| Local deps | ✅ | `pip install -r requirements.txt` |
| `.env` bootstrap | ✅ | `python3 scripts/setup_local_env.py` — Backoffice key from N8N |
| Server `/health` | ✅ | Running on port 8080 |
| Backoffice tools | ✅ | `backoffice_get_hubs` → 99 hubs |
| Supabase REST | ✅ | 10,804 orders via service key (sanity check) |
| Git commit | ✅ | Local repo on `main` |

## Blocked — needs you

### 1. GitHub repo + push
Cloud agent token cannot create repos under `jussi-chapeau`. Run locally:

```bash
cd apukuski-bi-chatbot
gh repo create jussi-chapeau/apukuski-bi-chatbot --private --source=. --remote=origin --push
```

### 2. Supabase read-only role
Add to `.claude/settings.json` or export:

```bash
export SUPABASE_ACCESS_TOKEN="sbp_..."
cd apukuski-bi-chatbot
python3 scripts/setup_local_env.py   # creates BI_DB_PASSWORD in .env
set -a && source .env && set +a
python3 scripts/setup_supabase_role.py
```

Or paste SQL from `docs/INTEGRATION.md` in Supabase SQL Editor (use `BI_DB_PASSWORD` from `.env`).

### 3. OPENROUTER_API_KEY
Add to `.env` for `/chat` LLM replies. Without it, `/health` works but chat fails.

### 4. Railway + Slack
- Install Railway CLI, `railway login`, connect repo, set env vars from `.env`
- In `apukuski-slack-bridge`: `CHIEF_BI_URL=https://<railway-url>`, route `#bi`

### Postgres note
Direct `DATABASE_URL` postgres connection failed from cloud agent (`Network is unreachable` to `db.*.supabase.co`). Works from Railway/your laptop once role exists. Use `scripts/smoke_test.py` locally after step 2.

## Quick local commands

```bash
cd apukuski-bi-chatbot
pip install -r requirements.txt
python3 scripts/setup_local_env.py
# fill OPENROUTER_API_KEY in .env
set -a && source .env && set +a
python3 scripts/smoke_test.py
uvicorn main:app --reload --port 8080
```
