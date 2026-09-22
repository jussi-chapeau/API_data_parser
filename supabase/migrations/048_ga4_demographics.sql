-- GA4 age and gender feeds (2026-09-22).
--
-- WHY GA4 AND NOT GOOGLE ADS. Both connectors expose demographics, but the coverage is not
-- comparable. Measured live over the last 30 days:
--
--   Google Ads  demographic views carry EUR 1,044 of EUR 9,504 spend   = 11.0%
--               and 49.1% of those clicks are AGE_RANGE_UNDETERMINED   -> ~5.6% effective
--   GA4         age-dimensioned sessions 6,561 of 7,129 baseline       = 92.0%
--               of which 33.4% resolve to a real bucket                -> ~31% effective
--
-- GA4 is roughly six times better. Google Ads demographics were considered and rejected.
--
-- WHY TWO TABLES AND NOT ONE CROSSED TABLE. A single table keyed (date, age, gender) would fit
-- Windsor's 3-column "Columns to Match" cap exactly and cost one task slot instead of two. It
-- was tested and is materially worse, because crossing the dimensions trips GA4's own
-- thresholding:
--
--   age alone            6 buckets, 6,561 sessions, 92.0% of baseline
--   gender alone         3 buckets, 7,040 sessions
--   age x gender         8 cells,   5,049 sessions, 70.8% of baseline, 17.0% fully known
--                        -- and the 35-44 bucket vanishes completely
--
-- Losing a whole age band to suppression is too high a price for one task slot.
--
-- THE `unknown` BUCKET IS THE POINT, NOT AN ERROR. 66.6% of age and 59.6% of gender resolve to
-- `unknown`, because GA4 only identifies users who are signed in to Google with ad
-- personalisation enabled. It is carried through as a first-class row and must never be
-- filtered out: the known subset is NOT a random sample of traffic. It skews toward signed-in
-- Google users, which means older and more Android -- and the observed age profile (55-64 and
-- 65+ are 57% of KNOWN ages, for a moving company) is very likely an artefact of who Google can
-- identify rather than a fact about who moves house.
--
-- That is the same failure mode as the unweighted income quintiles in migration 042: a real
-- number that measures something other than it appears to. Hence the caveat column, following
-- the precedent set in migration 045 -- the consumer is an LLM whose column descriptions come
-- from a hardcoded dict in its own repo, so a COMMENT would never reach its prompt.
--
-- ---------------------------------------------------------------------------
-- WINDSOR TASK SETUP -- two tasks, source Google Analytics 4.
--
-- TASK A  -> windsor.ga4_daily_age      Columns to Match: date, age_bucket
-- TASK B  -> windsor.ga4_daily_gender   Columns to Match: date, gender
--   Fields (identical either side of the dimension):
--     Date                               -> date
--     Age            (task A only)       -> age_bucket
--     Gender         (task B only)       -> gender
--     Sessions                           -> sessions
--     Total users                        -> totalusers
--     New users                          -> newusers
--     Engaged sessions                   -> engaged_sessions
--     Key event count for begin_checkout -> checkouts
--   5 metrics, 2 dimensions -- well inside GA4's 10-metric cap.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS windsor.ga4_daily_age (
  date             date NOT NULL,
  age_bucket       text NOT NULL,
  sessions         numeric,
  totalusers       numeric,
  newusers         numeric,
  engaged_sessions numeric,
  checkouts        numeric,
  synced_at        timestamptz,
  PRIMARY KEY (date, age_bucket)
);

CREATE TABLE IF NOT EXISTS windsor.ga4_daily_gender (
  date             date NOT NULL,
  gender           text NOT NULL,
  sessions         numeric,
  totalusers       numeric,
  newusers         numeric,
  engaged_sessions numeric,
  checkouts        numeric,
  synced_at        timestamptz,
  PRIMARY KEY (date, gender)
);

ALTER TABLE windsor.ga4_daily_age    OWNER TO windsor_writer;
ALTER TABLE windsor.ga4_daily_gender OWNER TO windsor_writer;

-- Durable copies: merge-only, NULLs never overwrite (migration 022).
CREATE TABLE IF NOT EXISTS core.ga4_daily_age (
  date date NOT NULL, age_bucket text NOT NULL,
  sessions numeric, totalusers numeric, newusers numeric,
  engaged_sessions numeric, checkouts numeric, synced_at timestamptz,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (date, age_bucket)
);

CREATE TABLE IF NOT EXISTS core.ga4_daily_gender (
  date date NOT NULL, gender text NOT NULL,
  sessions numeric, totalusers numeric, newusers numeric,
  engaged_sessions numeric, checkouts numeric, synced_at timestamptz,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (date, gender)
);

CREATE INDEX IF NOT EXISTS idx_core_ga4_age_date    ON core.ga4_daily_age (date);
CREATE INDEX IF NOT EXISTS idx_core_ga4_gender_date ON core.ga4_daily_gender (date);

CREATE OR REPLACE FUNCTION core.refresh_demographics_from_windsor()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = core, windsor, pg_temp
AS $fn$
DECLARE n_age integer := 0; n_gen integer := 0;
BEGIN
  INSERT INTO core.ga4_daily_age AS c
    (date, age_bucket, sessions, totalusers, newusers, engaged_sessions, checkouts, synced_at)
  SELECT date, age_bucket, sessions, totalusers, newusers, engaged_sessions, checkouts, synced_at
  FROM windsor.ga4_daily_age WHERE age_bucket IS NOT NULL
  ON CONFLICT (date, age_bucket) DO UPDATE SET
    sessions         = COALESCE(EXCLUDED.sessions, c.sessions),
    totalusers       = COALESCE(EXCLUDED.totalusers, c.totalusers),
    newusers         = COALESCE(EXCLUDED.newusers, c.newusers),
    engaged_sessions = COALESCE(EXCLUDED.engaged_sessions, c.engaged_sessions),
    checkouts        = COALESCE(EXCLUDED.checkouts, c.checkouts),
    synced_at        = COALESCE(EXCLUDED.synced_at, c.synced_at),
    last_seen_at     = now();
  GET DIAGNOSTICS n_age = ROW_COUNT;

  INSERT INTO core.ga4_daily_gender AS c
    (date, gender, sessions, totalusers, newusers, engaged_sessions, checkouts, synced_at)
  SELECT date, gender, sessions, totalusers, newusers, engaged_sessions, checkouts, synced_at
  FROM windsor.ga4_daily_gender WHERE gender IS NOT NULL
  ON CONFLICT (date, gender) DO UPDATE SET
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

SELECT cron.unschedule('core-refresh-demographics')
  WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'core-refresh-demographics');

SELECT cron.schedule('core-refresh-demographics', '13,43 * * * *',
                     $cron$SELECT core.refresh_demographics_from_windsor()$cron$);

-- ---------------------------------------------------------------------------
-- One long-format contract view. Long rather than two wide views because the consumer's
-- question is "how does <metric> vary by demographic", and a single `dimension`/`bucket` pair
-- makes `unknown` impossible to drop by accident -- it is a row, not a missing column.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW public.analytics_ga_daily_demographics AS
WITH u AS (
  SELECT date, 'age'::text AS dimension, age_bucket AS bucket,
         sessions, totalusers, newusers, engaged_sessions, checkouts, synced_at
  FROM core.ga4_daily_age
  UNION ALL
  SELECT date, 'gender'::text, gender,
         sessions, totalusers, newusers, engaged_sessions, checkouts, synced_at
  FROM core.ga4_daily_gender
)
SELECT date, dimension, bucket,
       sessions::integer          AS sessions,
       totalusers::integer        AS total_users,
       newusers::integer          AS new_users,
       engaged_sessions::integer  AS engaged_sessions,
       checkouts::integer         AS begin_checkout_count,
       synced_at,
       'GA4 resolves a demographic only for users signed in to Google with ad personalisation '
       'on: about 67% of age and 60% of gender sessions are the `unknown` bucket. The known '
       'remainder is NOT a random sample -- it skews older and more Android, so an apparent '
       'age profile may describe who Google can identify rather than who the customers are. '
       'Always report the unknown bucket alongside any split, never filter it out, and never '
       'rescale the known part to 100%.'::text AS demographic_caveat
FROM u;

COMMENT ON VIEW public.analytics_ga_daily_demographics IS
  'GA4 age and gender in long form (dimension/bucket). Chosen over Google Ads demographics, '
  'which reach only 11% of ad spend. Age and gender are separate pulls, NOT crossed: crossing '
  'them drops coverage from 92% to 71% and suppresses the 35-44 band entirely. See migration 048.';

GRANT SELECT ON public.analytics_ga_daily_demographics TO bi_chatbot_readonly;
REVOKE ALL ON public.analytics_ga_daily_demographics FROM anon, authenticated, PUBLIC;

INSERT INTO core.schema_contract (view_name, column_name, ordinal, data_type)
SELECT c.table_name, c.column_name, c.ordinal_position, c.data_type
FROM information_schema.columns c
WHERE c.table_schema = 'public' AND c.table_name = 'analytics_ga_daily_demographics'
ON CONFLICT DO NOTHING;
