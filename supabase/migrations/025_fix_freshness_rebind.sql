-- Rebind analytics_freshness after the Google Ads table rename (2026-09-16).
--
-- THE BUG, and it is a trap worth naming: a Postgres view stores references to the OID of the
-- object it reads, not its name. Renaming a table therefore SILENTLY REPOINTS every dependent
-- view at the renamed object -- the view definition rewrites itself to say
-- `FROM ads_google_campaign_daily_legacy`.
--
-- So when migration 024 renamed ads_google_campaign_daily -> _legacy and put a view in its
-- place, analytics_freshness stopped measuring the live feed and started measuring the frozen
-- legacy table. It would have reported the same data_through forever and never fired. A
-- monitoring system quietly watching the wrong object is worse than no monitoring, because it
-- reports healthy -- the same shape of failure as the synced_at problem this watchdog was
-- built to replace.
--
-- Caught because the freshness view said Google data ended 2026-09-15 while the view itself
-- had 09-16. A one-day discrepancy was the only visible symptom.
--
-- REMEMBER FOR NEXT TIME: after ANY rename-and-replace-with-a-view, recreate every dependent
-- view. That applies to the Meta cutover still to come (ads_meta_placement_daily).

CREATE OR REPLACE VIEW public.analytics_freshness AS
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

COMMENT ON VIEW public.analytics_freshness IS
  'Marketing/analytics feed freshness. `data_age_days` (from MAX(date)) is the real signal; '
  '`false_freshness_days` shows how wrong a synced_at-based alarm would be. Alert on '
  'data_age_days, never on last_touched. NOTE: renaming any source table silently repoints '
  'this view at the renamed object -- recreate it after every rename (see migration 025).';

GRANT SELECT ON public.analytics_freshness TO bi_chatbot_readonly;
