-- Contract view for the GA4 conversion-type breakdown (2026-09-17).
--
-- Answers a question bi_website_report could not: which events make up the `conversions`
-- total. No legacy half -- Supermetrics never carried event-level data -- so this is a plain
-- view over core with no union and no boundary.
--
-- WHAT IT IMMEDIATELY SHOWS, and it is the reason this feed was worth building. Over
-- 2026-08-18..09-16, 54,004 "conversions" break down as:
--
--     add_to_cart     44,112   (82%)  flagged as a key event
--     session_start    7,329   (14%)  flagged as a key event
--     everything else    ~757   (1.4%)
--
-- session_start fires once per session, so flagging it makes "conversions" partly a restatement
-- of session count. add_to_cart at 44,112 against 5,878 page_view events is ~7.5 per page view,
-- which is not plausible behaviour and points at a misfiring implementation rather than real
-- intent.
--
-- Genuine intent signals are the small numbers: whatsapp_click 440 (EUR 22,000 attributed),
-- begin_checkout 272 (EUR 50,459), mail_to_service 40, purchase 4, call_to_service 1 -- about
-- 757 in total, roughly 71x smaller than the headline figure.
--
-- The fix is in GA4's config (unflag session_start and add_to_cart, investigate the latter's
-- volume), not here. This view exists so the problem is visible and measurable.
--
-- Event-level total (54,004) vs the totals feed (52,674) differs by 2.5% -- expected, since
-- GA4 applies thresholding per query and a breakdown never sums to the aggregate.

CREATE OR REPLACE VIEW public.analytics_ga_daily_events AS
  SELECT date,
         event_name,
         is_conversion_event,
         event_count,
         conversions,
         event_value,
         synced_at
  FROM core.ga4_daily_events;

COMMENT ON VIEW public.analytics_ga_daily_events IS
  'GA4 events per day with key-event flag. Use to break down the `conversions` figure in '
  'analytics_ga_daily_totals, which is NOT a conversion count: ~95% of it is add_to_cart and '
  'session_start, both flagged as key events in the GA4 property. Genuine intent events '
  '(begin_checkout, whatsapp_click, purchase, mail_to_service, call_to_service) total ~757 '
  'against a headline 54,004 for 2026-08-18..09-16. Does NOT sum to the totals feed -- GA4 '
  'thresholds each query separately (2.5% apart over that window).';

GRANT SELECT ON public.analytics_ga_daily_events TO bi_chatbot_readonly;
