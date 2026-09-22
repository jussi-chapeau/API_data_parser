-- Stop `conversions` being read as a business metric (2026-09-22).
--
-- WHAT PROMPTED THIS. The BI bot was asked about Google Ads traffic and answered, correctly
-- from the data it had: "2,604 sessions, 19,076 GA4 conversions". That is 7.3 conversions per
-- session. The data was reported faithfully; the metric underneath it is meaningless.
--
-- MEASURED 2026-09-22 against core.ga4_daily_events, 35 days (2026-08-18..09-21), site-wide:
--
--     add_to_cart      49,447   counted as conversion   <- 83% of the total
--     session_start     8,316   counted as conversion   <- every session, by definition
--     to_web_app        2,017   counted
--     whatsapp_click      568   counted
--     begin_checkout      321   counted
--     purchase              4   counted
--
-- Two separate problems:
--
--   1. session_start is marked as a key event in GA4. That makes the metric circular -- a
--      conversion rate computed from it can never be anything but ~100%.
--   2. add_to_cart fires 49,447 times against 8,316 sessions (5.9 per session). That is the
--      price calculator, not carts. It drowns everything else.
--
-- Hence every traffic source reads 7-9 "conversions" per session, INCLUDING direct, which is
-- the tell: the number describes traffic volume, not intent.
--
-- Meanwhile `purchase` fired FOUR times against 538 real orders in the same window (0.7%), so
-- the one event that would have been trustworthy is not installed properly either.
--
-- ALSO WORTH KNOWING: GA4 has key events configured named
-- `tarjouspyyntö_muutolle_lomake_lähetys`, `ph_one_lead`, `varaus__sivu` and
-- `oppaan_lataus_vahvistus` -- precisely the lead events this business would want. None of
-- them has fired once in 35 days. Either the tracking was never installed or the events were
-- renamed and the key-event config was left pointing at the old names.
--
-- WHY A COLUMN AND NOT A COMMENT. The consumer is an LLM. Its column descriptions come from a
-- hardcoded dict in apukuski-bi-chatbot's app/schema_digest.py, so a COMMENT ON COLUMN here
-- would never reach its prompt. A column value does. Same reasoning as the `caveat` column on
-- the segmentation artefacts.
--
-- THE REAL FIX IS NOT HERE. Unmarking session_start (and probably add_to_cart) as key events
-- is a change in the GA4 admin UI that no migration can make. This migration stops the number
-- being quoted as fact in the meantime. When GA4 is corrected, drop the caveat column and
-- remove these rows from core.schema_contract.
--
-- Columns are APPENDED, so no existing ordinal shifts and nothing selecting by position breaks.

CREATE OR REPLACE VIEW public.analytics_ga_daily_source AS
SELECT l.date, l.source_medium, l.sessions, l.total_users, l.conversions,
       l.engagement_rate, l.engaged_sessions, l.begin_checkout_count, l.synced_at,
       'NOT a business conversion count: GA4 marks session_start and add_to_cart as key '
       'events, so this reads 7-9 per session on every source including direct. Measured '
       '2026-09-22: add_to_cart was 83% of it and purchase fired 4 times against 538 real '
       'orders. Use begin_checkout_count as the intent signal, or count orders in Supabase '
       'for anything decision-grade. Never present this as conversions or a conversion rate.'
         ::text AS conversions_caveat
FROM public.analytics_ga_daily_source_legacy l
WHERE l.date <= '2026-08-10'::date
UNION ALL
SELECT s.date, s.session_source_medium AS source_medium, s.sessions::integer,
       s.totalusers::integer AS total_users, s.conversions, s.engagement_rate,
       s.engaged_sessions::integer AS engaged_sessions, s.checkouts AS begin_checkout_count,
       s.synced_at,
       'NOT a business conversion count: GA4 marks session_start and add_to_cart as key '
       'events, so this reads 7-9 per session on every source including direct. Measured '
       '2026-09-22: add_to_cart was 83% of it and purchase fired 4 times against 538 real '
       'orders. Use begin_checkout_count as the intent signal, or count orders in Supabase '
       'for anything decision-grade. Never present this as conversions or a conversion rate.'
         ::text AS conversions_caveat
FROM core.ga4_daily_source s
WHERE s.date >= '2026-08-11'::date;

CREATE OR REPLACE VIEW public.analytics_ga_daily_totals AS
SELECT l.date, l.sessions, l.total_users, l.new_users, l.views, l.views_per_session,
       l.bounce_rate, l.avg_session_length_sec, l.conversions, l.user_conversion_rate,
       l.synced_at,
       'NOT a business conversion count, and user_conversion_rate inherits the same fault: '
       'GA4 marks session_start and add_to_cart as key events. Measured 2026-09-22: 49,511 '
       '"conversions" over 35 days, of which add_to_cart 49,447 and session_start 8,316, '
       'while purchase fired 4 times against 538 real orders. For funnel signal use '
       'core.ga4_daily_events (begin_checkout, whatsapp_click, phone_click, mail_to_service); '
       'for revenue use orders. Never present this as conversions or a conversion rate.'
         ::text AS conversions_caveat
FROM public.analytics_ga_daily_totals_legacy l
WHERE l.date <= '2026-06-30'::date
UNION ALL
SELECT t.date, t.sessions::integer, t.totalusers::integer AS total_users,
       t.newusers::integer AS new_users, t.screen_page_views::integer AS views,
       round(t.screen_page_views / NULLIF(t.sessions, 0::numeric), 4) AS views_per_session,
       t.bounce_rate, t.average_session_duration AS avg_session_length_sec,
       t.conversions, t.user_conversion_rate, t.synced_at,
       'NOT a business conversion count, and user_conversion_rate inherits the same fault: '
       'GA4 marks session_start and add_to_cart as key events. Measured 2026-09-22: 49,511 '
       '"conversions" over 35 days, of which add_to_cart 49,447 and session_start 8,316, '
       'while purchase fired 4 times against 538 real orders. For funnel signal use '
       'core.ga4_daily_events (begin_checkout, whatsapp_click, phone_click, mail_to_service); '
       'for revenue use orders. Never present this as conversions or a conversion rate.'
         ::text AS conversions_caveat
FROM core.ga4_daily_totals t
WHERE t.date >= '2026-07-01'::date;

-- Register the appended columns so public.schema_contract_drift stays clean rather than
-- reporting this deliberate change as a break (the mechanism added in migration 035).
INSERT INTO core.schema_contract (view_name, column_name, ordinal, data_type)
SELECT c.table_name, c.column_name, c.ordinal_position, c.data_type
FROM information_schema.columns c
WHERE c.table_schema = 'public'
  AND c.table_name IN ('analytics_ga_daily_source','analytics_ga_daily_totals')
  AND c.column_name = 'conversions_caveat'
ON CONFLICT DO NOTHING;
