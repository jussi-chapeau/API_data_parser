-- Record what the landing-page feeds actually measure (2026-09-22).
--
-- Both feeds loaded on the first run and immediately disagreed, which is the whole reason for
-- building them as a pair. Measured live, last 30 days, hostname x landing_page:
--
--     tilaus.apukuski.com   /            4,572   the ordering app is a SPA: everything lands on /
--     apukuski.com          (not set)    2,175   EVERY session on the marketing site
--     tilaus.apukuski.com   (not set)      218
--     tilaus.apukuski.com   /offer          94
--     tilaus.apukuski.com   /order          23
--
-- So GA4's landing page is 64% a single SPA root and 34% (not set), across just FOUR distinct
-- values. It cannot answer "which pages does paid traffic land on" for this site.
--
-- The marketing site reporting `(not set)` on 100% of its 2,175 sessions is a tagging problem,
-- not a Windsor or pipeline problem: GA4 derives landing page from the session's first
-- page_view, and it comes back unset when the tag fires late or the session is stitched across
-- a redirect. Worth fixing at the GA4/GTM end -- until then this feed stays near-useless.
--
-- Google Ads `final_url` DOES answer the question -- 8,784 rows and EUR 26,364.87 across real
-- pages (/hinta-arvio/, /muuttopalvelu/, /koti-muutto-tampere/ ...). That is ad configuration
-- rather than observed behaviour, which is a genuine limitation, but it is the only
-- page-level view of paid traffic currently available.
--
-- This reverses the recommendation given when 046 was written, where GA4 was expected to be
-- the better source because it carries behaviour. It would be, if it were recording.

CREATE OR REPLACE VIEW public.analytics_ga_daily_landing AS
SELECT date, landing_page, session_source_medium AS source_medium,
       sessions::integer                   AS sessions,
       totalusers::integer                 AS total_users,
       engaged_sessions::integer           AS engaged_sessions,
       engagement_rate,
       checkouts::integer                  AS begin_checkout_count,
       conversions_purchase::integer       AS purchase_count,
       conversions_whatsapp_click::integer AS whatsapp_click_count,
       synced_at,
       'This column is NOT usable as a landing-page breakdown for this site. Measured '
       '2026-09-22: only four distinct values exist -- 64% of sessions are "/" because the '
       'ordering app (tilaus.apukuski.com) is a single-page app, and 34% are "(not set)", '
       'which is 100% of sessions on the marketing site apukuski.com. That is a GA4 tagging '
       'gap, not missing sync. For page-level paid traffic use ads_google_landing_daily, which '
       'carries real URLs and spend. Never present this as "where our visitors land".'
         ::text AS landing_page_caveat
FROM core.ga4_daily_landing;

COMMENT ON VIEW public.analytics_ga_daily_landing IS
  'GA4 landing pages. Currently near-useless for this site: 64% SPA root, 34% (not set). See '
  'the landing_page_caveat column and migration 051. Use ads_google_landing_daily for '
  'page-level paid traffic.';

INSERT INTO core.schema_contract (view_name, column_name, ordinal, data_type)
SELECT c.table_name, c.column_name, c.ordinal_position, c.data_type
FROM information_schema.columns c
WHERE c.table_schema='public' AND c.table_name='analytics_ga_daily_landing'
  AND c.column_name='landing_page_caveat'
ON CONFLICT DO NOTHING;
