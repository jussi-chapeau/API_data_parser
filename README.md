# Backoffice API → Supabase Data Pipeline

Automated data synchronization pipeline that extracts data from the Chapeau Backoffice API and loads it into Supabase for analytics and dashboarding.

## Architecture

```
┌─────────────────────┐     ┌─────────────────────┐     ┌─────────────────────┐
│   Backoffice API    │     │       N8N Cloud     │     │      Supabase       │
│   (AWS Lambda)      │────▶│   (Orchestration)   │────▶│    (PostgreSQL)     │
│                     │     │                     │     │                     │
│  /order             │     │  orders-hot         │     │  orders (9,500+)    │
│  /manual-order      │     │  orders-warm        │     │  routes             │
│  /route             │     │  orders-cool        │     │  hubs (98)          │
│  /hub               │     │  routes-sync        │     │  sync_log           │
│                     │     │  reference-sync     │     │                     │
│                     │     │  backfill           │     │                     │
└─────────────────────┘     └─────────────────────┘     └─────────────────────┘
```

## Data Flow

| Source Endpoint | Target Table | Sync Frequency | Description |
|-----------------|--------------|----------------|-------------|
| `/order` + `/manual-order` | `orders` | Hourly (hot), 6h (warm), Daily (cool) | All platform orders |
| `/route` | `routes` | Daily | Driver routes with order assignments |
| `/hub` | `hubs` | Weekly | Warehouse/hub locations |

## N8N Workflows

### Orders Sync (Tiered)

Three workflows handle orders with different recency tiers to balance freshness and API load:

| Workflow | File | Schedule | Date Range | Purpose |
|----------|------|----------|------------|---------|
| **orders-hot** | `orders-hot.json` | Every hour | Last 3 days | Catch new orders quickly |
| **orders-warm** | `orders-warm.json` | Every 6 hours | Days 4-14 | Update recent orders |
| **orders-cool** | `orders-cool.json` | Daily at 3 AM | Days 15-45 | Backfill older orders |

### Other Workflows

| Workflow | File | Schedule | Description |
|----------|------|----------|-------------|
| **routes-sync** | `routes-sync.json` | Daily | Syncs route data (last 45 days) |
| **reference-sync** | `reference-sync.json` | Weekly | Syncs hub/warehouse reference data |
| **backfill** | `backfill.json` | Manual | Full historical import from 2024-01-01 |

### Workflow Structure

Each order workflow follows this pattern:

```
Schedule Trigger
      │
      ▼
Set Date Range ──────────────────────┐
      │                              │
      ▼                              ▼
Fetch Orders              Fetch Manual Orders
      │                              │
      └──────────┬───────────────────┘
                 ▼
               Merge
                 │
                 ▼
         Transform Orders ─────── tsToIso() converts timestamps
                 │
                 ▼
          Upsert to Supabase ───── ON CONFLICT (order_id) UPDATE
                 │
                 ▼
             Log Sync
```

## Supabase Schema

### `orders`
Primary table containing all platform orders.

| Column | Type | Description |
|--------|------|-------------|
| `order_id` | TEXT PK | Unique order identifier |
| `is_manual` | BOOLEAN | Manual order flag |
| `created_at` | TIMESTAMPTZ | Order creation time |
| `organization_name` | TEXT | Customer organization |
| `org_id` | TEXT | Organization ID |
| `hub_id` | TEXT | Assigned hub FK |
| `order_state` | TEXT | CONFIRMED, IN_TRANSIT, DELIVERED, etc. |
| `order_type` | TEXT | Kuljetus, etc. |
| `first_schedule` | TIMESTAMPTZ | Scheduled pickup time |
| `schedule` | JSONB | Time window array |
| `content` | JSONB | Order items |
| `stops` | TEXT | Delivery address |
| `charge` | JSONB | Pricing breakdown |
| `platform_fee` | INTEGER | Platform fee (cents) |
| `service_fee` | INTEGER | Service fee (cents) |
| `route_id` | TEXT | Assigned route FK |
| `commission_rate` | FLOAT | Partner commission rate |
| `underway_at` | TIMESTAMPTZ | Driver started |
| `in_transit_at` | TIMESTAMPTZ | In transit |
| `delivered_at` | TIMESTAMPTZ | Delivery completed |
| `review` | JSONB | Customer review |
| `manual_data` | JSONB | Manual order extra fields |
| `synced_at` | TIMESTAMPTZ | Last sync timestamp |

### `routes`
Driver routes linking orders to delivery runs.

| Column | Type | Description |
|--------|------|-------------|
| `route_id` | TEXT PK | Unique route identifier |
| `date` | DATE | Route date |
| `hub_id` | TEXT | Hub FK |
| `partner` | TEXT | Driver/partner name |
| `internal_cost` | INTEGER | Route cost (cents) |
| `order_ids` | JSONB | Array of order IDs |
| `warehouse_order_ids` | JSONB | Warehouse orders |
| `total_sales` | FLOAT | Total route revenue |
| `synced_at` | TIMESTAMPTZ | Last sync timestamp |

### `hubs`
Warehouse/hub reference data.

| Column | Type | Description |
|--------|------|-------------|
| `hub_id` | TEXT PK | Unique hub identifier |
| `hub_name` | TEXT | Display name |
| `hub_location` | JSONB | Address, city, coordinates |
| `opening_hours` | JSONB | Operating hours |
| `service_info` | JSONB | Contact details |
| `tz` | TEXT | Timezone (Europe/Helsinki) |
| `synced_at` | TIMESTAMPTZ | Last sync timestamp |

### `sync_log`
Audit log for workflow executions.

| Column | Type | Description |
|--------|------|-------------|
| `id` | BIGSERIAL PK | Auto-increment ID |
| `workflow` | TEXT | Workflow name |
| `started_at` | TIMESTAMPTZ | Execution start |
| `completed_at` | TIMESTAMPTZ | Execution end |
| `date_range` | TEXT | Date range processed |
| `rows_upserted` | INTEGER | Records synced |
| `status` | TEXT | success/error |
| `error_message` | TEXT | Error details if failed |

## Setup

### Prerequisites

- N8N Cloud account
- Supabase project
- Backoffice API access (API key)

### 1. Supabase Setup

Run the migration to create tables:

```sql
-- See supabase/migrations/001_create_tables.sql
```

### 2. N8N Workflow Import

1. Import each workflow JSON from `n8n-workflows/`
2. Replace placeholder credentials:
   - `SUPABASE_SERVICE_KEY` → Your Supabase service role key
   - API key is already configured in workflow JSONs

3. Activate workflows:
   - `orders-hot` - Activate immediately
   - `orders-warm` - Activate immediately
   - `orders-cool` - Activate immediately
   - `routes-sync` - Activate immediately
   - `reference-sync` - Activate immediately
   - `backfill` - Keep inactive (manual trigger only)

### 3. Initial Data Load

1. Run `reference-sync` first to populate hubs
2. Run `backfill` manually for historical orders
3. Scheduled workflows will maintain freshness

## API Reference

### Backoffice API Endpoints

Base URL: `https://qtml5qv6uk.execute-api.eu-central-1.amazonaws.com/production`

| Endpoint | Method | Parameters | Description |
|----------|--------|------------|-------------|
| `/order` | GET | `start_date`, `end_date` | Platform orders |
| `/manual-order` | GET | `start_date`, `end_date` | Manual orders |
| `/route` | GET | `start_date`, `end_date` | Driver routes |
| `/hub` | GET | - | All hubs |

Authentication: `x-api-key` header

### Supabase REST API

Base URL: `https://ybznbfezrdgzgptxkgul.supabase.co/rest/v1`

All tables support standard PostgREST operations with upsert via:
```
Prefer: resolution=merge-duplicates
```

## Timestamp Handling

The Backoffice API returns timestamps in mixed formats:
- Unix timestamps as strings: `"1781188789.077578"`
- ISO 8601 strings: `"2026-06-12T15:00:00+00:00"`

The `tsToIso()` transform function handles both:

```javascript
function tsToIso(val) {
  if (val === null || val === undefined || val === '') return null;
  // If already ISO format, return as-is
  if (typeof val === 'string' && (val.includes('T') || /^\d{4}-/.test(val))) return val;
  const n = parseFloat(val);
  if (isNaN(n)) return null;
  return new Date(n * 1000).toISOString();
}
```

## Current Status

| Component | Status | Notes |
|-----------|--------|-------|
| Orders sync | ✅ Working | 9,500+ orders synced |
| Hubs sync | ✅ Working | 98 hubs synced |
| Routes sync | ⚠️ Blocked | Backoffice API returning Lambda Error |
| Backfill | ✅ Complete | Historical data from 2024-01-01 |

## N8N Workflow IDs (Production)

| Workflow | N8N ID |
|----------|--------|
| orders-hot | `qRghfLrGIwogWMn6` |
| routes-sync | `cgEcgz89U6Rp7UJH` |
| reference-sync | `D62F3xpZ443ZFUwa` |
| backfill | `OWmc3Nb2DslOImuY` |

## License

Private - Chapeau internal use only.
