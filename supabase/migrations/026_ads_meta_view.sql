-- Cut Meta over to Windsor via the durable core layer (2026-09-16).
--
-- BOUNDARY IS 2026-03-31, NOT 2026-03-16, and the reason matters.
--
-- Windsor's Meta feed is exact for every month where the campaigns still exist -- April, May,
-- August and September all match Supermetrics to the cent on spend AND impressions. March does
-- not, and the gap is precisely one campaign:
--
--     "Waitlist", 2026-03-16..03-30:  EUR 146.06 / 42,524 impressions
--     March shortfall (legacy - windsor): EUR 146.06 / 42,524 impressions
--
-- Exact on both. Waitlist ran 2026-03-05..03-30 and appears nowhere in the Windsor feed.
-- That is Meta's Marketing API excluding deleted/archived campaigns by default -- not a
-- Windsor misconfiguration, and not fixable from our side. The day after Waitlist stopped
-- (03-31) the two feeds agree exactly, which is what proves the diagnosis.
--
-- So the boundary sits after Waitlist ended: legacy keeps all history including deleted
-- campaigns, Windsor serves from 2026-04-01 where it is verified exact. Cutting at 03-16
-- would have silently erased EUR 146 of real spend from reporting.
--
-- GENERAL WARNING for anyone extending this: Windsor/Meta will under-report ANY historical
-- period containing campaigns that were later deleted. Legacy is the only record of those.
--
-- GRAIN. Legacy is placement grain ('facebook|feed|mobile_app'); Windsor is platform grain
-- ('facebook') because Windsor caps Columns to Match at 3 (migration 021). Rather than union
-- two different shapes into one `placement` column -- which would quietly break any
-- GROUP BY placement -- legacy rows are aggregated to platform grain here, taking the first
-- component. Full placement detail remains in ads_meta_placement_daily_legacy.
--
-- PRECONDITION, done: N8N workflow `Ads Meta Daily Sync` (vuQOMC0tnTaZkMTC) deactivated.
-- Reads core.*, never windsor.* -- see migration 022.

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables
             WHERE table_schema='public' AND table_name='ads_meta_placement_daily'
               AND table_type='BASE TABLE') THEN
    ALTER TABLE public.ads_meta_placement_daily RENAME TO ads_meta_placement_daily_legacy;
  END IF;
END
$$;

COMMENT ON TABLE public.ads_meta_placement_daily_legacy IS
  'Supermetrics-era Meta ads at full placement grain, 2025-07-31..2026-09-02. Frozen '
  '2026-09-16. The ONLY record of campaigns later deleted in Meta (e.g. "Waitlist"), which '
  'the Marketing API no longer returns. Read via the ads_meta_placement_daily view.';

CREATE OR REPLACE VIEW public.ads_meta_placement_daily AS
  SELECT date,
         campaign_id,
         campaign_name,
         -- Aggregated from 'facebook|feed|mobile_app' to 'facebook' so both halves of the
         -- union share one grain.
         split_part(placement, '|', 1) AS placement,
         SUM(impressions)              AS impressions,
         SUM(cost_eur)                 AS cost_eur,
         SUM(link_clicks)              AS link_clicks,
         SUM(website_conversions)      AS website_conversions,
         SUM(website_conversion_value) AS website_conversion_value,
         SUM(website_purchases)        AS website_purchases,
         MAX(synced_at)                AS synced_at
  FROM public.ads_meta_placement_daily_legacy
  WHERE date <= DATE '2026-03-31'
  GROUP BY date, campaign_id, campaign_name, split_part(placement, '|', 1)
  UNION ALL
  SELECT date,
         campaign_id,
         campaign           AS campaign_name,
         publisher_platform AS placement,
         impressions,
         spend              AS cost_eur,
         link_clicks,
         -- Legacy's website_conversions and website_purchases were near-duplicates (258 vs
         -- 253 over 13 months); both map to Meta's single "Website Purchases" action.
         purchases          AS website_conversions,
         purchase_value     AS website_conversion_value,
         purchases          AS website_purchases,
         synced_at
  FROM core.ads_meta_daily;

COMMENT ON VIEW public.ads_meta_placement_daily IS
  'Meta ads daily: Supermetrics history (<= 2026-03-31, aggregated to platform grain) + '
  'Windsor via core (>= 2026-04-01). `placement` is the publisher platform '
  '(facebook/instagram/threads), NOT the legacy 3-part composite -- Windsor caps match '
  'columns at 3. NOTE: this feed under-reports historical periods containing campaigns since '
  'deleted in Meta; only the _legacy table has those.';

GRANT SELECT ON public.ads_meta_placement_daily TO bi_chatbot_readonly;

-- MUST recreate: renaming the source table silently repoints dependent views at the renamed
-- object (see migration 025 -- this exact trap, second occurrence).
CREATE OR REPLACE VIEW public.analytics_freshness AS
  -- Column aliases are REQUIRED: CREATE OR REPLACE VIEW cannot rename existing columns,
  -- and without them Postgres names the first one "max" and refuses the replace.
  SELECT 'analytics_ga_daily_totals'::text AS feed,
         MAX(date)                          AS data_through,
         (CURRENT_DATE - MAX(date))         AS data_age_days,
         MAX(synced_at)::date               AS last_touched,
         (MAX(synced_at)::date - MAX(date)) AS false_freshness_days
  FROM public.analytics_ga_daily_totals
  UNION ALL
  SELECT 'ads_google_campaign_daily',
         MAX(date), (CURRENT_DATE - MAX(date)),
         MAX(synced_at)::date, (MAX(synced_at)::date - MAX(date))
  FROM public.ads_google_campaign_daily
  UNION ALL
  SELECT 'ads_meta_placement_daily',
         MAX(date), (CURRENT_DATE - MAX(date)),
         MAX(synced_at)::date, (MAX(synced_at)::date - MAX(date))
  FROM public.ads_meta_placement_daily;

GRANT SELECT ON public.analytics_freshness TO bi_chatbot_readonly;
