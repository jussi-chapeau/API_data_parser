-- Move GA4 demographics from daily to monthly grain (2026-09-22).
--
-- FOUND BY THE BI BOT, NOT BY US. It asked why age 35-44 was only 4.5% of known visitors and
-- whether a bucket was being dropped in the sync. It was. The sync grain was destroying the
-- data it was meant to carry.
--
-- THE MECHANISM. GA4 applies its demographic thresholding PER QUERY. Windsor asks day by day,
-- so a bucket with a handful of sessions on a given day is suppressed on that day. Sum those
-- days and the small buckets have been quietly eaten. Measured against the live connector,
-- last 90 days -- the same question asked two ways:
--
--     bucket     summed daily   period total     lost
--     unknown          11,410         12,502    1,092
--     55-64             1,663          1,806      143   ( 8%)
--     25-34             1,155          1,518      363   (24%)
--     65+               1,085          1,400      315   (23%)
--     45-54               813          1,300      487   (37%)
--     35-44               240            899      659   (73%)
--     18-24                 0            249      249   (100% -- absent entirely)
--     TOTAL            16,366         19,674    3,308   (16.8%)
--
-- The loss is inversely proportional to bucket size, so daily querying does not just lose
-- volume -- it systematically BIASES the age distribution toward the largest buckets. The
-- 18-24 band does not exist in the daily data at all. Any age profile built on it would have
-- been wrong in a consistent, confident-looking direction.
--
-- THE FIX. Ask for `year_month` instead of `date`. Thresholding then applies once per month
-- instead of thirty times, and essentially everything survives: 19,484 sessions recovered of
-- the 19,674 period total (99%), with 35-44 at 897 instead of 240 and 18-24 at 249 instead of
-- zero.
--
-- The cost is monthly rather than daily granularity, which for demographics is no loss at all:
-- nobody needs a daily age split, and the daily version was actively misleading. Every other
-- feed stays daily -- this trade is specific to dimensions GA4 thresholds.
--
-- The tables are rebuilt rather than migrated. The rows currently in them came from the daily
-- tasks and carry exactly the bias described above; keeping them would mean mixing two
-- incompatible grains in one table.
--
-- ---------------------------------------------------------------------------
-- WINDSOR TASKS MUST BE EDITED -- the old ones write a `date` column that no longer exists.
--
-- TASK 3  -> windsor.ga4_daily_age     Columns to Match: year_month,age
--   fields=year_month,age,sessions,totalusers,newusers,engaged_sessions,checkouts
-- TASK 4  -> windsor.ga4_daily_gender  Columns to Match: year_month,gender
--   fields=year_month,gender,sessions,totalusers,newusers,engaged_sessions,checkouts
--
-- year_month arrives as text 'YYYYMM' (e.g. '202609'), not a date -- typed accordingly.
-- ---------------------------------------------------------------------------

DROP VIEW IF EXISTS public.analytics_ga_daily_demographics;
DROP TABLE IF EXISTS windsor.ga4_daily_age;
DROP TABLE IF EXISTS windsor.ga4_daily_gender;
DROP TABLE IF EXISTS core.ga4_daily_age;
DROP TABLE IF EXISTS core.ga4_daily_gender;

DELETE FROM core.schema_contract WHERE view_name = 'analytics_ga_daily_demographics';

CREATE TABLE windsor.ga4_daily_age (
  year_month       text NOT NULL,          -- 'YYYYMM' as GA4 returns it
  age              text NOT NULL,
  sessions numeric, totalusers numeric, newusers numeric,
  engaged_sessions numeric, checkouts numeric, synced_at timestamptz,
  PRIMARY KEY (year_month, age)
);

CREATE TABLE windsor.ga4_daily_gender (
  year_month       text NOT NULL,
  gender           text NOT NULL,
  sessions numeric, totalusers numeric, newusers numeric,
  engaged_sessions numeric, checkouts numeric, synced_at timestamptz,
  PRIMARY KEY (year_month, gender)
);

ALTER TABLE windsor.ga4_daily_age    OWNER TO windsor_writer;
ALTER TABLE windsor.ga4_daily_gender OWNER TO windsor_writer;

CREATE TABLE core.ga4_daily_age (
  year_month text NOT NULL, age text NOT NULL,
  sessions numeric, totalusers numeric, newusers numeric,
  engaged_sessions numeric, checkouts numeric, synced_at timestamptz,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (year_month, age)
);

CREATE TABLE core.ga4_daily_gender (
  year_month text NOT NULL, gender text NOT NULL,
  sessions numeric, totalusers numeric, newusers numeric,
  engaged_sessions numeric, checkouts numeric, synced_at timestamptz,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (year_month, gender)
);

CREATE OR REPLACE FUNCTION core.refresh_demographics_from_windsor()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = core, windsor, pg_temp
AS $fn$
DECLARE n_age integer := 0; n_gen integer := 0;
BEGIN
  INSERT INTO core.ga4_daily_age AS c
    (year_month, age, sessions, totalusers, newusers, engaged_sessions, checkouts, synced_at)
  SELECT year_month, age, sessions, totalusers, newusers, engaged_sessions, checkouts, synced_at
  FROM windsor.ga4_daily_age WHERE age IS NOT NULL AND year_month IS NOT NULL
  ON CONFLICT (year_month, age) DO UPDATE SET
    sessions         = COALESCE(EXCLUDED.sessions, c.sessions),
    totalusers       = COALESCE(EXCLUDED.totalusers, c.totalusers),
    newusers         = COALESCE(EXCLUDED.newusers, c.newusers),
    engaged_sessions = COALESCE(EXCLUDED.engaged_sessions, c.engaged_sessions),
    checkouts        = COALESCE(EXCLUDED.checkouts, c.checkouts),
    synced_at        = COALESCE(EXCLUDED.synced_at, c.synced_at),
    last_seen_at     = now();
  GET DIAGNOSTICS n_age = ROW_COUNT;

  INSERT INTO core.ga4_daily_gender AS c
    (year_month, gender, sessions, totalusers, newusers, engaged_sessions, checkouts, synced_at)
  SELECT year_month, gender, sessions, totalusers, newusers, engaged_sessions, checkouts, synced_at
  FROM windsor.ga4_daily_gender WHERE gender IS NOT NULL AND year_month IS NOT NULL
  ON CONFLICT (year_month, gender) DO UPDATE SET
    sessions         = COALESCE(EXCLUDED.sessions, c.sessions),
    totalusers       = COALESCE(EXCLUDED.totalusers, c.totalusers),
    newusers         = COALESCE(EXCLUDED.newusers, c.newusers),
    engaged_sessions = COALESCE(EXCLUDED.engaged_sessions, c.engaged_sessions),
    checkouts        = COALESCE(EXCLUDED.checkouts, c.checkouts),
    synced_at        = COALESCE(EXCLUDED.synced_at, c.synced_at),
    last_seen_at     = now();
  GET DIAGNOSTICS n_gen = ROW_COUNT;

  RETURN jsonb_build_object('ran_at', now(), 'age_rows', n_age, 'gender_rows', n_gen);
END
$fn$;

-- Renamed: it is monthly now, and calling it "daily" would be the kind of stale label that
-- causes exactly this class of mistake.
CREATE VIEW public.analytics_ga_monthly_demographics AS
WITH u AS (
  SELECT year_month, 'age'::text AS dimension, age AS bucket,
         sessions, totalusers, newusers, engaged_sessions, checkouts, synced_at
  FROM core.ga4_daily_age
  UNION ALL
  SELECT year_month, 'gender'::text, gender,
         sessions, totalusers, newusers, engaged_sessions, checkouts, synced_at
  FROM core.ga4_daily_gender
)
SELECT to_date(year_month, 'YYYYMM') AS month,
       dimension, bucket,
       sessions::integer         AS sessions,
       totalusers::integer       AS total_users,
       newusers::integer         AS new_users,
       engaged_sessions::integer AS engaged_sessions,
       checkouts::integer        AS begin_checkout_count,
       synced_at,
       'MONTHLY on purpose. GA4 thresholds demographics per query, so asking day by day '
       'suppressed small buckets: over 90 days the 35-44 band lost 73% of its sessions and '
       '18-24 disappeared entirely. Monthly recovers 99%. Do NOT request a daily version. '
       'Separately, GA4 resolves a demographic only for users signed in to Google with ad '
       'personalisation on -- about 67% of age and 60% of gender is the `unknown` bucket, and '
       'the known remainder skews older and more Android. Never filter out `unknown` and never '
       'rescale the known part to 100%.'::text AS demographic_caveat
FROM u;

COMMENT ON VIEW public.analytics_ga_monthly_demographics IS
  'GA4 age and gender at MONTHLY grain. Daily was tried first and systematically destroyed the '
  'small buckets through GA4 per-query thresholding -- see migration 053. Replaces '
  'analytics_ga_daily_demographics, which was never populated with trustworthy data.';

GRANT SELECT ON public.analytics_ga_monthly_demographics TO bi_chatbot_readonly;
REVOKE ALL ON public.analytics_ga_monthly_demographics FROM anon, authenticated, PUBLIC;

INSERT INTO core.schema_contract (view_name, column_name, ordinal, data_type)
SELECT c.table_name, c.column_name, c.ordinal_position, c.data_type
FROM information_schema.columns c
WHERE c.table_schema='public' AND c.table_name='analytics_ga_monthly_demographics'
ON CONFLICT DO NOTHING;
