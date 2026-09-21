-- Customer-area segmentation, stage 3b: facility detection and area labels (2026-09-21).
--
-- FACILITY DETECTION BY FREQUENCY. A household address appears once or twice; a depot appears
-- dozens of times. Measured over 19,596 resolved stops:
--
--     uses  1      8,863 addresses        uses 11-25      23 addresses
--     uses  2      1,483                  uses 26-50       6
--     uses  3-5      552                  uses 51+         3
--     uses  6-10      63
--
-- The heavy end is unmistakable: the top addresses are used 91, 76 and 73 times and are
-- delivery-only (pickup = 0) across two or three different services -- which is exactly why a
-- per-service rule misses them and frequency does not.
--
-- WHY THE THRESHOLD IS 11 AND NOT 6. `address` is street + number, with `apartment` held
-- separately, so a large apartment block repeats legitimately and excluding it would discard
-- real customers rather than a facility. I tried to separate the two by counting distinct
-- apartment values per address, expecting depots to have none. **It did not discriminate** --
-- among addresses used 51+ times, 2 of 3 still showed multiple apartment values. So the
-- refinement was dropped and the threshold set conservatively at the clearly institutional
-- band (11+, 32 addresses, ~4% of stops) instead of the ambiguous 6-10 band. Role skew and
-- service span are recorded so this can be tightened later on evidence rather than by feel.

CREATE OR REPLACE FUNCTION geo.refresh_frequent_address(min_uses integer DEFAULT 11)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = geo, public, pg_temp
AS $fn$
DECLARE n integer;
BEGIN
  TRUNCATE geo.frequent_address;

  INSERT INTO geo.frequent_address
    (address_hash, occurrence_count, distinct_services, first_seen, last_seen, classification)
  SELECT sr.address_hash,
         count(*),
         count(DISTINCT r.service_group),
         min(o.created_at), max(o.created_at),
         CASE WHEN count(*) FILTER (WHERE sr.role = 'delivery') = count(*) THEN 'facility_inbound'
              WHEN count(*) FILTER (WHERE sr.role = 'pickup')   = count(*) THEN 'facility_outbound'
              ELSE 'facility_mixed' END
  FROM geo.stop_resolution sr
  JOIN public.orders o ON o.order_id = sr.order_id
  JOIN geo.order_type_resolution t
    ON t.raw_order_type = COALESCE(o.order_type,'') AND t.is_manual = o.is_manual
  JOIN geo.service_rule r ON r.rule_id = t.rule_id
  -- role IS NOT NULL restricts this to platform stops. Manual orders hash the whole free-text
  -- blob, which is not an address and would produce meaningless "reuse" counts.
  WHERE sr.address_hash IS NOT NULL AND sr.role IS NOT NULL
  GROUP BY sr.address_hash
  HAVING count(*) >= min_uses;

  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN jsonb_build_object('ran_at', now(), 'threshold', min_uses, 'facilities', n);
END
$fn$;

-- ---------------------------------------------------------------------------
-- Area labels. Bands are cut against the NATIONAL distribution of all 3,018 Paavo areas --
-- never against Apukuski's own order footprint, which would shift every time the business
-- entered a new city and silently relabel history nobody edited.
--
-- Suppressed areas (Paavo withheld the figures, or nobody lives there) are excluded from the
-- cut AND labelled 'unknown'. Banding them would turn a suppression marker into a finding.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION geo.refresh_paavo_labels(v_vintage text)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = geo, pg_temp
AS $fn$
DECLARE n integer;
BEGIN
  -- Record the quintile boundaries as data, so "q3" is answerable without reading SQL.
  DELETE FROM geo.paavo_band_def WHERE vintage = v_vintage AND dimension = 'income_household';

  INSERT INTO geo.paavo_band_def
    (vintage, dimension, band_label, band_order, lower_bound, upper_bound, source_note)
  SELECT v_vintage, 'income_household', b.band_label, b.band_order,
         min(s.income), max(s.income),
         'National quintile of median household income (Paavo tr_mtu) over non-suppressed areas'
  FROM (
    SELECT median_income_household AS income,
           ntile(5) OVER (ORDER BY median_income_household) AS q
    FROM geo.paavo_area
    WHERE vintage = v_vintage AND NOT paavo_suppressed AND median_income_household IS NOT NULL
  ) s
  CROSS JOIN LATERAL (SELECT ('q' || s.q || CASE s.q WHEN 1 THEN '_lowest' WHEN 5 THEN '_highest'
                                                     ELSE '' END)::text AS band_label,
                             s.q AS band_order) b
  GROUP BY b.band_label, b.band_order;

  DELETE FROM geo.paavo_label WHERE vintage = v_vintage;

  INSERT INTO geo.paavo_label
    (vintage, postal_code, income_band, life_stage_label, area_type, label_confidence)
  SELECT a.vintage, a.postal_code,
         CASE WHEN a.paavo_suppressed OR a.median_income_household IS NULL THEN 'unknown'
              ELSE COALESCE((SELECT d.band_label FROM geo.paavo_band_def d
                             WHERE d.vintage = a.vintage AND d.dimension = 'income_household'
                               AND a.median_income_household BETWEEN d.lower_bound AND d.upper_bound
                             ORDER BY d.band_order LIMIT 1), 'unknown') END,
         -- COALESCE is load-bearing: some areas report a household count but have every
         -- life-stage breakdown withheld (Paavo -1 -> NULL), so the pick returns no row.
         -- e.g. postcode 89740. That is a suppression, and it must read as 'unknown'.
         CASE WHEN a.paavo_suppressed OR COALESCE(a.households,0) = 0 THEN 'unknown'
              ELSE COALESCE((SELECT x.lbl FROM (VALUES
                      ('singles',            a.hh_singles),
                      ('young_no_children',  a.hh_young),
                      ('families',           a.hh_with_children),
                      ('adults',             a.hh_adults),
                      ('pensioners',         a.hh_pensioners)
                    ) AS x(lbl, cnt)
                    WHERE x.cnt IS NOT NULL ORDER BY x.cnt DESC, x.lbl LIMIT 1), 'unknown') END,
         CASE WHEN a.paavo_suppressed OR COALESCE(a.area_m2,0) = 0
                   OR COALESCE(a.inhabitants,0) = 0 THEN 'unknown'
              WHEN a.inhabitants / (a.area_m2 / 1000000.0) >= 1500 THEN 'urban_dense'
              WHEN a.inhabitants / (a.area_m2 / 1000000.0) >=  500 THEN 'urban'
              WHEN a.inhabitants / (a.area_m2 / 1000000.0) >=  100 THEN 'suburban'
              ELSE 'rural' END,
         CASE WHEN a.paavo_suppressed THEN 'suppressed' ELSE 'ok' END
  FROM geo.paavo_area a
  WHERE a.vintage = v_vintage;

  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN jsonb_build_object('ran_at', now(), 'vintage', v_vintage, 'labelled', n);
END
$fn$;

COMMENT ON FUNCTION geo.refresh_paavo_labels(text) IS
  'Derive coarse area labels from raw Paavo values. Quintiles cut nationally over '
  'non-suppressed areas only. Raw values never leave `geo`; only these labels go downstream.';

SELECT geo.refresh_frequent_address(11);
SELECT geo.refresh_paavo_labels('pno_tilasto_2026');
