-- Customer-area segmentation, stage 3: per-stop postcode resolution (2026-09-21).
--
-- WHAT THIS STORES, AND WHAT IT POINTEDLY DOES NOT. One row per (order, stop) holding a
-- POSTAL CODE and a salted address hash. No coordinates, no address text, no contact name or
-- phone. There is no reason for this table to become a second copy of the location data that
-- `orders.stops` already carries -- and every reason for it not to be.
--
-- WHY IN-DATABASE AND INCREMENTAL. Orders arrive every few minutes via N8N. A Python batch job
-- would always be stale and would need credentials the database already has. Same pattern and
-- same reasoning as core.refresh_from_windsor() in migration 022.
--
-- FOUR TRAPS THIS HANDLES EXPLICITLY:
--
--   1. `orders.stops` is TEXT, and all 4,569 non-cancelled manual orders hold plain prose, not
--      JSON. A bare ::jsonb cast throws and takes the whole batch with it. geo.safe_jsonb()
--      checks for a leading '[' first -- so the exception path is almost never entered -- and
--      still catches the exception for the genuinely malformed case.
--   2. Null-island and out-of-range coordinates are filtered BEFORE the spatial test, so the
--      GiST index is not asked to do work for garbage.
--   3. ST_Intersects, not ST_Contains. ST_Contains excludes boundary points, which would
--      silently return "unknown" for any address sitting on a postcode edge. Intersects can
--      match two polygons along a shared edge, so the pick is ordered for determinism --
--      arbitrary, but stable, which is what matters for a label nobody should see change.
--   4. The cron is offset, never */30. docs/GOTCHAS.md records a real full-Supabase outage
--      caused by lock contention; this must not land on top of core-refresh-from-windsor.
--
-- MANUAL ORDERS HAVE NO COORDINATES, EVER. They fall back to extracting a 5-digit postcode
-- from the address text, VALIDATED against the Paavo postcode universe -- which alone discards
-- most false positives from house numbers, prices and dates. Accepted only when exactly one
-- distinct valid postcode is present; two or more without a reliable ordering signal is
-- `unknown`, never a guess. Measured: only 2.1% of manual moving orders contain two distinct
-- postcodes, so this is rarely a loss.

-- ---------------------------------------------------------------------------
-- Defensive: stop `core`'s blanket from reaching tables created from here on. This does NOT
-- touch existing grants, so it cannot break the BI repo -- it only closes the trapdoor where
-- a future `core` table becomes consumer-readable with no grant line to notice in review.
-- The wider remediation (narrowing SELECT on public.orders) is a separate, breaking change
-- still waiting on the BI repo's column inventory.
-- ---------------------------------------------------------------------------
ALTER DEFAULT PRIVILEGES IN SCHEMA core REVOKE SELECT ON TABLES FROM bi_chatbot_readonly;

CREATE OR REPLACE FUNCTION geo.safe_jsonb(t text)
RETURNS jsonb
LANGUAGE plpgsql
IMMUTABLE
SET search_path = pg_temp
AS $fn$
BEGIN
  IF t IS NULL OR left(btrim(t), 1) <> '[' THEN
    RETURN NULL;
  END IF;
  RETURN t::jsonb;
EXCEPTION WHEN others THEN
  RETURN NULL;
END
$fn$;

COMMENT ON FUNCTION geo.safe_jsonb(text) IS
  'Cast to jsonb or NULL. The leading-[ check keeps the exception path cold: all 4,569 manual '
  'orders hold prose, so without it every one would raise and roll back the batch.';

-- ---------------------------------------------------------------------------
-- Which Paavo vintage an order binds to. Chosen ONCE at first resolution and never changed,
-- so loading a new Paavo year adds rows and restates nothing.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION geo.vintage_for(ts timestamptz)
RETURNS text
LANGUAGE sql
STABLE
SET search_path = geo, pg_temp
AS $fn$
  SELECT COALESCE(
    -- the latest vintage whose reference year the order had already reached ...
    (SELECT v.vintage FROM geo.paavo_vintage v
      WHERE v.reference_year <= EXTRACT(YEAR FROM ts)::int
      ORDER BY v.reference_year DESC LIMIT 1),
    -- ... otherwise the earliest we hold. Orders predate the only vintage currently loaded
    -- (2026), and labelling a 2024 order with 2026 area statistics is both unavoidable and
    -- defensible: area character moves far more slowly than a year.
    (SELECT v.vintage FROM geo.paavo_vintage v ORDER BY v.reference_year ASC LIMIT 1));
$fn$;

CREATE TABLE IF NOT EXISTS geo.stop_resolution (
  order_id                text    NOT NULL,
  stop_seq                integer NOT NULL,
  role                    text,                  -- 'pickup' | 'delivery' | 'waypoint' | NULL
  postal_code             text,
  address_hash            bytea,                 -- salted; never the address itself
  method                  text    NOT NULL,      -- coordinate_pip | text_postcode_single | unknown
  confidence              text    NOT NULL,      -- high | medium | none
  paavo_vintage_used      text REFERENCES geo.paavo_vintage(vintage),
  resolved_at             timestamptz NOT NULL DEFAULT now(),
  resolved_from_synced_at timestamptz,
  PRIMARY KEY (order_id, stop_seq)
);

CREATE INDEX IF NOT EXISTS idx_stop_res_hash ON geo.stop_resolution (address_hash);
CREATE INDEX IF NOT EXISTS idx_stop_res_postal ON geo.stop_resolution (postal_code);

-- ---------------------------------------------------------------------------
-- The resolver. Idempotent: it rebuilds every stop row for the orders it touches, so a
-- re-sync (or the 2026-09-21 stops repair) simply produces the same or better answers.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION geo.resolve_new_stops(batch_limit integer DEFAULT 2000)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = geo, extensions, public, pg_temp
AS $fn$
DECLARE
  n_orders integer := 0;
  n_stops  integer := 0;
BEGIN
  CREATE TEMP TABLE _todo ON COMMIT DROP AS
  SELECT o.order_id, o.stops, o.is_manual, o.created_at, o.synced_at
  FROM public.orders o
  LEFT JOIN LATERAL (
    SELECT max(sr.resolved_from_synced_at) AS seen
    FROM geo.stop_resolution sr WHERE sr.order_id = o.order_id
  ) r ON true
  WHERE o.order_state IS DISTINCT FROM 'CANCELLED'
    AND (r.seen IS NULL OR o.synced_at IS DISTINCT FROM r.seen)
  LIMIT batch_limit;

  SELECT count(*) INTO n_orders FROM _todo;
  IF n_orders = 0 THEN
    RETURN jsonb_build_object('ran_at', now(), 'orders', 0, 'stops', 0);
  END IF;

  DELETE FROM geo.stop_resolution sr USING _todo t WHERE sr.order_id = t.order_id;

  -- Platform orders: structured stops with coordinates.
  INSERT INTO geo.stop_resolution
    (order_id, stop_seq, role, postal_code, address_hash, method, confidence,
     paavo_vintage_used, resolved_from_synced_at)
  SELECT t.order_id, s.ord::int, s.val->>'role',
         a.postal_code,
         geo.address_hash(s.val->>'address'),
         CASE WHEN a.postal_code IS NOT NULL THEN 'coordinate_pip' ELSE 'unknown' END,
         CASE WHEN a.postal_code IS NOT NULL THEN 'high' ELSE 'none' END,
         geo.vintage_for(t.created_at), t.synced_at
  FROM _todo t
  CROSS JOIN LATERAL jsonb_array_elements(geo.safe_jsonb(t.stops)) WITH ORDINALITY AS s(val, ord)
  LEFT JOIN LATERAL (
    SELECT p.postal_code
    FROM geo.paavo_area p
    WHERE p.vintage = geo.vintage_for(t.created_at)
      -- coordinates arrive as strings; the bbox filter also rejects null island (47 stops)
      AND (s.val->'location'->>'latitude')  ~ '^-?[0-9.]+$'
      AND (s.val->'location'->>'longitude') ~ '^-?[0-9.]+$'
      AND (s.val->'location'->>'latitude')::numeric  BETWEEN 59.5 AND 70.1
      AND (s.val->'location'->>'longitude')::numeric BETWEEN 19.0 AND 31.6
      AND extensions.ST_Intersects(
            p.geom,
            extensions.ST_Transform(
              extensions.ST_SetSRID(extensions.ST_MakePoint(
                (s.val->'location'->>'longitude')::numeric,
                (s.val->'location'->>'latitude')::numeric), 4326), 3067))
    ORDER BY p.postal_code
    LIMIT 1
  ) a ON true
  WHERE geo.safe_jsonb(t.stops) IS NOT NULL;

  -- Manual orders: no coordinates. One 5-digit token, and only if Paavo agrees it is a real
  -- postcode. Two or more distinct valid codes without an ordering signal -> unknown.
  INSERT INTO geo.stop_resolution
    (order_id, stop_seq, role, postal_code, address_hash, method, confidence,
     paavo_vintage_used, resolved_from_synced_at)
  SELECT t.order_id, 1, NULL,
         c.pc,
         geo.address_hash(t.stops),
         CASE WHEN c.pc IS NOT NULL THEN 'text_postcode_single' ELSE 'unknown' END,
         CASE WHEN c.pc IS NOT NULL THEN 'medium' ELSE 'none' END,
         geo.vintage_for(t.created_at), t.synced_at
  FROM _todo t
  LEFT JOIN LATERAL (
    SELECT CASE WHEN count(*) = 1 THEN min(x.code) END AS pc
    FROM (
      SELECT DISTINCT m[1] AS code
      FROM regexp_matches(COALESCE(t.stops,''), '\y(\d{5})\y', 'g') m
      WHERE EXISTS (SELECT 1 FROM geo.paavo_area p
                    WHERE p.vintage = geo.vintage_for(t.created_at)
                      AND p.postal_code = m[1])
    ) x
  ) c ON true
  WHERE geo.safe_jsonb(t.stops) IS NULL;

  GET DIAGNOSTICS n_stops = ROW_COUNT;
  SELECT count(*) INTO n_stops FROM geo.stop_resolution sr
    WHERE sr.order_id IN (SELECT order_id FROM _todo);

  RETURN jsonb_build_object('ran_at', now(), 'orders', n_orders, 'stops', n_stops);
END
$fn$;

COMMENT ON FUNCTION geo.resolve_new_stops(integer) IS
  'Resolve stops to postcodes for orders not yet resolved, or re-synced since. Idempotent. '
  'Postal codes only -- no coordinates or addresses are copied into geo.';

-- Offset deliberately: never */30, which would collide with core-refresh-from-windsor.
SELECT cron.unschedule('geo-resolve-stops')
  WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'geo-resolve-stops');

SELECT cron.schedule('geo-resolve-stops', '7,37 * * * *',
                     $cron$SELECT geo.resolve_new_stops(2000)$cron$);
