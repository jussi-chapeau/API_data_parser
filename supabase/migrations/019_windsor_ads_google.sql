-- Windsor staging table for Google Ads (2026-09-14).
--
-- Unlike GA4 (dead 5 weeks) and Meta (deliberately wound down), this feed is HEALTHY:
-- 4,938 rows, complete every month since 2024-08, data through 2026-09-13. Migrating it is
-- consolidation ahead of the Supermetrics cancellation, not repair -- so it moves last and
-- carefully. It also has the most history to protect.
--
-- Column names are Windsor field ids from https://connectors.windsor.ai/google_ads/fields
-- (2,542 fields), checked against the catalogue rather than assumed. Note `spend`, not our
-- `cost_eur` -- the alias back to the consumer's column name happens in the view.
--
-- CAMPAIGN GRAIN, matching legacy ads_google_campaign_daily exactly. Google Ads has no
-- placement dimension to preserve (that is Meta-specific), so PK is (date, campaign_id).
--
-- A WARNING THAT BELONGS NEXT TO THE DATA: `conversions` is NOT comparable with Meta's
-- purchase count. Google Ads recorded 9,311 conversions over two years -- it counts calls,
-- form fills and other conversion actions -- while Meta recorded 258 website purchases over
-- thirteen months. Summing `conversions` across platforms produces a meaningless number.
-- Spend, impressions and clicks ARE comparable; those are what the planned cross-platform
-- view exists to total.

CREATE TABLE IF NOT EXISTS windsor.ads_google_daily (
  date              date NOT NULL,
  account_name      text,
  campaign_id       text NOT NULL,
  campaign          text,
  impressions       numeric,
  clicks            numeric,
  spend             numeric,
  conversions       numeric,   -- platform-specific definition; see warning above
  conversion_value  numeric,
  currency          text,
  synced_at         timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (date, campaign_id)
);

-- RLS with no policy returns zero rows SILENTLY rather than erroring -- the failure shape
-- that hid the five-week GA4 outage. Off here; access via the grants below.
ALTER TABLE windsor.ads_google_daily DISABLE ROW LEVEL SECURITY;

-- Owned by windsor_writer per migration 016: Windsor issues ALTER TABLE ... ADD COLUMN for
-- unmatched fields, which needs ownership. Without it a single typo in a hand-typed field
-- list takes the whole feed down in a silent retry loop.
ALTER TABLE windsor.ads_google_daily OWNER TO windsor_writer;

COMMENT ON COLUMN windsor.ads_google_daily.conversions IS
  'Google Ads conversion actions (calls, form fills, purchases). NOT comparable with Meta '
  'purchases -- never SUM across platforms.';

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bi_chatbot_readonly') THEN
    GRANT SELECT ON windsor.ads_google_daily TO bi_chatbot_readonly;
  END IF;
END
$$;
