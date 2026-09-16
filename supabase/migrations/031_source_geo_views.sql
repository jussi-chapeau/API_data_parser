-- Contract views for GA4 source + geo, restoring bi_website_report (2026-09-16).
--
-- Boundary 2026-08-10/11 for both -- see migration 030 for why. In short: legacy keeps all
-- validated history and Windsor takes over exactly where the old sync died, so the source
-- feed's dimension change lands at the feed change rather than mid-history.
--
-- BOTH sides of both unions are bounded. Bounding one side only is what caused 93 duplicated
-- GA4 dates and 98 duplicated Meta keys earlier today (migration 027); not repeating it.
--
-- `region` is NULL for Windsor-era geo rows. Windsor caps match columns at 3, so the request
-- omits region entirely and GA4 aggregates across it (migration 028). Honest NULL rather than
-- a fabricated value.
--
-- After the renames below, analytics_freshness MUST be recreated -- renaming a table silently
-- repoints dependent views at the renamed object. That trap has already fired twice today
-- (migrations 025, 026).

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='public'
             AND table_name='analytics_ga_daily_source' AND table_type='BASE TABLE') THEN
    ALTER TABLE public.analytics_ga_daily_source RENAME TO analytics_ga_daily_source_legacy;
  END IF;
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema='public'
             AND table_name='analytics_ga_daily_geo' AND table_type='BASE TABLE') THEN
    ALTER TABLE public.analytics_ga_daily_geo RENAME TO analytics_ga_daily_geo_legacy;
  END IF;
END
$$;

CREATE OR REPLACE VIEW public.analytics_ga_daily_source AS
  SELECT date, source_medium, sessions, total_users, conversions,
         engagement_rate, engaged_sessions, begin_checkout_count, synced_at
  FROM public.analytics_ga_daily_source_legacy
  WHERE date <= DATE '2026-08-10'
  UNION ALL
  SELECT date,
         session_source_medium AS source_medium,
         sessions::int,
         totalusers::int       AS total_users,
         conversions,
         engagement_rate,
         engaged_sessions,
         checkouts             AS begin_checkout_count,
         synced_at
  FROM core.ga4_daily_source
  WHERE date >= DATE '2026-08-11';

COMMENT ON VIEW public.analytics_ga_daily_source IS
  'GA4 traffic sources: Supermetrics (<= 2026-08-10) + Windsor via core (>= 2026-08-11). '
  'IMPORTANT: the dimension CHANGES at the boundary. Legacy uses attribution-scoped '
  'source_medium; Windsor uses session-scoped session_source_medium, because GA4 rejects the '
  'attribution one alongside session metrics. Windsor reads ~11% lower as a result. Comparing '
  'traffic-source totals across 2026-08-10 compares two different measurements.';

CREATE OR REPLACE VIEW public.analytics_ga_daily_geo AS
  SELECT date, city, region, country, sessions, total_users, new_users,
         conversions, user_conversion_rate, begin_checkout_count, synced_at
  FROM public.analytics_ga_daily_geo_legacy
  WHERE date <= DATE '2026-08-10'
  UNION ALL
  SELECT date,
         city,
         NULL::text AS region,   -- not requested; GA4 aggregates across it (migration 028)
         country,
         sessions::int,
         totalusers::int AS total_users,
         newusers::int   AS new_users,
         conversions,
         user_conversion_rate,
         checkouts       AS begin_checkout_count,
         synced_at
  FROM core.ga4_daily_geo
  WHERE date >= DATE '2026-08-11';

COMMENT ON VIEW public.analytics_ga_daily_geo IS
  'GA4 geography: Supermetrics (<= 2026-08-10) + Windsor via core (>= 2026-08-11). Validated '
  'exact against legacy for March-July. `region` is NULL for Windsor-era rows -- the feed is '
  'city + country only, since Windsor caps match columns at 3. GRAIN DIFFERS ACROSS THE '
  'BOUNDARY: the natural key is (date, city, region, country) for legacy rows and '
  '(date, city, country) after it. Six legacy rows share a (date, city, country) -- all '
  'city=''(not set)'' split across regions, 17 sessions total. Not double counting: the view '
  'total equals legacy + core exactly. GROUP BY city/country aggregates correctly either way.';

GRANT SELECT ON public.analytics_ga_daily_source, public.analytics_ga_daily_geo TO bi_chatbot_readonly;

-- Rebind after the renames. Aliases are required: CREATE OR REPLACE VIEW cannot rename columns.
CREATE OR REPLACE VIEW public.analytics_freshness AS
  SELECT 'analytics_ga_daily_totals'::text AS feed,
         MAX(date)                          AS data_through,
         (CURRENT_DATE - MAX(date))         AS data_age_days,
         MAX(synced_at)::date               AS last_touched,
         (MAX(synced_at)::date - MAX(date)) AS false_freshness_days
  FROM public.analytics_ga_daily_totals
  UNION ALL SELECT 'analytics_ga_daily_source', MAX(date), (CURRENT_DATE - MAX(date)),
         MAX(synced_at)::date, (MAX(synced_at)::date - MAX(date))
  FROM public.analytics_ga_daily_source
  UNION ALL SELECT 'analytics_ga_daily_geo', MAX(date), (CURRENT_DATE - MAX(date)),
         MAX(synced_at)::date, (MAX(synced_at)::date - MAX(date))
  FROM public.analytics_ga_daily_geo
  UNION ALL SELECT 'ads_google_campaign_daily', MAX(date), (CURRENT_DATE - MAX(date)),
         MAX(synced_at)::date, (MAX(synced_at)::date - MAX(date))
  FROM public.ads_google_campaign_daily
  UNION ALL SELECT 'ads_meta_placement_daily', MAX(date), (CURRENT_DATE - MAX(date)),
         MAX(synced_at)::date, (MAX(synced_at)::date - MAX(date))
  FROM public.ads_meta_placement_daily;

GRANT SELECT ON public.analytics_freshness TO bi_chatbot_readonly;
