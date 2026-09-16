-- Durable copy layer: decouple reporting from Windsor's mutable staging tables (2026-09-16).
--
-- WHY THIS EXISTS. On 2026-09-16 a misconfigured Windsor task deleted a month of production
-- GA4 data (2026-08-17..09-15). The contract views read windsor.* directly, so the loss hit
-- BI instantly, and it was only recoverable because a snapshot happened to have been taken
-- that morning on a hunch. That is not a control.
--
-- The root problem is ownership of lifecycle. Windsor treats its tables as a rolling window
-- IT manages: it deletes rows that fall outside `Backfill data for`, replaces rows wholesale
-- rather than merging, and will happily rewrite a table if a task is pointed at the wrong one.
-- All of that is reasonable behaviour for a staging area and unacceptable for a system of
-- record. Observed directly: 185 -> 172 rows as the window slid, and sentinel values in
-- unsent columns being wiped.
--
--   windsor.*  vendor-owned, volatile, may delete or rewrite at any time
--   core.*     ours, durable, MERGE-ONLY -- nothing in here is ever deleted by a sync
--   public.*   the contract the BI repo reads
--
-- Two properties make this safe:
--
--   1. NO DELETES. The refresh only inserts and updates. If Windsor drops a row from its
--      window -- or a task is misconfigured and wipes the table -- core keeps its copy and
--      reporting is unaffected. This is the whole point.
--
--   2. NULLS NEVER OVERWRITE VALUES. Every column merges as
--      COALESCE(EXCLUDED.col, core.col). If a field list is narrowed (which happened
--      repeatedly while configuring these tasks) Windsor starts sending NULL for the dropped
--      column; without this, that NULL would silently erase good history. Real revisions
--      still propagate, because a real value is never NULL.
--
-- Trade-off accepted: a value that legitimately becomes NULL upstream will not propagate.
-- For daily metric counts that does not happen, and the protection is worth far more than
-- the edge case.

CREATE SCHEMA IF NOT EXISTS core;

-- ---------------------------------------------------------------------------
-- Durable tables. Deliberately NOT `LIKE windsor.*` -- staging has picked up stray
-- columns from misconfigured tasks (conversions_purchase, and 15 ad columns ALTERed
-- into the GA4 table). core carries only what we actually mean to keep.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.ga4_daily_totals (
  date                     date PRIMARY KEY,
  sessions                 numeric,
  totalusers               numeric,
  newusers                 numeric,
  screen_page_views        numeric,
  bounce_rate              numeric,
  conversions              numeric,
  average_session_duration numeric,
  user_conversion_rate     numeric,
  ecommerce_purchases      numeric,
  transactions             numeric,
  synced_at                timestamptz,   -- Windsor's own stamp, carried through
  first_seen_at            timestamptz NOT NULL DEFAULT now(),
  last_seen_at             timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS core.ads_google_daily (
  date             date NOT NULL,
  campaign_id      text NOT NULL,
  account_name     text,
  campaign         text,
  impressions      numeric,
  clicks           numeric,
  spend            numeric,
  conversions      numeric,
  conversion_value numeric,
  currency         text,
  datasource       text,
  synced_at        timestamptz,
  first_seen_at    timestamptz NOT NULL DEFAULT now(),
  last_seen_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (date, campaign_id)
);

CREATE TABLE IF NOT EXISTS core.ads_meta_daily (
  date               date NOT NULL,
  campaign_id        text NOT NULL,
  publisher_platform text NOT NULL,
  account_name       text,
  campaign           text,
  impressions        numeric,
  clicks             numeric,
  link_clicks        numeric,
  spend              numeric,
  purchases          numeric,   -- actions_offsite_conversion_fb_pixel_purchase
  purchase_value     numeric,   -- action_values_offsite_conversion_fb_pixel_purchase
  synced_at          timestamptz,
  first_seen_at      timestamptz NOT NULL DEFAULT now(),
  last_seen_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (date, campaign_id, publisher_platform)
);

CREATE INDEX IF NOT EXISTS idx_core_ga4_date ON core.ga4_daily_totals (date);
CREATE INDEX IF NOT EXISTS idx_core_google_date ON core.ads_google_daily (date);
CREATE INDEX IF NOT EXISTS idx_core_meta_date ON core.ads_meta_daily (date);

-- ---------------------------------------------------------------------------
-- The refresh. Merge-only, null-safe. Returns a per-table row count so a caller
-- (or the freshness watchdog) can tell whether it actually did anything.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.refresh_from_windsor()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
AS $fn$
DECLARE
  n_ga4 integer := 0;
  n_google integer := 0;
  n_meta integer := 0;
BEGIN
  INSERT INTO core.ga4_daily_totals AS c
    (date, sessions, totalusers, newusers, screen_page_views, bounce_rate, conversions,
     average_session_duration, user_conversion_rate, ecommerce_purchases, transactions, synced_at)
  SELECT date, sessions, totalusers, newusers, screen_page_views, bounce_rate, conversions,
         average_session_duration, user_conversion_rate, ecommerce_purchases, transactions, synced_at
  FROM windsor.ga4_daily_totals
  ON CONFLICT (date) DO UPDATE SET
    sessions                 = COALESCE(EXCLUDED.sessions, c.sessions),
    totalusers               = COALESCE(EXCLUDED.totalusers, c.totalusers),
    newusers                 = COALESCE(EXCLUDED.newusers, c.newusers),
    screen_page_views        = COALESCE(EXCLUDED.screen_page_views, c.screen_page_views),
    bounce_rate              = COALESCE(EXCLUDED.bounce_rate, c.bounce_rate),
    conversions              = COALESCE(EXCLUDED.conversions, c.conversions),
    average_session_duration = COALESCE(EXCLUDED.average_session_duration, c.average_session_duration),
    user_conversion_rate     = COALESCE(EXCLUDED.user_conversion_rate, c.user_conversion_rate),
    ecommerce_purchases      = COALESCE(EXCLUDED.ecommerce_purchases, c.ecommerce_purchases),
    transactions             = COALESCE(EXCLUDED.transactions, c.transactions),
    synced_at                = COALESCE(EXCLUDED.synced_at, c.synced_at),
    last_seen_at             = now();
  GET DIAGNOSTICS n_ga4 = ROW_COUNT;

  INSERT INTO core.ads_google_daily AS c
    (date, campaign_id, account_name, campaign, impressions, clicks, spend,
     conversions, conversion_value, currency, datasource, synced_at)
  SELECT date, campaign_id, account_name, campaign, impressions, clicks, spend,
         conversions, conversion_value, currency, datasource, synced_at
  FROM windsor.ads_google_daily
  WHERE campaign_id IS NOT NULL
  ON CONFLICT (date, campaign_id) DO UPDATE SET
    account_name     = COALESCE(EXCLUDED.account_name, c.account_name),
    campaign         = COALESCE(EXCLUDED.campaign, c.campaign),
    impressions      = COALESCE(EXCLUDED.impressions, c.impressions),
    clicks           = COALESCE(EXCLUDED.clicks, c.clicks),
    spend            = COALESCE(EXCLUDED.spend, c.spend),
    conversions      = COALESCE(EXCLUDED.conversions, c.conversions),
    conversion_value = COALESCE(EXCLUDED.conversion_value, c.conversion_value),
    currency         = COALESCE(EXCLUDED.currency, c.currency),
    datasource       = COALESCE(EXCLUDED.datasource, c.datasource),
    synced_at        = COALESCE(EXCLUDED.synced_at, c.synced_at),
    last_seen_at     = now();
  GET DIAGNOSTICS n_google = ROW_COUNT;

  INSERT INTO core.ads_meta_daily AS c
    (date, campaign_id, publisher_platform, account_name, campaign, impressions, clicks,
     link_clicks, spend, purchases, purchase_value, synced_at)
  SELECT date, campaign_id, publisher_platform, account_name, campaign, impressions, clicks,
         link_clicks, spend,
         actions_offsite_conversion_fb_pixel_purchase,
         action_values_offsite_conversion_fb_pixel_purchase,
         synced_at
  FROM windsor.ads_meta_daily
  WHERE campaign_id IS NOT NULL AND publisher_platform IS NOT NULL
  ON CONFLICT (date, campaign_id, publisher_platform) DO UPDATE SET
    account_name   = COALESCE(EXCLUDED.account_name, c.account_name),
    campaign       = COALESCE(EXCLUDED.campaign, c.campaign),
    impressions    = COALESCE(EXCLUDED.impressions, c.impressions),
    clicks         = COALESCE(EXCLUDED.clicks, c.clicks),
    link_clicks    = COALESCE(EXCLUDED.link_clicks, c.link_clicks),
    spend          = COALESCE(EXCLUDED.spend, c.spend),
    purchases      = COALESCE(EXCLUDED.purchases, c.purchases),
    purchase_value = COALESCE(EXCLUDED.purchase_value, c.purchase_value),
    synced_at      = COALESCE(EXCLUDED.synced_at, c.synced_at),
    last_seen_at   = now();
  GET DIAGNOSTICS n_meta = ROW_COUNT;

  RETURN jsonb_build_object(
    'ran_at', now(),
    'ga4_rows', n_ga4,
    'google_rows', n_google,
    'meta_rows', n_meta
  );
END
$fn$;

COMMENT ON FUNCTION core.refresh_from_windsor() IS
  'Merge windsor.* staging into core.*. Never deletes; NULLs never overwrite existing values. '
  'Safe to run repeatedly. See migration 022 for why this layer exists.';

-- ---------------------------------------------------------------------------
-- Schedule. Every 30 min: the Windsor tasks run daily at staggered times (00:27, 01:21,
-- 09:07), so half-hourly means core is never more than 30 minutes behind a push, without
-- polling aggressively.
-- ---------------------------------------------------------------------------
SELECT cron.unschedule('core-refresh-from-windsor')
  WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'core-refresh-from-windsor');

SELECT cron.schedule('core-refresh-from-windsor', '*/30 * * * *',
                     $cron$SELECT core.refresh_from_windsor()$cron$);

GRANT USAGE ON SCHEMA core TO bi_chatbot_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA core TO bi_chatbot_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA core GRANT SELECT ON TABLES TO bi_chatbot_readonly;
