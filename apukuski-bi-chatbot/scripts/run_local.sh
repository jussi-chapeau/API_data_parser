#!/usr/bin/env bash
# Local dev: bootstrap .env, optional Supabase role, start server.
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"

python3 scripts/setup_local_env.py

if [[ -n "${SUPABASE_ACCESS_TOKEN:-}" ]]; then
  set -a && source .env && set +a
  python3 scripts/setup_supabase_role.py
fi

echo "Starting uvicorn on :8080 (needs OPENROUTER_API_KEY in .env for /chat)"
exec uvicorn main:app --reload --host 0.0.0.0 --port 8080
