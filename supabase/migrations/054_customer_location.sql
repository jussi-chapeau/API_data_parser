-- Materialise "where our customers are" as a table (2026-09-22).
--
-- The customer-end logic -- right end per service, facilities excluded, distinct addresses per
-- order -- was being recomputed inside every ad-hoc query, which is both slow and a copy-paste
-- invitation to get it subtly wrong. It is the same definition migration 041 uses for binding,
-- so it belongs in one place.
--
-- One row per (order, distinct customer address), carrying the POSTCODE only -- no coordinates,
-- no address text, no hash beyond what stop_resolution already holds. Lives in `geo`, so it is
-- never consumer-readable.
--
-- Note this counts customer LOCATIONS, not orders: a recommerce job contributes two (seller and
-- buyer), a move contributes two (from and to), recycling and carry help one. That is the right
-- grain for "where are our customers" and the wrong one for "how many orders" -- which is why
-- the column is named customer_locations wherever it surfaces.

CREATE TABLE IF NOT EXISTS geo.customer_location (
  order_id      text NOT NULL,
  address_hash  bytea NOT NULL,
  created_month date NOT NULL,
  service_group text,
  postal_code   text NOT NULL,
  computed_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (order_id, address_hash)
);

CREATE INDEX IF NOT EXISTS idx_custloc_month ON geo.customer_location (created_month);
CREATE INDEX IF NOT EXISTS idx_custloc_pc    ON geo.customer_location (postal_code);

CREATE OR REPLACE FUNCTION geo.refresh_customer_location()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = geo, public, pg_temp
AS $fn$
DECLARE n integer;
BEGIN
  TRUNCATE geo.customer_location;

  INSERT INTO geo.customer_location
    (order_id, address_hash, created_month, service_group, postal_code)
  SELECT DISTINCT ON (s.order_id, sr.address_hash)
         s.order_id, sr.address_hash, s.m, s.service_group, sr.postal_code
  FROM (
    SELECT o.order_id, date_trunc('month', o.created_at)::date AS m,
           r.service_group, r.origin_is_customer, r.destination_is_customer
    FROM public.orders o
    JOIN geo.order_type_resolution t
      ON t.raw_order_type = COALESCE(o.order_type,'') AND t.is_manual = o.is_manual
    JOIN geo.service_rule r ON r.rule_id = t.rule_id
    WHERE o.order_state IS DISTINCT FROM 'CANCELLED' AND r.segmentation_eligible
  ) s
  JOIN geo.stop_resolution sr ON sr.order_id = s.order_id
  WHERE sr.postal_code IS NOT NULL
    AND sr.address_hash IS NOT NULL
    AND (sr.role IS NULL
         OR (sr.role = 'pickup'   AND s.origin_is_customer)
         OR (sr.role = 'delivery' AND s.destination_is_customer))
    AND NOT EXISTS (SELECT 1 FROM geo.frequent_address f
                    WHERE f.address_hash = sr.address_hash)
  ORDER BY s.order_id, sr.address_hash, sr.stop_seq;

  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN jsonb_build_object('ran_at', now(), 'customer_locations', n);
END
$fn$;

SELECT cron.unschedule('geo-refresh-customer-location')
  WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'geo-refresh-customer-location');

SELECT cron.schedule('geo-refresh-customer-location', '27 * * * *',
                     $cron$SELECT geo.refresh_customer_location()$cron$);

SELECT geo.refresh_customer_location();
