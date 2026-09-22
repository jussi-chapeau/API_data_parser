-- Landing-page feeds: GA4 (where paid sessions actually land) + Google Ads (where ads point).
-- 2026-09-22.
--
-- WHY TWO TABLES AND NOT A COLUMN ON AN EXISTING ONE. Landing page is a DIMENSION: adding it
-- to windsor.ga4_daily_source would change that table's grain from (date, source_medium) to
-- (date, source_medium, landing_page), which breaks its primary key and silently multiplies
-- every existing metric. Same for ads_google_daily. New grain, new table.
--
-- WHY BOTH SOURCES. They answer different questions and the difference between them is itself
-- a finding:
--   * Google Ads `final_url`  -- where a campaign is CONFIGURED to send traffic.
--   * GA4 `landingPage`       -- where sessions ACTUALLY landed, with behaviour attached.
-- When these disagree you are looking at a redirect, a tracking-template rewrite, or a
-- campaign pointing somewhere nobody intended. Neither table alone shows that.
--
-- `Landing page`, NOT `Landing page + query string`. The query-string variant explodes
-- cardinality with gclid and UTM noise, producing thousands of near-duplicate rows for what is
-- one page. Windsor also caps "Columns to Match" at 3, and both primary keys here use exactly
-- 3 columns -- adding query string would not fit even if we wanted it.
--
-- TABLES ARE CREATED HERE, THEN HANDED TO windsor_writer. Per migration 016: Windsor issues
-- CREATE TABLE IF NOT EXISTS and ALTER TABLE ... ADD COLUMN before every load, and ALTER needs
-- ownership. Creating them ourselves fixes the types and keys (Windsor guesses FLOAT for
-- everything and TEXT for rates); granting ownership means a field-list typo degrades to one
-- oddly-typed column instead of taking the whole feed down and retrying every 30 minutes
-- unnoticed.
--
-- THESE WILL BE EMPTY UNTIL THE WINDSOR TASKS EXIST. That is deliberate and safe: the views
-- return zero rows rather than failing. Two destination task slots are required.
--
-- ---------------------------------------------------------------------------
-- WINDSOR TASK SETUP -- the field -> column mapping to enter in the UI.
-- Get these wrong and Windsor ALTERs a new column in rather than failing (migration 016), so
-- the symptom is a half-empty table, not an error.
--
-- TASK 1  source Google Analytics 4  ->  windsor.ga4_daily_landing
--   Columns to Match (max 3, and we use exactly 3):
--     date, landing_page, session_source_medium
--   Fields:
--     Date                                 -> date
--     Landing page                         -> landing_page          (NOT "+ query string")
--     Session source / medium              -> session_source_medium
--     Sessions                             -> sessions
--     Total users                          -> totalusers
--     Engaged sessions                     -> engaged_sessions
--     Engagement rate                      -> engagement_rate
--     Key event count for begin_checkout   -> checkouts
--     Key event count for purchase         -> purchases
--     Key event count for whatsapp_click   -> whatsapp_clicks
--   7 metrics, 3 dimensions -- inside GA4's 10-metric cap, with room for one more.
--
-- TASK 2  source Google Ads  ->  windsor.ads_google_landing
--   Columns to Match: date, campaign_id, final_url
--   Fields:
--     Date          -> date
--     Campaign id   -> campaign_id
--     Campaign name -> campaign
--     Final url     -> final_url
--     Clicks        -> clicks
--     Impressions   -> impressions
--     Cost          -> spend
--
-- Set "Backfill data for" generously on the first run; the rolling window only governs what
-- Windsor keeps in staging, and core.* never deletes.
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- Staging
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS windsor.ga4_daily_landing (
  date                  date NOT NULL,
  landing_page          text NOT NULL,
  session_source_medium text NOT NULL,
  sessions              numeric,
  totalusers            numeric,
  engaged_sessions      numeric,
  engagement_rate       numeric,
  checkouts             numeric,   -- Key event count for begin_checkout
  purchases             numeric,   -- Key event count for purchase
  whatsapp_clicks       numeric,   -- Key event count for whatsapp_click
  synced_at             timestamptz,
  PRIMARY KEY (date, landing_page, session_source_medium)
);

CREATE TABLE IF NOT EXISTS windsor.ads_google_landing (
  date        date NOT NULL,
  campaign_id text NOT NULL,
  final_url   text NOT NULL,
  campaign    text,
  clicks      numeric,
  impressions numeric,
  spend       numeric,
  synced_at   timestamptz,
  PRIMARY KEY (date, campaign_id, final_url)
);

ALTER TABLE windsor.ga4_daily_landing  OWNER TO windsor_writer;
ALTER TABLE windsor.ads_google_landing OWNER TO windsor_writer;

-- ---------------------------------------------------------------------------
-- Durable copies. Merge-only, NULLs never overwrite -- migration 022's discipline, which
-- exists because a misconfigured vendor task once deleted a month of production GA4 data.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.ga4_daily_landing (
  date                  date NOT NULL,
  landing_page          text NOT NULL,
  session_source_medium text NOT NULL,
  sessions              numeric,
  totalusers            numeric,
  engaged_sessions      numeric,
  engagement_rate       numeric,
  checkouts             numeric,
  purchases             numeric,
  whatsapp_clicks       numeric,
  synced_at             timestamptz,
  first_seen_at         timestamptz NOT NULL DEFAULT now(),
  last_seen_at          timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (date, landing_page, session_source_medium)
);

CREATE TABLE IF NOT EXISTS core.ads_google_landing (
  date          date NOT NULL,
  campaign_id   text NOT NULL,
  final_url     text NOT NULL,
  campaign      text,
  clicks        numeric,
  impressions   numeric,
  spend         numeric,
  synced_at     timestamptz,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (date, campaign_id, final_url)
);

CREATE INDEX IF NOT EXISTS idx_core_ga4_landing_date ON core.ga4_daily_landing (date);
CREATE INDEX IF NOT EXISTS idx_core_ads_landing_date ON core.ads_google_landing (date);

CREATE OR REPLACE FUNCTION core.refresh_landing_from_windsor()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = core, windsor, pg_temp
AS $fn$
DECLARE n_ga4 integer := 0; n_ads integer := 0;
BEGIN
  INSERT INTO core.ga4_daily_landing AS c
    (date, landing_page, session_source_medium, sessions, totalusers, engaged_sessions,
     engagement_rate, checkouts, purchases, whatsapp_clicks, synced_at)
  SELECT date, landing_page, session_source_medium, sessions, totalusers, engaged_sessions,
         engagement_rate, checkouts, purchases, whatsapp_clicks, synced_at
  FROM windsor.ga4_daily_landing
  WHERE landing_page IS NOT NULL AND session_source_medium IS NOT NULL
  ON CONFLICT (date, landing_page, session_source_medium) DO UPDATE SET
    sessions         = COALESCE(EXCLUDED.sessions, c.sessions),
    totalusers       = COALESCE(EXCLUDED.totalusers, c.totalusers),
    engaged_sessions = COALESCE(EXCLUDED.engaged_sessions, c.engaged_sessions),
    engagement_rate  = COALESCE(EXCLUDED.engagement_rate, c.engagement_rate),
    checkouts        = COALESCE(EXCLUDED.checkouts, c.checkouts),
    purchases        = COALESCE(EXCLUDED.purchases, c.purchases),
    whatsapp_clicks  = COALESCE(EXCLUDED.whatsapp_clicks, c.whatsapp_clicks),
    synced_at        = COALESCE(EXCLUDED.synced_at, c.synced_at),
    last_seen_at     = now();
  GET DIAGNOSTICS n_ga4 = ROW_COUNT;

  INSERT INTO core.ads_google_landing AS c
    (date, campaign_id, final_url, campaign, clicks, impressions, spend, synced_at)
  SELECT date, campaign_id, final_url, campaign, clicks, impressions, spend, synced_at
  FROM windsor.ads_google_landing
  WHERE campaign_id IS NOT NULL AND final_url IS NOT NULL
  ON CONFLICT (date, campaign_id, final_url) DO UPDATE SET
    campaign     = COALESCE(EXCLUDED.campaign, c.campaign),
    clicks       = COALESCE(EXCLUDED.clicks, c.clicks),
    impressions  = COALESCE(EXCLUDED.impressions, c.impressions),
    spend        = COALESCE(EXCLUDED.spend, c.spend),
    synced_at    = COALESCE(EXCLUDED.synced_at, c.synced_at),
    last_seen_at = now();
  GET DIAGNOSTICS n_ads = ROW_COUNT;

  RETURN jsonb_build_object('ran_at', now(), 'ga4_landing_rows', n_ga4,
                            'ads_landing_rows', n_ads);
END
$fn$;

-- Offset from the other core jobs so refreshes never pile onto the same locks.
SELECT cron.unschedule('core-refresh-landing')
  WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'core-refresh-landing');

SELECT cron.schedule('core-refresh-landing', '23,53 * * * *',
                     $cron$SELECT core.refresh_landing_from_windsor()$cron$);

-- ---------------------------------------------------------------------------
-- Contract views
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW public.analytics_ga_daily_landing AS
SELECT date,
       landing_page,
       session_source_medium AS source_medium,
       sessions::integer          AS sessions,
       totalusers::integer        AS total_users,
       engaged_sessions::integer  AS engaged_sessions,
       engagement_rate,
       checkouts::integer         AS begin_checkout_count,
       purchases::integer         AS purchase_count,
       whatsapp_clicks::integer   AS whatsapp_click_count,
       synced_at
FROM core.ga4_daily_landing;

CREATE OR REPLACE VIEW public.ads_google_landing_daily AS
SELECT date, campaign_id, campaign, final_url,
       clicks::integer      AS clicks,
       impressions::integer AS impressions,
       spend,
       synced_at
FROM core.ads_google_landing;

COMMENT ON VIEW public.analytics_ga_daily_landing IS
  'Where sessions actually landed, by traffic source. Filter source_medium for paid: '
  'google / cpc. begin_checkout_count is the usable intent signal -- do NOT add a conversions '
  'column here, the GA4 key-event config that makes it meaningless is documented in migration '
  '045. purchase_count is present but GA4 purchase tracking is broken (4 events against 538 '
  'real orders over 35 days), so treat it as a floor, never as revenue.';

COMMENT ON VIEW public.ads_google_landing_daily IS
  'Where Google Ads campaigns are CONFIGURED to send traffic, with clicks and spend per '
  'destination. This is ad configuration, not on-site behaviour -- join to '
  'analytics_ga_daily_landing to see whether sessions actually arrived where the ad pointed. '
  'A mismatch means a redirect, a tracking template, or a misconfigured campaign.';

GRANT SELECT ON public.analytics_ga_daily_landing TO bi_chatbot_readonly;
GRANT SELECT ON public.ads_google_landing_daily   TO bi_chatbot_readonly;

-- Creating anything in `public` inherits Supabase's default privileges, which hands the
-- PostgREST roles full DML. Closed here explicitly -- the same trapdoor migration 033 PART 2
-- cleaned up, and which reopens for every new object in this schema.
REVOKE ALL ON public.analytics_ga_daily_landing FROM anon, authenticated, PUBLIC;
REVOKE ALL ON public.ads_google_landing_daily   FROM anon, authenticated, PUBLIC;

INSERT INTO core.schema_contract (view_name, column_name, ordinal, data_type)
SELECT c.table_name, c.column_name, c.ordinal_position, c.data_type
FROM information_schema.columns c
WHERE c.table_schema = 'public'
  AND c.table_name IN ('analytics_ga_daily_landing','ads_google_landing_daily')
ON CONFLICT DO NOTHING;
