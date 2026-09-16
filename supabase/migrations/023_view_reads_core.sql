-- Point the GA4 contract view at core instead of Windsor staging (2026-09-16).
--
-- This is the change that makes migration 022 actually protective. Until now the view read
-- windsor.ga4_daily_totals directly, so anything Windsor deleted or overwrote hit BI
-- immediately -- which is exactly how a month of production data (2026-08-17..09-15)
-- disappeared earlier today.
--
-- Now: Windsor -> windsor.* (volatile) -> core.* (merge-only, never deleted) -> this view.
-- Verified before switching: deleting a row from staging leaves core untouched, and a NULL
-- from staging does not erase a real value in core.
--
-- Also changed: avg_session_length_sec and user_conversion_rate now read their real columns
-- rather than being hardcoded NULL. They are still empty today because the Windsor field list
-- has not picked them up yet, but when it does they populate with no further migration.
-- Reading a real column that happens to be NULL is honest; hardcoding NULL over a column that
-- has data would not be.

CREATE OR REPLACE VIEW public.analytics_ga_daily_totals AS
  SELECT date,
         sessions,
         total_users,
         new_users,
         views,
         views_per_session,
         bounce_rate,
         avg_session_length_sec,
         conversions,
         user_conversion_rate,
         synced_at
  FROM public.analytics_ga_daily_totals_legacy
  WHERE date <= DATE '2026-06-30'
  UNION ALL
  SELECT date,
         sessions::int,
         totalusers::int,
         newusers::int,
         screen_page_views::int,
         -- Derived: GA4 caps a request at 10 metrics, and this is the one field that is
         -- exactly reconstructable (verified within 0.005 across all 587 legacy days), so it
         -- was dropped from the field list to keep both purchase metrics.
         ROUND(screen_page_views / NULLIF(sessions, 0), 4),
         bounce_rate,
         average_session_duration,
         conversions,
         user_conversion_rate,
         synced_at
  FROM core.ga4_daily_totals;

COMMENT ON VIEW public.analytics_ga_daily_totals IS
  'GA4 daily totals: Supermetrics history (<= 2026-06-30) + Windsor feed (>= 2026-07-01) via '
  'the durable core layer, NOT Windsor staging -- see migration 022/023. Validated on a '
  '40-day overlap: sessions diverge 0.02%, users 0.00%. NOTE: `conversions` is NOT a '
  'conversion count (~7.85/session; nearly every GA4 event is a key event) -- true in both '
  'halves.';

GRANT SELECT ON public.analytics_ga_daily_totals TO bi_chatbot_readonly;
