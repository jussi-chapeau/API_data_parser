CREATE TABLE IF NOT EXISTS orders (
  order_id TEXT PRIMARY KEY,
  is_manual BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ,
  organization_name TEXT,
  org_id TEXT,
  hub_id TEXT,
  order_state TEXT,
  order_type TEXT,
  first_schedule TIMESTAMPTZ,
  schedule JSONB,
  content JSONB,
  stops TEXT,
  charge JSONB,
  platform_fee INTEGER,
  service_fee INTEGER,
  route_id TEXT,
  commission_rate FLOAT,
  underway_at TIMESTAMPTZ,
  in_transit_at TIMESTAMPTZ,
  delivered_at TIMESTAMPTZ,
  review JSONB,
  manual_data JSONB,
  synced_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS routes (
  route_id TEXT PRIMARY KEY,
  date DATE,
  hub_id TEXT,
  partner TEXT,
  internal_cost INTEGER,
  order_ids JSONB,
  warehouse_order_ids JSONB,
  total_sales FLOAT,
  synced_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS hubs (
  hub_id TEXT PRIMARY KEY,
  hub_name TEXT,
  opening_hours JSONB,
  hub_location JSONB,
  service_info JSONB,
  tz TEXT,
  synced_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sync_log (
  id BIGSERIAL PRIMARY KEY,
  workflow TEXT,
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  date_range TEXT,
  rows_upserted INTEGER,
  status TEXT,
  error_message TEXT
);
