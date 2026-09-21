-- Customer-area segmentation: fix the income bands to be household-weighted (2026-09-21).
--
-- THE BUG THIS FIXES, FOUND BY LOOKING AT THE OUTPUT RATHER THAN THE CODE. After the first
-- labelling pass, 4,155 of ~10,000 placed orders landed in `q1_lowest` against ~1,300-1,700 in
-- every other quintile. A 2.5x concentration in the bottom band is not a plausible finding
-- about a moving company's customers, so it was worth chasing rather than reporting.
--
-- Migration 040 cut quintiles so each band held an equal number of AREAS. But Paavo's 3,018
-- areas are wildly unequal in size, and 2,297 of them are rural:
--
--     q1_lowest   563 areas -> 1,734,382 people   31.2% of Finland
--     q2          562 areas -> 1,029,826          18.5%
--     q3          562 areas ->   921,718          16.6%
--     q4          562 areas ->   832,723          15.0%
--     q5_highest  562 areas -> 1,032,305          18.6%
--
-- So "q1" was never "the poorest fifth of Finland" -- it was a third of the population. Two
-- effects compound: rural areas dominate by count and have higher HOUSEHOLD income (families,
-- two earners), while dense urban areas have low household medians because so many households
-- are one person. Our customers are overwhelmingly urban (3,182 of the q1 orders are in
-- urban_dense areas), so they piled into a band that was mislabelled as poor.
--
-- THE FIX. Cut the quintiles weighted by HOUSEHOLD COUNT, so each band covers ~20% of Finnish
-- households rather than ~20% of map polygons. Households, not inhabitants, because the metric
-- being banded is median household income -- weighting a household statistic by headcount would
-- over-weight large families.
--
-- After this, "q1" means "an area among the lowest-income fifth of where Finnish households
-- actually live", which is the claim a brand team would reasonably read it as.
--
-- This does not make the ecological fallacy go away: an area's median income still says
-- nothing about any individual customer's income. It only stops the BANDS themselves from
-- being misleading before that caveat is even applied.

CREATE OR REPLACE FUNCTION geo.refresh_paavo_labels(v_vintage text)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = geo, pg_temp
AS $fn$
DECLARE n integer;
BEGIN
  DELETE FROM geo.paavo_band_def WHERE vintage = v_vintage AND dimension = 'income_household';

  -- Household-weighted quintiles: walk areas in income order, accumulating households, and cut
  -- where the running share crosses 20/40/60/80%.
  INSERT INTO geo.paavo_band_def
    (vintage, dimension, band_label, band_order, lower_bound, upper_bound, source_note)
  SELECT v_vintage, 'income_household', b.band_label, b.band_order, min(s.income), max(s.income),
         'Quintile of median household income (Paavo tr_mtu), WEIGHTED BY HOUSEHOLD COUNT so '
         'each band covers ~20% of Finnish households. Cutting per-area instead put 31% of the '
         'population in q1 -- see migration 042.'
  FROM (
    SELECT median_income_household AS income,
           sum(COALESCE(households,0)) OVER (ORDER BY median_income_household,  postal_code)
             / NULLIF(sum(COALESCE(households,0)) OVER (), 0)::numeric AS cum_share
    FROM geo.paavo_area
    WHERE vintage = v_vintage AND NOT paavo_suppressed AND median_income_household IS NOT NULL
  ) s
  CROSS JOIN LATERAL (
    SELECT CASE WHEN s.cum_share <= 0.2 THEN 1 WHEN s.cum_share <= 0.4 THEN 2
                WHEN s.cum_share <= 0.6 THEN 3 WHEN s.cum_share <= 0.8 THEN 4 ELSE 5 END AS band_order
  ) q
  CROSS JOIN LATERAL (
    SELECT q.band_order,
           ('q' || q.band_order || CASE q.band_order WHEN 1 THEN '_lowest'
                                                     WHEN 5 THEN '_highest' ELSE '' END)::text
             AS band_label
  ) b
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

SELECT geo.refresh_paavo_labels('pno_tilasto_2026');
SELECT geo.refresh_order_area_binding();
