-- Move the GA4 view boundary back to 2026-06-30 (2026-09-14).
--
-- Windsor has been backfilled to 2026-07-01 (75 rows, 07-01..09-13, no gaps), so it can now
-- serve everything from July onward and the legacy table is needed only for history.
--
-- WHY THIS IS SAFE -- the two feeds were compared directly, which was impossible until the
-- backfill created an overlap. Across the 40 shared days (2026-07-01..08-09):
--     sessions     0.02% mean divergence (7,888 vs 7,887 total -- one session over 40 days)
--     total users  0.00%
--     views        0.16%
-- Windsor reproduces Supermetrics. The handover recommended validating against GA4's UI by
-- hand because no overlap existed; backfilling six extra weeks made a real diff possible, and
-- it passed.
--
-- IT ALSO FIXES A BAD ROW. The legacy 2026-08-10 row holds 7 sessions -- the sync died
-- mid-day. Windsor reports 236 for that date. Moving the boundary to 06-30 means Windsor
-- supplies 08-10, replacing the partial row with the true value. That is the single largest
-- correction in this change: every other day matches to within a session.
--
-- Gap 2026-08-11..08-14 is also closed by the same backfill.
--
-- NOT changed yet: the Windsor-era SELECT still computes views_per_session and NULLs
-- avg_session_length_sec / user_conversion_rate. The corrected Windsor field list (which adds
-- average_session_duration, user_conversion_rate, ecommerce_purchases, transactions) has not
-- yet been saved on the Windsor side -- those columns exist but are still NULL for every row.
-- Once they populate, this view should be updated to read them directly instead. Tracked in
-- docs/STATUS.md workstream I.

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
         -- Still derived: Windsor supplies screen_page_views_per_session, but GA4 caps a
         -- request at 10 metrics and this is the one field that is exactly reconstructable
         -- (verified within 0.005 across all 587 legacy days), so it was dropped from the
         -- field list in favour of keeping both purchase metrics.
         ROUND(screen_page_views / NULLIF(sessions, 0), 4),
         bounce_rate,
         -- Columns exist on the Windsor table but are NULL until the corrected field list is
         -- saved. Left as explicit NULLs rather than reading empty columns, so the view does
         -- not silently start returning nulls-that-look-like-data.
         NULL::numeric,
         conversions,
         NULL::numeric,
         synced_at
  FROM windsor.ga4_daily_totals;

COMMENT ON VIEW public.analytics_ga_daily_totals IS
  'GA4 daily totals: Supermetrics history (<= 2026-06-30) stitched to the live Windsor feed '
  '(>= 2026-07-01). Validated on a 40-day overlap before the boundary was moved: sessions '
  'diverge 0.02%, users 0.00%. NOTE: `conversions` is NOT a conversion count -- ~7.85 per '
  'session, because nearly every GA4 event is flagged as a key event. True in both halves. '
  'avg_session_length_sec and user_conversion_rate are NULL for Windsor-era rows pending a '
  'field-list change on the Windsor side.';

GRANT SELECT ON public.analytics_ga_daily_totals TO bi_chatbot_readonly;
