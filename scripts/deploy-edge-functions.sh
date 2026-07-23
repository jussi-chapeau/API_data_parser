#!/usr/bin/env bash
# Deploy Supabase Edge Functions for Lovable → N8N integration.
#
# Prerequisites:
#   export SUPABASE_ACCESS_TOKEN="..."   # from https://supabase.com/dashboard/account/tokens
#   export N8N_API_KEY="..."             # apukuski n8n cloud API key
#
# Usage:
#   ./scripts/deploy-edge-functions.sh

set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT_REF="ybznbfezrdgzgptxkgul"

if [[ -z "${SUPABASE_ACCESS_TOKEN:-}" ]]; then
  echo "ERROR: SUPABASE_ACCESS_TOKEN is not set." >&2
  echo "Create a token at https://supabase.com/dashboard/account/tokens" >&2
  exit 1
fi

if [[ -z "${N8N_API_KEY:-}" ]]; then
  echo "ERROR: N8N_API_KEY is not set." >&2
  exit 1
fi

export SUPABASE_ACCESS_TOKEN

echo "Setting secrets..."
npx supabase secrets set \
  N8N_API_URL="https://apukuski.app.n8n.cloud/api/v1" \
  N8N_API_KEY="$N8N_API_KEY" \
  N8N_WEBHOOK_BASE="https://apukuski.app.n8n.cloud/webhook" \
  --project-ref "$PROJECT_REF"

echo "Deploying n8n-trigger-sync..."
npx supabase functions deploy n8n-trigger-sync --project-ref "$PROJECT_REF"

echo "Deploying n8n-update-schedule..."
npx supabase functions deploy n8n-update-schedule --project-ref "$PROJECT_REF"

echo "Done. Functions:"
echo "  https://${PROJECT_REF}.supabase.co/functions/v1/n8n-trigger-sync"
echo "  https://${PROJECT_REF}.supabase.co/functions/v1/n8n-update-schedule"
