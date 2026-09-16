-- Bring GA4 source + geo online via core, restoring bi_website_report (2026-09-16).
--
-- Both feeds died with the Supermetrics sync on 2026-08-10, taking bi_website_report's
-- breakdowns and the dashboard's traffic-source chart with them.
--
-- VALIDATION, and the two feeds differ in an important way:
--
--   GEO IS EXACT. Monthly sessions match legacy precisely for March, April, May, June and
--   July (3,217 / 5,492 / 6,248 / 6,020 / 6,304). August differs only because legacy's final
--   day is partial -- the sync died mid-day on 08-10 -- so Windsor is the more complete side.
--
--   SOURCE RUNS ~11% BELOW LEGACY EVERY MONTH, and that is expected rather than wrong. GA4
--   refused `source_medium` (Attribution-scoped) alongside session metrics, so this feed uses
--   `session_source_medium` (Traffic Source-scoped) -- see migration 029. A different
--   attribution model produces different numbers. It is not missing data; it is a different
--   measurement.
--
-- BOUNDARY 2026-08-10/11 for both, deliberately. Legacy keeps all its validated history and
-- Windsor takes over exactly where the old sync stopped. That puts the source dimension
-- change at the same point as the feed change, instead of introducing an ~11% step in the
-- middle of otherwise continuous history. Anyone comparing traffic-source numbers across
-- 2026-08-10 is comparing two different dimensions -- noted in the view comment.
--
-- BOTH sides of every union are bounded. Bounding only one side is what produced 93
-- duplicated GA4 dates and 98 duplicated Meta keys earlier today (migration 027).

-- ---------------------------------------------------------------------------
-- 1. Durable tables
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.ga4_daily_source (
  date                  date NOT NULL,
  session_source_medium text NOT NULL,
  sessions              numeric,
  totalusers            numeric,
  conversions           numeric,
  engagement_rate       numeric,
  engaged_sessions      numeric,
  checkouts             numeric,
  synced_at             timestamptz,
  first_seen_at         timestamptz NOT NULL DEFAULT now(),
  last_seen_at          timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (date, session_source_medium)
);

CREATE TABLE IF NOT EXISTS core.ga4_daily_geo (
  date                 date NOT NULL,
  city                 text NOT NULL,
  country              text NOT NULL,
  sessions             numeric,
  totalusers           numeric,
  newusers             numeric,
  conversions          numeric,
  user_conversion_rate numeric,
  checkouts            numeric,
  synced_at            timestamptz,
  first_seen_at        timestamptz NOT NULL DEFAULT now(),
  last_seen_at         timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (date, city, country)
);

CREATE INDEX IF NOT EXISTS idx_core_src_date ON core.ga4_daily_source (date);
CREATE INDEX IF NOT EXISTS idx_core_geo_date ON core.ga4_daily_geo (date);

-- ---------------------------------------------------------------------------
-- 2. Extend the refresh. Same contract as migration 022: merge-only, NULLs never
--    overwrite values, nothing is ever deleted.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION core.refresh_windsor_source_geo()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
AS $fn$
DECLARE n_src integer := 0; n_geo integer := 0;
BEGIN
  INSERT INTO core.ga4_daily_source AS c
    (date, session_source_medium, sessions, totalusers, conversions,
     engagement_rate, engaged_sessions, checkouts, synced_at)
  SELECT date, session_source_medium, sessions, totalusers, conversions,
         engagement_rate, engaged_sessions, checkouts, synced_at
  FROM windsor.ga4_daily_source
  WHERE session_source_medium IS NOT NULL
  ON CONFLICT (date, session_source_medium) DO UPDATE SET
    sessions         = COALESCE(EXCLUDED.sessions, c.sessions),
    totalusers       = COALESCE(EXCLUDED.totalusers, c.totalusers),
    conversions      = COALESCE(EXCLUDED.conversions, c.conversions),
    engagement_rate  = COALESCE(EXCLUDED.engagement_rate, c.engagement_rate),
    engaged_sessions = COALESCE(EXCLUDED.engaged_sessions, c.engaged_sessions),
    checkouts        = COALESCE(EXCLUDED.checkouts, c.checkouts),
    synced_at        = COALESCE(EXCLUDED.synced_at, c.synced_at),
    last_seen_at     = now();
  GET DIAGNOSTICS n_src = ROW_COUNT;

  INSERT INTO core.ga4_daily_geo AS c
    (date, city, country, sessions, totalusers, newusers, conversions,
     user_conversion_rate, checkouts, synced_at)
  SELECT date, city, country, sessions, totalusers, newusers, conversions,
         user_conversion_rate, checkouts, synced_at
  FROM windsor.ga4_daily_geo
  WHERE city IS NOT NULL AND country IS NOT NULL
  ON CONFLICT (date, city, country) DO UPDATE SET
    sessions             = COALESCE(EXCLUDED.sessions, c.sessions),
    totalusers           = COALESCE(EXCLUDED.totalusers, c.totalusers),
    newusers             = COALESCE(EXCLUDED.newusers, c.newusers),
    conversions          = COALESCE(EXCLUDED.conversions, c.conversions),
    user_conversion_rate = COALESCE(EXCLUDED.user_conversion_rate, c.user_conversion_rate),
    checkouts            = COALESCE(EXCLUDED.checkouts, c.checkouts),
    synced_at            = COALESCE(EXCLUDED.synced_at, c.synced_at),
    last_seen_at         = now();
  GET DIAGNOSTICS n_geo = ROW_COUNT;

  RETURN jsonb_build_object('ran_at', now(), 'source_rows', n_src, 'geo_rows', n_geo);
END
$fn$;

SELECT core.refresh_windsor_source_geo();

SELECT cron.unschedule('core-refresh-source-geo')
  WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'core-refresh-source-geo');
SELECT cron.schedule('core-refresh-source-geo', '*/30 * * * *',
                     $cron$SELECT core.refresh_windsor_source_geo()$cron$);

GRANT SELECT ON core.ga4_daily_source, core.ga4_daily_geo TO bi_chatbot_readonly;
