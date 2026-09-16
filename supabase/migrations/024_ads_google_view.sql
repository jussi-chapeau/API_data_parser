-- Cut Google Ads over to Windsor via the durable core layer (2026-09-16).
--
-- Unlike GA4 (dead five weeks) and Meta (deliberately wound down), this feed was healthy --
-- so this is consolidation ahead of the Supermetrics cancellation, not repair. It also has
-- the most history to protect: legacy goes back to 2024-08-05.
--
-- VALIDATED BEFORE CUTTING OVER, which is the whole point of having done the backfill wide.
-- Six complete months compared day by day, windsor vs Supermetrics:
--     2026-03  EUR 2,377.42   2026-06  EUR 7,806.23
--     2026-04  EUR 5,027.19   2026-07  EUR 6,472.26
--     2026-05  EUR 7,659.81   2026-08  EUR 9,080.44
-- Every month exact on spend, clicks AND impressions. September differs only because Windsor
-- is one day further ahead (16 days vs 15), which is an improvement, not a discrepancy.
--
-- PRECONDITION, already done: N8N workflow `Ads Google Daily Sync` (CZbzvcmagNxvC1JN) was
-- DEACTIVATED first. It wrote this table nightly; a UNION ALL view is not auto-updatable, so
-- leaving it on would turn a working sync into a nightly hard failure.
--
-- Boundary at 2026-03-15: legacy supplies everything up to then, core from 2026-03-16 --
-- the first date Windsor was backfilled to and the first date validated as exact.
--
-- Reads core.*, NOT windsor.* -- see migration 022. Windsor deletes rows outside its rolling
-- window, so a view reading staging directly would lose history as the window slides.

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema='public' AND table_name='ads_google_campaign_daily'
      AND table_type='BASE TABLE'
  ) THEN
    ALTER TABLE public.ads_google_campaign_daily RENAME TO ads_google_campaign_daily_legacy;
  END IF;
END
$$;

COMMENT ON TABLE public.ads_google_campaign_daily_legacy IS
  'Supermetrics-era Google Ads, 2024-08-05..2026-09-15. Frozen 2026-09-16. Read through the '
  'ads_google_campaign_daily view, not directly.';

-- Column names and order match the legacy table exactly, so apukuski-bi-chatbot needs no
-- change. Windsor names differ and are aliased back here: `spend` -> cost_eur,
-- `campaign` -> campaign_name.
CREATE OR REPLACE VIEW public.ads_google_campaign_daily AS
  SELECT date, campaign_id, campaign_name, impressions, clicks, cost_eur,
         conversions, conversion_value, synced_at
  FROM public.ads_google_campaign_daily_legacy
  WHERE date <= DATE '2026-03-15'
  UNION ALL
  SELECT date,
         campaign_id,
         campaign        AS campaign_name,
         impressions,
         clicks,
         spend           AS cost_eur,
         conversions,
         conversion_value,
         synced_at
  FROM core.ads_google_daily;

COMMENT ON VIEW public.ads_google_campaign_daily IS
  'Google Ads daily by campaign: Supermetrics history (<= 2026-03-15) + Windsor via core '
  '(>= 2026-03-16). Validated exact across six complete months before cutover. NOTE: '
  '`conversions` counts Google Ads conversion ACTIONS (calls, form fills, purchases) and is '
  'NOT comparable with Meta purchases -- never SUM it across platforms.';

GRANT SELECT ON public.ads_google_campaign_daily TO bi_chatbot_readonly;
