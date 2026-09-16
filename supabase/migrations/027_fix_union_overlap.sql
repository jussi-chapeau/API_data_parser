-- Fix duplicate rows caused by unbounded union halves (2026-09-16). URGENT -- live defect.
--
-- THE BUG, which was mine. Each contract view is `legacy WHERE date <= B` UNION ALL `core`.
-- The legacy half was bounded; the core half was NOT. That was accidentally safe while
-- Windsor's staging table happened to start after B -- and stopped being safe the moment core
-- accumulated earlier history than Windsor originally had. core.ga4_daily_totals now starts
-- 2026-03-30 against a boundary of 2026-06-30, so 93 dates appeared twice.
--
--     analytics_ga_daily_totals   93 duplicated dates
--     ads_meta_placement_daily    98 duplicated keys
--     ads_google_campaign_daily    0 -- boundary 03-15 vs core start 03-16, luck not design
--
-- Impact: any SUM over the affected range double-counted. Sessions, spend, impressions.
-- 2026-03-31 Meta spend read EUR 72.02 against a true EUR 36.01 -- exactly doubled, which is
-- how it was spotted.
--
-- THE LESSON, now in GOTCHAS: in a UNION ALL of two sources, bound BOTH halves explicitly.
-- A boundary on one side only is not a boundary, it is a coincidence that holds until the
-- other side's range changes -- and the durable core layer exists precisely so that range can
-- grow independently of Windsor.
--
-- Boundaries are unchanged in intent; only the missing half of each is added:
--   GA4     legacy <= 2026-06-30, core >= 2026-07-01
--   Google  legacy <= 2026-03-15, core >= 2026-03-16
--   Meta    legacy <= 2026-03-31, core >= 2026-04-01  (after the deleted "Waitlist" campaign)

CREATE OR REPLACE VIEW public.analytics_ga_daily_totals AS
  SELECT date, sessions, total_users, new_users, views, views_per_session, bounce_rate,
         avg_session_length_sec, conversions, user_conversion_rate, synced_at
  FROM public.analytics_ga_daily_totals_legacy
  WHERE date <= DATE '2026-06-30'
  UNION ALL
  SELECT date, sessions::int, totalusers::int, newusers::int, screen_page_views::int,
         ROUND(screen_page_views / NULLIF(sessions, 0), 4),
         bounce_rate, average_session_duration, conversions, user_conversion_rate, synced_at
  FROM core.ga4_daily_totals
  WHERE date >= DATE '2026-07-01';

CREATE OR REPLACE VIEW public.ads_google_campaign_daily AS
  SELECT date, campaign_id, campaign_name, impressions, clicks, cost_eur,
         conversions, conversion_value, synced_at
  FROM public.ads_google_campaign_daily_legacy
  WHERE date <= DATE '2026-03-15'
  UNION ALL
  SELECT date, campaign_id, campaign AS campaign_name, impressions, clicks,
         spend AS cost_eur, conversions, conversion_value, synced_at
  FROM core.ads_google_daily
  WHERE date >= DATE '2026-03-16';

CREATE OR REPLACE VIEW public.ads_meta_placement_daily AS
  SELECT date, campaign_id, campaign_name,
         split_part(placement, '|', 1)  AS placement,
         SUM(impressions)               AS impressions,
         SUM(cost_eur)                  AS cost_eur,
         SUM(link_clicks)               AS link_clicks,
         SUM(website_conversions)       AS website_conversions,
         SUM(website_conversion_value)  AS website_conversion_value,
         SUM(website_purchases)         AS website_purchases,
         MAX(synced_at)                 AS synced_at
  FROM public.ads_meta_placement_daily_legacy
  WHERE date <= DATE '2026-03-31'
  GROUP BY date, campaign_id, campaign_name, split_part(placement, '|', 1)
  UNION ALL
  SELECT date, campaign_id, campaign AS campaign_name, publisher_platform AS placement,
         impressions, spend AS cost_eur, link_clicks,
         purchases AS website_conversions, purchase_value AS website_conversion_value,
         purchases AS website_purchases, synced_at
  FROM core.ads_meta_daily
  WHERE date >= DATE '2026-04-01';
