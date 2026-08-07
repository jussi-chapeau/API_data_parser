-- Structured geo-enrichment for orders.stops (workstream A).
--
-- Real data doesn't match the clean pickup/delivery split originally assumed: only 579 of
-- 6,941 platform orders (8.3%) have `stops` as a structured JSON array with an explicit
-- role (pickup/delivery). The rest -- 91.7% of platform orders, and effectively all manual
-- orders -- have `stops` as a single plain address string with no role marker at all.
--
-- Design accordingly: pickup_city/postal_code and delivery_city/postal_code are populated
-- ONLY when the source stop had a confident role (from structured JSON). waypoints_parsed
-- is the comprehensive source of truth -- every stop we could extract and geocode, tagged
-- role='pickup'/'delivery'/'unknown', even when we can't confidently say which end of the
-- job it is. Don't guess a role onto an unlabeled single address -- see docs/STATUS.md.

ALTER TABLE orders ADD COLUMN IF NOT EXISTS pickup_city TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS pickup_postal_code TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_city TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_postal_code TEXT;

-- Array of every stop we could extract + geocode from `stops`, regardless of whether the
-- role is known: [{role, raw_address, geocoded_address, city, postal_code, latitude,
-- longitude, geocode_status}, ...]. geocode_status is 'ok' | 'not_found' | 'error' |
-- 'skipped_no_address'.
ALTER TABLE orders ADD COLUMN IF NOT EXISTS waypoints_parsed JSONB;

-- Access/logistics detail per stop, kept separate from geocoding data since it answers a
-- different question (how to access the location, not where it is):
-- [{role, apartment, floor, elevator_in_use, carrier_count, information}, ...]. Only
-- populated for the ~8% of stops with structured JSON -- plain-text stops carry this
-- information inline in free text we don't attempt to parse out.
ALTER TABLE orders ADD COLUMN IF NOT EXISTS stop_building_details JSONB;

CREATE INDEX IF NOT EXISTS idx_orders_pickup_city ON orders (pickup_city) WHERE pickup_city IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_orders_delivery_city ON orders (delivery_city) WHERE delivery_city IS NOT NULL;
