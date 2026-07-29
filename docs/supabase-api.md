# Apukuski Supabase API — Data Access Guide

## Overview

Order, route, and hub data from the Backoffice API is synced into Supabase via N8N workflows on a rolling schedule. This document explains how to query that data.

**Project URL:** `https://ybznbfezrdgzgptxkgul.supabase.co`  
**Data coverage:** Orders from 2024-01-01 to present, refreshed hourly (last 3 days) and daily (up to 45 days back)

---

## Authentication

All requests require an API key in the header:

```
apikey: <your-anon-or-service-key>
Authorization: Bearer <your-anon-or-service-key>
```

- **Anon key** — read-only, safe to use in frontend apps. Get it from Project Settings → API Keys.
- **Service key** — full access, never expose in client-side code.

---

## Base URL

```
https://ybznbfezrdgzgptxkgul.supabase.co/rest/v1
```

---

## Tables

### `orders`

One row per order. Covers both standard (`is_manual=false`) and manual orders (`is_manual=true`).

| Column | Type | Description |
|---|---|---|
| `order_id` | text | Primary key |
| `is_manual` | boolean | true = manual order |
| `created_at` | timestamptz | Order creation time |
| `organization_name` | text | Customer org name |
| `org_id` | text | Customer org ID |
| `hub_id` | text | Hub FK → `hubs.hub_id` |
| `order_state` | text | e.g. `delivered`, `cancelled` |
| `order_type` | text | Order classification |
| `origin` | text | Order source channel (platform orders only). e.g. `app`, `AVY#{id}`. null for manual orders and pre-July-2026 rows. |
| `first_schedule` | timestamptz | First scheduled delivery time |
| `schedule` | jsonb | Full schedule object |
| `content` | jsonb | Order items/contents |
| `stops` | text | **Free-text address string, not a count.** Holds a single location (usually pickup) plus occasional operator notes. No structured delivery address exists in the API — ~59% of rows contain no postcode. Do not use for geographic joins; reconcile by `order_id`. |
| `charge` | jsonb | Charge object from API — **shape differs for platform vs manual** (see below) |
| `platform_fee` | integer | Platform fee in **cents, excl. VAT** — populated for platform orders only; `null` for manual |
| `service_fee` | integer | Service fee in **cents, excl. VAT** — populated for platform orders only; `null` for manual |
| `route_id` | text | Route FK → `routes.route_id` |
| `commission_rate` | float | Commission rate (e.g. 0.15 = 15%) |
| `underway_at` | timestamptz | When courier went underway |
| `in_transit_at` | timestamptz | When order entered transit |
| `delivered_at` | timestamptz | Delivery completion time |
| `review` | jsonb | Customer review object |
| `manual_data` | jsonb | Manual order metadata + interim total (see below) |
| `synced_at` | timestamptz | Last time this row was written by N8N |

#### Charge / pricing: platform vs manual (important for Lovable)

| | Platform (`is_manual=false`) | Manual (`is_manual=true`) |
|---|---|---|
| `charge` | Full breakdown (`workPrice`, `hubDrivePrice`, `platformFee`, …) | `{ paymentMethod, charge }` only |
| Usable total | Derive from breakdown / fee fields | `charge.charge` = **gross total incl. VAT** (euro string) |
| `platform_fee` / `service_fee` | Net cents from API | Always `null` |
| Settlement via API | Possible | **Not available** — ops calculates in Airtable |

**Accepted interim rule (2026-07-23):** for manual orders, use the VAT-inclusive total as-is. Do not invent a fee breakdown.

Normalized total for consumers should live in `manual_data`:

```json
{
  "customer": "...",
  "additionalInfo": "...",
  "serviceFeeApplied": true,
  "payment_method": "invoice",
  "total_incl_vat_eur": 323.40,
  "total_incl_vat_cents": 32340,
  "price_basis": "gross_incl_vat",
  "source_field": "charge.charge"
}
```

**Lovable display helpers:**

```sql
-- Manual order gross total (euros)
select order_id,
       (manual_data->>'total_incl_vat_cents')::int / 100.0 as total_incl_vat_eur
from orders
where is_manual = true;

-- Fallback if transform not yet deployed: parse raw charge.charge
select order_id,
       (charge->>'charge')::numeric as total_incl_vat_eur
from orders
where is_manual = true
  and charge ? 'charge';
```

### `routes`

One row per route (courier run). Updated daily.

| Column | Type | Description |
|---|---|---|
| `route_id` | text | Primary key |
| `date` | date | Route date |
| `hub_id` | text | Hub FK → `hubs.hub_id` |
| `partner` | text | Courier partner name |
| `internal_cost` | integer | Internal cost in **cents** |
| `order_ids` | jsonb | Array of order IDs on this route |
| `warehouse_order_ids` | jsonb | Array of warehouse order IDs |
| `total_sales` | float | Total sales value |
| `synced_at` | timestamptz | Last sync time |

### `hubs`

Reference data. Updated weekly.

| Column | Type | Description |
|---|---|---|
| `hub_id` | text | Primary key |
| `hub_name` | text | Human-readable hub name |
| `opening_hours` | jsonb | Opening hours object |
| `hub_location` | jsonb | Location/address object |
| `service_info` | jsonb | Service configuration |
| `tz` | text | Timezone string (e.g. `Europe/Helsinki`) |
| `synced_at` | timestamptz | Last sync time |

### `sync_log`

Audit trail of every N8N sync run.

| Column | Type | Description |
|---|---|---|
| `id` | bigint | Auto-increment PK |
| `workflow` | text | Workflow name (e.g. `orders-hot`) |
| `started_at` | timestamptz | Run start time |
| `completed_at` | timestamptz | Run end time |
| `date_range` | text | Date range synced |
| `rows_upserted` | integer | Number of rows written |
| `status` | text | `success` or `error` |
| `error_message` | text | Error detail if status=error |

---

## Example Queries

### Get recent delivered orders
```http
GET /rest/v1/orders?order_state=eq.delivered&order=delivered_at.desc&limit=50
```

### Get all orders for a specific hub
```http
GET /rest/v1/orders?hub_id=eq.HUB_ID&order=created_at.desc
```

### Get orders in a date range
```http
GET /rest/v1/orders?created_at=gte.2026-06-01T00:00:00Z&created_at=lte.2026-06-30T23:59:59Z
```

### Get manual orders only
```http
GET /rest/v1/orders?is_manual=eq.true&order=created_at.desc
```

### Get routes for a specific date
```http
GET /rest/v1/routes?date=eq.2026-07-01
```

### Get all hubs
```http
GET /rest/v1/hubs?select=hub_id,hub_name,tz
```

### Check last sync status
```http
GET /rest/v1/sync_log?order=started_at.desc&limit=10
```

### Select specific columns (reduces payload size)
```http
GET /rest/v1/orders?select=order_id,created_at,org_id,order_state,platform_fee,service_fee&limit=100
```

---

## JavaScript / TypeScript

Install the Supabase client:
```bash
npm install @supabase/supabase-js
```

```typescript
import { createClient } from '@supabase/supabase-js'

const supabase = createClient(
  'https://ybznbfezrdgzgptxkgul.supabase.co',
  '<your-anon-key>'
)

// Recent delivered orders
const { data, error } = await supabase
  .from('orders')
  .select('order_id, created_at, organization_name, order_state, platform_fee, service_fee')
  .eq('order_state', 'delivered')
  .order('delivered_at', { ascending: false })
  .limit(100)

// Orders for a hub in June
const { data } = await supabase
  .from('orders')
  .select('*')
  .eq('hub_id', 'HUB_ID')
  .gte('created_at', '2026-06-01')
  .lte('created_at', '2026-06-30')
```

---

## Python

```python
import requests

BASE_URL = "https://ybznbfezrdgzgptxkgul.supabase.co/rest/v1"
HEADERS = {
    "apikey": "<your-anon-key>",
    "Authorization": "Bearer <your-anon-key>",
}

# Recent orders
r = requests.get(
    f"{BASE_URL}/orders",
    headers=HEADERS,
    params={
        "order_state": "eq.delivered",
        "order": "delivered_at.desc",
        "limit": "100",
    }
)
orders = r.json()

# Convert fee columns from cents to euros
for o in orders:
    o['platform_fee_eur'] = o['platform_fee'] / 100 if o['platform_fee'] else None
    o['service_fee_eur'] = o['service_fee'] / 100 if o['service_fee'] else None
```

---

## Notes

- **Fees are stored in cents** (integer). Divide by 100 for euro values.
- **Do not mix tax bases:** platform `platform_fee` / `service_fee` are **excl. VAT**; manual `charge.charge` / `manual_data.total_incl_vat_*` are **incl. VAT**.
- **Timestamps** are ISO 8601 UTC. Convert to local time using the hub's `tz` field.
- **JSONB columns** (`schedule`, `content`, `charge`, `manual_data`, etc.) contain nested objects from the Backoffice API — structure varies by `is_manual`.
- **Data freshness:**
  - Last 3 days: refreshed hourly
  - Days 4–14: refreshed every 6 hours
  - Days 15–45: refreshed daily at 03:30
  - Hubs: refreshed weekly Monday 02:00
- **Row count:** ~11,000 orders, 99 hubs as of 29 July 2026.
- **No end-customer name on platform orders.** `/order` rows have no customer field at
  all (0 of 1,385 sampled) — `organization_name` is the hub/partner, not the customer.
  Only manual orders carry customer name/email/phone. Customer-name lookups therefore
  only ever hit manual orders.
- **Every order has a schedule.** Of 1,900 orders sampled (Apr–Jul 2026) only 1 lacked
  `first_schedule`, and `schedule` always holds exactly two entries (window start/end).
  There is no unscheduled or pending state, so "sold but not yet scheduled" gigs do not
  appear here at all — they live in the Offers layer until a date is set. Far-future
  bookings are visible: 71 were scheduled 31+ days out, longest 87 days.
- Full manual-order charge decision history: `data/AWS_API_charge_object_bug_report.md`.
