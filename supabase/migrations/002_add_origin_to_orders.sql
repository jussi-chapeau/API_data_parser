-- Order creation channel from Backoffice API (platform orders only).
-- Values seen: 'app', 'AVY#{id}', null for pre-July-2026 and manual orders.
ALTER TABLE orders ADD COLUMN IF NOT EXISTS origin TEXT;

CREATE INDEX IF NOT EXISTS idx_orders_origin ON orders (origin) WHERE origin IS NOT NULL;
