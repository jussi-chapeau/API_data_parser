-- Align staging column names with what Windsor actually returns (2026-09-22).
--
-- Windsor names destination columns after the API FIELD NAMES, not after whatever we called the
-- column. Migrations 046 and 048 were written from the field-picker LABELS ("Age", "Key event
-- count for purchase") rather than the API ids, and two tables ended up mismatched. Verified
-- against the live connector by reading the JSON keys back:
--
--   fields=date,age,sessions,...              -> returns `age`
--   fields=...,conversions_purchase,...       -> returns `conversions_purchase`
--   fields=...,conversions_whatsapp_click,... -> returns `conversions_whatsapp_click`
--
-- ONE OF THESE WOULD HAVE BEEN SILENT AND FATAL. `windsor.ga4_daily_age.age_bucket` is NOT NULL
-- and part of the primary key. Windsor owns the table (migration 016), so on first run it would
-- have issued ALTER TABLE ... ADD COLUMN age, then inserted rows with `age` populated and
-- `age_bucket` NULL -- violating the NOT NULL constraint and failing the ENTIRE upload, retrying
-- on every schedule, with nothing visible in Supabase except a table that never fills. That is
-- precisely the outage shape migration 016 was written to prevent, reintroduced by getting a
-- column name wrong.
--
-- The landing-page mismatch is the milder version: `purchases` and `whatsapp_clicks` would have
-- sat permanently NULL while Windsor quietly added two correctly-named columns beside them.
--
-- Both tables are empty -- no Windsor task has been created yet -- so these are plain renames
-- with nothing to migrate. Checked before renaming rather than assumed.
--
-- `windsor.ga4_daily_gender` and `windsor.ads_google_landing` were verified correct and are
-- left alone: gender / campaign, campaign_id, clicks, date, final_url, impressions, spend.

DO $do$
BEGIN
  IF (SELECT count(*) FROM windsor.ga4_daily_age) <> 0
     OR (SELECT count(*) FROM windsor.ga4_daily_landing) <> 0 THEN
    RAISE EXCEPTION 'Refusing to rename: staging tables are not empty. Check what landed first.';
  END IF;
END
$do$;

ALTER TABLE windsor.ga4_daily_age     RENAME COLUMN age_bucket      TO age;
ALTER TABLE core.ga4_daily_age        RENAME COLUMN age_bucket      TO age;

ALTER TABLE windsor.ga4_daily_landing RENAME COLUMN purchases       TO conversions_purchase;
ALTER TABLE windsor.ga4_daily_landing RENAME COLUMN whatsapp_clicks TO conversions_whatsapp_click;
ALTER TABLE core.ga4_daily_landing    RENAME COLUMN purchases       TO conversions_purchase;
ALTER TABLE core.ga4_daily_landing    RENAME COLUMN whatsapp_clicks TO conversions_whatsapp_click;

-- Refresh functions referenced the old names.
CREATE OR REPLACE FUNCTION core.refresh_demographics_from_windsor()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = core, windsor, pg_temp
AS $fn$
DECLARE n_age integer := 0; n_gen integer := 0;
BEGIN
  INSERT INTO core.ga4_daily_age AS c
    (date, age, sessions, totalusers, newusers, engaged_sessions, checkouts, synced_at)
  SELECT date, age, sessions, totalusers, newusers, engaged_sessions, checkouts, synced_at
  FROM windsor.ga4_daily_age WHERE age IS NOT NULL
  ON CONFLICT (date, age) DO UPDATE SET
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
     engagement_rate, checkouts, conversions_purchase, conversions_whatsapp_click, synced_at)
  SELECT date, landing_page, session_source_medium, sessions, totalusers, engaged_sessions,
         engagement_rate, checkouts, conversions_purchase, conversions_whatsapp_click, synced_at
  FROM windsor.ga4_daily_landing
  WHERE landing_page IS NOT NULL AND session_source_medium IS NOT NULL
  ON CONFLICT (date, landing_page, session_source_medium) DO UPDATE SET
    sessions                   = COALESCE(EXCLUDED.sessions, c.sessions),
    totalusers                 = COALESCE(EXCLUDED.totalusers, c.totalusers),
    engaged_sessions           = COALESCE(EXCLUDED.engaged_sessions, c.engaged_sessions),
    engagement_rate            = COALESCE(EXCLUDED.engagement_rate, c.engagement_rate),
    checkouts                  = COALESCE(EXCLUDED.checkouts, c.checkouts),
    conversions_purchase       = COALESCE(EXCLUDED.conversions_purchase, c.conversions_purchase),
    conversions_whatsapp_click = COALESCE(EXCLUDED.conversions_whatsapp_click,
                                          c.conversions_whatsapp_click),
    synced_at                  = COALESCE(EXCLUDED.synced_at, c.synced_at),
    last_seen_at               = now();
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

-- Views referenced the old names too. Column NAMES in the published contract are unchanged
-- (purchase_count, whatsapp_click_count, bucket), so nothing downstream moves.
CREATE OR REPLACE VIEW public.analytics_ga_daily_landing AS
SELECT date, landing_page, session_source_medium AS source_medium,
       sessions::integer                   AS sessions,
       totalusers::integer                 AS total_users,
       engaged_sessions::integer           AS engaged_sessions,
       engagement_rate,
       checkouts::integer                  AS begin_checkout_count,
       conversions_purchase::integer       AS purchase_count,
       conversions_whatsapp_click::integer AS whatsapp_click_count,
       synced_at
FROM core.ga4_daily_landing;

CREATE OR REPLACE VIEW public.analytics_ga_daily_demographics AS
WITH u AS (
  SELECT date, 'age'::text AS dimension, age AS bucket,
         sessions, totalusers, newusers, engaged_sessions, checkouts, synced_at
  FROM core.ga4_daily_age
  UNION ALL
  SELECT date, 'gender'::text, gender,
         sessions, totalusers, newusers, engaged_sessions, checkouts, synced_at
  FROM core.ga4_daily_gender
)
SELECT date, dimension, bucket,
       sessions::integer         AS sessions,
       totalusers::integer       AS total_users,
       newusers::integer         AS new_users,
       engaged_sessions::integer AS engaged_sessions,
       checkouts::integer        AS begin_checkout_count,
       synced_at,
       'GA4 resolves a demographic only for users signed in to Google with ad personalisation '
       'on: about 67% of age and 60% of gender sessions are the `unknown` bucket. The known '
       'remainder is NOT a random sample -- it skews older and more Android, so an apparent '
       'age profile may describe who Google can identify rather than who the customers are. '
       'Always report the unknown bucket alongside any split, never filter it out, and never '
       'rescale the known part to 100%.'::text AS demographic_caveat
FROM u;
