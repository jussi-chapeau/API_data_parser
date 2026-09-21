-- Customer-area segmentation, stage 3c: per-order area binding (2026-09-21).
--
-- One row per non-cancelled order, ALWAYS -- including orders with no resolvable area and
-- orders excluded from segmentation. That is what lets the published totals reconcile against
-- the order table without a filter anyone has to remember.
--
-- WHY THIS LIVES IN `geo` AND NOT `core`. The build plan said `core`, on the reasoning that it
-- is durable reporting data. It holds the full origin x destination cross, which must never be
-- published -- and `core` is the schema whose ALTER DEFAULT PRIVILEGES hands SELECT to the BI
-- consumer automatically. Keeping every internal piece of this feature in `geo` means the rule
-- is one sentence ("nothing in geo is ever granted") and is checkable in one query, rather than
-- a per-table judgement call that a future migration gets wrong once.
--
-- FOUR SENTINELS, EACH MEANING SOMETHING DIFFERENT. Collapsing these into one NULL would make
-- "we could not find out" indistinguishable from "there is nothing to find out", which is
-- precisely the distinction a brand analysis needs:
--
--   'unknown'         a customer end we tried to resolve and could not
--   'not_applicable'  that end is not a customer (recycling destination; carry help's
--                     second stop, which is the same address as the first)
--   'excluded'        business, freight, route ops, unclassified -- counted, never profiled
--   'withheld'        social-services moves. Counted under their own service_group so
--                     reconciliation holds, but never crossed with an income band: that
--                     combination is an inference about an identifiable household.
--
-- DISTINCT ADDRESSES, NOT STOPS. Ends are deduplicated on address hash, so `Pelkka kantoapu`
-- (98.3% same address on both stops) contributes one customer end rather than two, with no
-- per-service special case. The same rule quietly fixes the 3.4% of moves where one address
-- was typed into both stops.

CREATE TABLE IF NOT EXISTS geo.order_area_binding (
  order_id                 text PRIMARY KEY,
  created_month            date    NOT NULL,
  service_group            text    NOT NULL,
  rule_id                  integer REFERENCES geo.service_rule(rule_id),
  segmentation_eligible    boolean NOT NULL,
  paavo_vintage            text REFERENCES geo.paavo_vintage(vintage),

  origin_income_band       text NOT NULL DEFAULT 'unknown',
  origin_life_stage        text NOT NULL DEFAULT 'unknown',
  origin_area_type         text NOT NULL DEFAULT 'unknown',
  destination_income_band  text NOT NULL DEFAULT 'unknown',
  destination_life_stage   text NOT NULL DEFAULT 'unknown',
  destination_area_type    text NOT NULL DEFAULT 'unknown',

  move_direction           text NOT NULL DEFAULT 'unknown',
  customer_end_count       integer NOT NULL DEFAULT 0,
  resolution_confidence    text NOT NULL DEFAULT 'none',
  bound_at                 timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_binding_month ON geo.order_area_binding (created_month);
CREATE INDEX IF NOT EXISTS idx_binding_group ON geo.order_area_binding (service_group);

CREATE OR REPLACE FUNCTION geo.refresh_order_area_binding()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = geo, public, pg_temp
AS $fn$
DECLARE n integer;
BEGIN
  INSERT INTO geo.order_area_binding AS b (
    order_id, created_month, service_group, rule_id, segmentation_eligible, paavo_vintage,
    origin_income_band, origin_life_stage, origin_area_type,
    destination_income_band, destination_life_stage, destination_area_type,
    move_direction, customer_end_count, resolution_confidence)
  WITH scoped AS (
    SELECT o.order_id, o.created_at, o.is_manual,
           r.rule_id, r.service_group, r.segmentation_eligible, r.sensitivity,
           r.origin_is_customer, r.destination_is_customer
    FROM public.orders o
    JOIN geo.order_type_resolution t
      ON t.raw_order_type = COALESCE(o.order_type,'') AND t.is_manual = o.is_manual
    JOIN geo.service_rule r ON r.rule_id = t.rule_id
    WHERE o.order_state IS DISTINCT FROM 'CANCELLED'
  ),
  -- Customer ends only: the right role for the service, a real postcode, and not an address
  -- frequency has flagged as a facility.
  customer_ends AS (
    SELECT s.order_id, sr.stop_seq, sr.role, sr.postal_code, sr.address_hash,
           sr.confidence, sr.paavo_vintage_used,
           row_number() OVER (PARTITION BY s.order_id, sr.address_hash
                              ORDER BY sr.stop_seq) AS dedup_rn
    FROM scoped s
    JOIN geo.stop_resolution sr ON sr.order_id = s.order_id
    WHERE s.segmentation_eligible
      AND sr.postal_code IS NOT NULL
      AND (sr.role IS NULL
           OR (sr.role = 'pickup'   AND s.origin_is_customer)
           OR (sr.role = 'delivery' AND s.destination_is_customer))
      AND NOT EXISTS (SELECT 1 FROM geo.frequent_address f
                      WHERE f.address_hash = sr.address_hash)
  ),
  -- Ranked with window functions rather than correlated subqueries: the first version ran one
  -- scan per order per column and timed out on 12,095 orders before writing a single row.
  ends_ranked AS (
    SELECT *,
           row_number() OVER (PARTITION BY order_id
                              ORDER BY (role = 'delivery')::int, stop_seq) AS origin_rank,
           row_number() OVER (PARTITION BY order_id
                              ORDER BY (role = 'delivery')::int DESC, stop_seq DESC) AS dest_rank
    FROM customer_ends WHERE dedup_rn = 1
  ),
  agg AS (
    SELECT order_id, count(*) AS n_ends,
           -- explicit ordering: relying on min() over the text values would happen to work
           -- ('high' < 'medium' < 'none') and would break the moment a label is renamed.
           CASE WHEN bool_or(confidence = 'none')   THEN 'none'
                WHEN bool_or(confidence = 'medium') THEN 'medium'
                ELSE 'high' END AS conf,
           max(paavo_vintage_used) AS vintage
    FROM ends_ranked GROUP BY order_id
  ),
  picked AS (
    SELECT a.order_id, a.n_ends, a.conf, a.vintage,
           o.postal_code AS origin_pc,
           -- a destination exists only when there are two distinct customer addresses; carry
           -- help collapses to one end and correctly yields NULL here
           CASE WHEN a.n_ends >= 2 THEN d.postal_code END AS dest_pc
    FROM agg a
    LEFT JOIN ends_ranked o ON o.order_id = a.order_id AND o.origin_rank = 1
    LEFT JOIN ends_ranked d ON d.order_id = a.order_id AND d.dest_rank = 1
                           AND d.role = 'delivery'
  )
  SELECT s.order_id,
         date_trunc('month', s.created_at)::date,
         s.service_group, s.rule_id, s.segmentation_eligible,
         COALESCE(p.vintage, geo.vintage_for(s.created_at)),

         -- Each end resolves to a label, an explicit sentinel, or 'unknown'.
         CASE WHEN NOT s.segmentation_eligible
                THEN CASE WHEN s.sensitivity = 'social_services' THEN 'withheld' ELSE 'excluded' END
              WHEN p.origin_pc IS NULL THEN 'unknown'
              ELSE COALESCE(lo.income_band, 'unknown') END,
         CASE WHEN NOT s.segmentation_eligible
                THEN CASE WHEN s.sensitivity = 'social_services' THEN 'withheld' ELSE 'excluded' END
              WHEN p.origin_pc IS NULL THEN 'unknown'
              ELSE COALESCE(lo.life_stage_label, 'unknown') END,
         CASE WHEN NOT s.segmentation_eligible
                THEN CASE WHEN s.sensitivity = 'social_services' THEN 'withheld' ELSE 'excluded' END
              WHEN p.origin_pc IS NULL THEN 'unknown'
              ELSE COALESCE(lo.area_type, 'unknown') END,

         CASE WHEN NOT s.segmentation_eligible
                THEN CASE WHEN s.sensitivity = 'social_services' THEN 'withheld' ELSE 'excluded' END
              WHEN NOT s.destination_is_customer THEN 'not_applicable'
              WHEN p.dest_pc IS NULL AND p.n_ends = 1 THEN 'not_applicable'
              WHEN p.dest_pc IS NULL THEN 'unknown'
              ELSE COALESCE(ld.income_band, 'unknown') END,
         CASE WHEN NOT s.segmentation_eligible
                THEN CASE WHEN s.sensitivity = 'social_services' THEN 'withheld' ELSE 'excluded' END
              WHEN NOT s.destination_is_customer THEN 'not_applicable'
              WHEN p.dest_pc IS NULL AND p.n_ends = 1 THEN 'not_applicable'
              WHEN p.dest_pc IS NULL THEN 'unknown'
              ELSE COALESCE(ld.life_stage_label, 'unknown') END,
         CASE WHEN NOT s.segmentation_eligible
                THEN CASE WHEN s.sensitivity = 'social_services' THEN 'withheld' ELSE 'excluded' END
              WHEN NOT s.destination_is_customer THEN 'not_applicable'
              WHEN p.dest_pc IS NULL AND p.n_ends = 1 THEN 'not_applicable'
              WHEN p.dest_pc IS NULL THEN 'unknown'
              ELSE COALESCE(ld.area_type, 'unknown') END,

         -- Move direction: the income-quintile delta between the two ends. Only meaningful for
         -- moving services, and only where BOTH ends resolved -- which, because manual orders
         -- carry a single address, is platform moves. Everything else is 'unknown', so the
         -- artefact still reconciles to the whole move cohort.
         CASE WHEN s.service_group <> 'muutto' THEN 'not_applicable'
              WHEN bo.band_order IS NULL OR bd.band_order IS NULL THEN 'unknown'
              WHEN bd.band_order > bo.band_order THEN 'up'
              WHEN bd.band_order < bo.band_order THEN 'down'
              ELSE 'lateral' END,

         COALESCE(p.n_ends, 0),
         CASE WHEN NOT s.segmentation_eligible THEN 'not_applicable'
              ELSE COALESCE(p.conf, 'none') END
  FROM scoped s
  LEFT JOIN picked p ON p.order_id = s.order_id
  LEFT JOIN geo.paavo_label lo
         ON lo.vintage = COALESCE(p.vintage, geo.vintage_for(s.created_at))
        AND lo.postal_code = p.origin_pc
  LEFT JOIN geo.paavo_label ld
         ON ld.vintage = COALESCE(p.vintage, geo.vintage_for(s.created_at))
        AND ld.postal_code = p.dest_pc
  LEFT JOIN geo.paavo_band_def bo
         ON bo.vintage = lo.vintage AND bo.dimension = 'income_household'
        AND bo.band_label = lo.income_band
  LEFT JOIN geo.paavo_band_def bd
         ON bd.vintage = ld.vintage AND bd.dimension = 'income_household'
        AND bd.band_label = ld.income_band
  ON CONFLICT (order_id) DO UPDATE SET
    created_month = EXCLUDED.created_month,
    service_group = EXCLUDED.service_group,
    rule_id = EXCLUDED.rule_id,
    segmentation_eligible = EXCLUDED.segmentation_eligible,
    paavo_vintage = COALESCE(b.paavo_vintage, EXCLUDED.paavo_vintage),  -- bound once, never rebound
    origin_income_band = EXCLUDED.origin_income_band,
    origin_life_stage = EXCLUDED.origin_life_stage,
    origin_area_type = EXCLUDED.origin_area_type,
    destination_income_band = EXCLUDED.destination_income_band,
    destination_life_stage = EXCLUDED.destination_life_stage,
    destination_area_type = EXCLUDED.destination_area_type,
    move_direction = EXCLUDED.move_direction,
    customer_end_count = EXCLUDED.customer_end_count,
    resolution_confidence = EXCLUDED.resolution_confidence,
    bound_at = now();

  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN jsonb_build_object('ran_at', now(), 'orders_bound', n);
END
$fn$;

COMMENT ON COLUMN geo.order_area_binding.paavo_vintage IS
  'Chosen once at first binding and never changed on refresh -- loading a new Paavo year adds '
  'area rows and restates no history. Rebinding is a deliberate migration, not a side effect.';

SELECT cron.unschedule('geo-refresh-binding')
  WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'geo-refresh-binding');

SELECT cron.schedule('geo-refresh-binding', '17,47 * * * *',
                     $cron$SELECT geo.refresh_order_area_binding()$cron$);

SELECT geo.refresh_order_area_binding();
