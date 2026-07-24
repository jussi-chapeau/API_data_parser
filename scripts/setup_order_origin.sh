#!/usr/bin/env bash
# Add orders.origin column and backfill from API.
#
# Step 1 requires SUPABASE_ACCESS_TOKEN (dashboard → Account → Access Tokens):
#   export SUPABASE_ACCESS_TOKEN="..."
#   ./scripts/setup_order_origin.sh
#
# Or run the SQL manually in Supabase SQL Editor:
#   ALTER TABLE orders ADD COLUMN IF NOT EXISTS origin TEXT;
#   CREATE INDEX IF NOT EXISTS idx_orders_origin ON orders (origin) WHERE origin IS NOT NULL;

set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== 1. Apply migration ==="
if [[ -n "${SUPABASE_ACCESS_TOKEN:-}" ]]; then
  python3 scripts/apply_supabase_migration.py supabase/migrations/002_add_origin_to_orders.sql
else
  echo "SKIP: SUPABASE_ACCESS_TOKEN not set."
  echo "Run migration SQL manually in Supabase dashboard, then re-run this script."
  exit 1
fi

echo "=== 2. Backfill July origins from API ==="
python3 scripts/backfill_order_origin.py --start 2026-07-01

echo "=== 3. Trigger hot sync (recent orders) ==="
curl -sS -X POST "https://apukuski.app.n8n.cloud/webhook/orders-hot-sync" \
  -H "Content-Type: application/json" \
  -d '{"source":"setup_order_origin"}'

echo
echo "Done."
