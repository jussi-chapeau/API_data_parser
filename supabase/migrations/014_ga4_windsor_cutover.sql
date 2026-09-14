-- GA4 analytics cutover: Supermetrics -> Windsor.ai (2026-09-14).
--
-- The GA4 sync stopped producing new data on 2026-08-10 and nobody noticed for five weeks.
-- Supermetrics is being dropped entirely; Windsor.ai now lands GA4 daily into
-- `windsor.ga4_daily_totals` (see apukuski-bi-chatbot/docs/WINDSOR_HANDOVER.md).
--
-- apukuski-bi-chatbot reads `public.analytics_ga_daily_totals` from three modules
-- (app/tools.py, app/dashboard.py, app/funnel.py). Rather than change code there, keep the
-- contract and swap what sits behind it: the legacy table becomes history, and a view
-- stitches it to the live Windsor feed. Zero code change in the consumer.
--
-- WHY THE OUTAGE WAS INVISIBLE -- and why this matters beyond GA4:
-- The BI bot's staleness warning reads MAX(synced_at). The Supermetrics workflow kept
-- succeeding every night and re-upserting ~100 existing rows with a fresh synced_at, while
-- adding no new dates. So synced_at said "today" while the newest actual data was 35 days
-- old. The alarm was not missing, it was defeated. Any staleness check must key on
-- MAX(date), never MAX(synced_at) -- see `analytics_freshness` at the bottom of this file.
--
-- PRECONDITION, already done: N8N workflow `Analytics GA Daily Sync` (j4gatZqXaw9tk55x)
-- was DEACTIVATED before this migration ran. It POSTed to all three analytics_ga_daily_*
-- tables nightly; a UNION ALL view is not auto-updatable, so leaving it active would have
-- turned a silent no-op into a nightly hard failure.

-- ---------------------------------------------------------------------------
-- 1. Legacy table becomes history
-- ---------------------------------------------------------------------------
-- Guarded so the file is safe to re-run. Once the swap has happened,
-- `analytics_ga_daily_totals` is a VIEW, and an unguarded RENAME would rename the view
-- instead of the table -- quietly destroying the contract this migration exists to preserve.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public'
      AND table_name = 'analytics_ga_daily_totals'
      AND table_type = 'BASE TABLE'
  ) THEN
    ALTER TABLE public.analytics_ga_daily_totals RENAME TO analytics_ga_daily_totals_legacy;
  END IF;
END
$$;

COMMENT ON TABLE public.analytics_ga_daily_totals_legacy IS
  'Supermetrics-era GA4 daily totals, 2025-01-01..2026-08-10. Frozen: the sync was retired '
  '2026-09-14. Read through the analytics_ga_daily_totals view, not directly. The final row '
  '(2026-08-10) is PARTIAL -- 7 sessions vs 131-186 on adjacent days, the sync died mid-day.';

-- ---------------------------------------------------------------------------
-- 2. The contract, preserved as a view
-- ---------------------------------------------------------------------------
-- Column list and order match the legacy table exactly, because downstream code selects
-- these names. Windsor's field names differ from ours (totalusers, screen_page_views,
-- newusers) and are aliased back here -- Windsor's names are its API contract, ours are the
-- consumer's, and this view is where the two meet.
--
-- Boundary is `date <= 2026-08-10` on the legacy side and the Windsor table starts
-- 2026-08-15, so the halves cannot overlap. Verified read-only before creating this:
-- 617 rows, 617 distinct dates, 0 duplicates. The 2026-08-11..08-14 gap is real and is
-- pending a Windsor backfill.
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
  WHERE date <= DATE '2026-08-10'
  UNION ALL
  SELECT date,
         sessions::int,
         totalusers::int,
         newusers::int,
         screen_page_views::int,
         -- Derived, not stored: Windsor does not supply it. Verified equal to views/sessions
         -- within 0.005 across all 587 legacy days, so the series stays continuous.
         ROUND(screen_page_views / NULLIF(sessions, 0), 4),
         bounce_rate,
         -- Windsor does not supply these two. NULL rather than a fabricated value: both are
         -- unread by the consumer today, and inventing numbers to fill a schema is how wrong
         -- figures get into reports.
         NULL::numeric,
         conversions,
         NULL::numeric,
         synced_at
  FROM windsor.ga4_daily_totals;

COMMENT ON VIEW public.analytics_ga_daily_totals IS
  'GA4 daily totals: Supermetrics history (<= 2026-08-10) stitched to the live Windsor feed '
  '(>= 2026-08-15). Gap 08-11..08-14 pending backfill. NOTE: `conversions` is NOT a '
  'conversion count -- ~7.85 per session, because nearly every GA4 event is flagged as a key '
  'event. True in both halves, so history is continuous and equally unusable. '
  'avg_session_length_sec and user_conversion_rate are NULL for Windsor-era rows.';

GRANT SELECT ON public.analytics_ga_daily_totals TO bi_chatbot_readonly;

-- ---------------------------------------------------------------------------
-- 3. A staleness signal that cannot be defeated the same way
-- ---------------------------------------------------------------------------
-- Keyed on MAX(date) -- how recent the DATA is -- not MAX(synced_at), which only says when
-- a job last touched a row. That distinction is the whole reason a five-week outage went
-- unseen. Both are exposed side by side so the difference stays visible.
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
  'Marketing/analytics feed freshness. `data_age_days` is the real signal; '
  '`false_freshness_days` is how many days a synced_at-based alarm would have been wrong by. '
  'Alert on data_age_days, never on last_touched.';

GRANT SELECT ON public.analytics_freshness TO bi_chatbot_readonly;
