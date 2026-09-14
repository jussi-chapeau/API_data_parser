-- Full GA4 schema for Windsor.ai, replacing the 2026-09-14 test run (2026-09-14).
--
-- The test run captured 8 fields at one grain. This is the complete set needed to serve the
-- contract apukuski-bi-chatbot depends on -- all three grains, every column the consumer
-- reads, and the ones it merely stores.
--
-- Field names below are `id` values pulled from the authoritative catalogue at
-- https://connectors.windsor.ai/googleanalytics4/fields (482 fields), NOT guessed and NOT
-- GA4's own API names. Windsor's names are the contract: `totalusers` not `total_users`,
-- `screen_page_views` not `views`, `conversions` not `key_events` (its UI says "Key events",
-- the field is `conversions`). The handover notes this has caused two incidents already.
--
-- TWO CORRECTIONS TO THE TEST RUN, both from checking the catalogue:
--
-- 1. `conversions_purchase` DOES NOT EXIST in Windsor's GA4 catalogue -- the only
--    conversions field is `conversions`. The test run requested it and got 0.0 for all 30
--    days. That reading was taken as evidence of a GA4 tagging problem ("1 purchase against
--    254 real orders"). It may equally be an artefact of an unrecognised field name. The
--    real purchase metrics are `ecommerce_purchases` and `transactions`; both are captured
--    below so the question can be settled with valid fields before anyone concludes GA4
--    tagging is broken.
--
-- 2. Three columns the test run had to NULL out are available after all:
--    `average_session_duration` -> avg_session_length_sec
--    `user_conversion_rate`     -> user_conversion_rate
--    `screen_page_views_per_session` -> views_per_session (no need to compute it in the view)
--    So the full config restores 100% of the legacy schema with no synthetic NULLs.
--
-- THREE TABLES AT THREE GRAINS, deliberately. GA4 applies thresholding and `(not set)`
-- bucketing per query, so a breakdown never sums to the total. Collapsing them into one wide
-- table would produce numbers that silently disagree with GA4's own UI. It is also required
-- by GA4's dimension/metric compatibility rules -- geo and attribution dimensions cannot
-- always be queried together.

-- ---------------------------------------------------------------------------
-- 1. Totals -- one row per day
-- ---------------------------------------------------------------------------
-- The test-run table already exists with a partial column set; extend rather than recreate
-- so the 30 days already landed (2026-08-15..09-13) are kept.
ALTER TABLE windsor.ga4_daily_totals
  ADD COLUMN IF NOT EXISTS screen_page_views_per_session numeric,
  ADD COLUMN IF NOT EXISTS average_session_duration      numeric,
  ADD COLUMN IF NOT EXISTS user_conversion_rate          numeric,
  ADD COLUMN IF NOT EXISTS ecommerce_purchases           numeric,
  ADD COLUMN IF NOT EXISTS transactions                  numeric;

-- Not a real Windsor field; it returned 0.0 for every row it was requested for. Dropping it
-- so nobody reads those zeros as a measurement. See correction 1 above.
ALTER TABLE windsor.ga4_daily_totals DROP COLUMN IF EXISTS conversions_purchase;

COMMENT ON TABLE windsor.ga4_daily_totals IS
  'Windsor.ai GA4 daily totals, one row per date. Column names are Windsor field ids. '
  'Staging: read via the public.analytics_ga_daily_totals view, never directly.';

-- ---------------------------------------------------------------------------
-- 2. Source / medium -- one row per day x source_medium
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS windsor.ga4_daily_source (
  date              date    NOT NULL,
  -- NOT NULL on purpose. GA4 returns the literal string '(not set)' rather than null for an
  -- unknown dimension, so this should never fire -- but if Windsor ever sent null, a
  -- nullable key column would silently defeat the upsert (null <> null in a unique index)
  -- and append duplicate rows forever. Failing loudly is the better outcome.
  source_medium     text    NOT NULL DEFAULT '(not set)',
  sessions              numeric,
  totalusers            numeric,
  conversions           numeric,
  engagement_rate       numeric,
  engaged_sessions      numeric,
  checkouts             numeric,   -- -> begin_checkout_count in the consumer contract
  synced_at         timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (date, source_medium)
);
ALTER TABLE windsor.ga4_daily_source DISABLE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------------
-- 3. Geography -- one row per day x city x region x country
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS windsor.ga4_daily_geo (
  date              date    NOT NULL,
  city              text    NOT NULL DEFAULT '(not set)',
  region            text    NOT NULL DEFAULT '(not set)',
  country           text    NOT NULL DEFAULT '(not set)',
  sessions              numeric,
  totalusers            numeric,
  newusers              numeric,
  conversions           numeric,
  user_conversion_rate  numeric,
  checkouts             numeric,
  synced_at         timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (date, city, region, country)
);
ALTER TABLE windsor.ga4_daily_geo DISABLE ROW LEVEL SECURITY;

-- RLS off on all three: Supabase enables it automatically when a table is created, and RLS
-- with no policy returns ZERO ROWS SILENTLY rather than erroring. That is the same failure
-- shape that hid the five-week outage -- the consumer would report "no GA4 data" instead of
-- a permission problem. These are staging tables behind views; access is controlled by the
-- grants below.

-- bi_chatbot_readonly already has SELECT on future windsor tables via ALTER DEFAULT
-- PRIVILEGES, but only for tables created BY windsor_writer. These were created by postgres,
-- so grant explicitly.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bi_chatbot_readonly') THEN
    GRANT SELECT ON windsor.ga4_daily_source, windsor.ga4_daily_geo TO bi_chatbot_readonly;
  END IF;
  -- Windsor issues an unconditional CREATE TABLE IF NOT EXISTS before every insert, and
  -- Postgres checks schema CREATE privilege BEFORE the IF NOT EXISTS short-circuit -- so the
  -- writer needs CREATE on the schema or every single run fails with "permission denied for
  -- schema", even though the table already exists.
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'windsor_writer') THEN
    GRANT USAGE, CREATE ON SCHEMA windsor TO windsor_writer;
    GRANT SELECT, INSERT, UPDATE ON windsor.ga4_daily_totals, windsor.ga4_daily_source,
                                    windsor.ga4_daily_geo TO windsor_writer;
  END IF;
END
$$;
