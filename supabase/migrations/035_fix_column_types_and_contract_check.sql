-- Restore the column TYPES the contract promised, and make type drift detectable (2026-09-17).
--
-- Reported by apukuski-bi-chatbot: the migration broke code that casts these columns. I
-- verified the cutover on column names, order and row counts -- and those all passed -- but
-- never checked TYPES. Five columns silently widened from integer to numeric because the
-- Windsor staging tables are numeric throughout and a UNION resolves to the wider type:
--
--     analytics_ga_daily_source.engaged_sessions   integer -> numeric
--     ads_google_campaign_daily.impressions        integer -> numeric
--     ads_google_campaign_daily.clicks             integer -> numeric
--     ads_meta_placement_daily.impressions         integer -> numeric
--     ads_meta_placement_daily.link_clicks         integer -> numeric
--
-- I had cast some columns (sessions::int, totalusers::int) and simply missed these, which is
-- exactly the kind of gap a by-eye check does not catch.
--
-- NOTE ON THE META VIEW: its legacy half aggregates with SUM(), and SUM(integer) returns
-- bigint -- so BOTH halves need casting there, not just the Windsor one. Casting only the new
-- side would have left the type wrong and looked like a fix.
--
-- PART 2 is the more important half. `analytics_freshness` could never have caught this: the
-- data was entirely fresh, it was the shape that changed. Monitoring recency says nothing
-- about compatibility. So this adds a frozen record of the contract and a view that reports
-- drift from it -- names, ordinal positions AND types. Empty result means the contract holds.

-- ---------------------------------------------------------------------------
-- 1. Restore the promised types
-- ---------------------------------------------------------------------------
-- NOTE: applied as DROP + CREATE, not CREATE OR REPLACE -- Postgres refuses to change a
-- view column type in place ("cannot change data type of view column"). All four views
-- (including analytics_freshness, which depends on three of them) were dropped and
-- recreated in ONE multi-statement call so they are never simultaneously absent to a
-- reader.
CREATE OR REPLACE VIEW public.analytics_ga_daily_source AS
  SELECT date, source_medium, sessions, total_users, conversions,
         engagement_rate, engaged_sessions, begin_checkout_count, synced_at
  FROM public.analytics_ga_daily_source_legacy
  WHERE date <= DATE '2026-08-10'
  UNION ALL
  SELECT date, session_source_medium AS source_medium, sessions::int,
         totalusers::int AS total_users, conversions, engagement_rate,
         engaged_sessions::int, checkouts AS begin_checkout_count, synced_at
  FROM core.ga4_daily_source
  WHERE date >= DATE '2026-08-11';

CREATE OR REPLACE VIEW public.ads_google_campaign_daily AS
  SELECT date, campaign_id, campaign_name, impressions, clicks, cost_eur,
         conversions, conversion_value, synced_at
  FROM public.ads_google_campaign_daily_legacy
  WHERE date <= DATE '2026-03-15'
  UNION ALL
  SELECT date, campaign_id, campaign AS campaign_name,
         impressions::int, clicks::int, spend AS cost_eur,
         conversions, conversion_value, synced_at
  FROM core.ads_google_daily
  WHERE date >= DATE '2026-03-16';

CREATE OR REPLACE VIEW public.ads_meta_placement_daily AS
  -- Both halves cast: SUM(integer) is bigint, so the legacy side needs it too.
  SELECT date, campaign_id, campaign_name,
         split_part(placement, '|', 1)      AS placement,
         SUM(impressions)::int              AS impressions,
         SUM(cost_eur)                      AS cost_eur,
         SUM(link_clicks)::int              AS link_clicks,
         SUM(website_conversions)           AS website_conversions,
         SUM(website_conversion_value)      AS website_conversion_value,
         SUM(website_purchases)             AS website_purchases,
         MAX(synced_at)                     AS synced_at
  FROM public.ads_meta_placement_daily_legacy
  WHERE date <= DATE '2026-03-31'
  GROUP BY date, campaign_id, campaign_name, split_part(placement, '|', 1)
  UNION ALL
  SELECT date, campaign_id, campaign AS campaign_name, publisher_platform AS placement,
         impressions::int, spend AS cost_eur, link_clicks::int,
         purchases AS website_conversions, purchase_value AS website_conversion_value,
         purchases AS website_purchases, synced_at
  FROM core.ads_meta_daily
  WHERE date >= DATE '2026-04-01';

-- ---------------------------------------------------------------------------
-- 2. Freeze the contract and expose drift
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.schema_contract (
  view_name   text NOT NULL,
  column_name text NOT NULL,
  ordinal     integer NOT NULL,
  data_type   text NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (view_name, column_name)
);

COMMENT ON TABLE core.schema_contract IS
  'Frozen record of what apukuski-bi-chatbot depends on: column name, position and type for '
  'each contract view. Compare with public.schema_contract_drift. Update deliberately when a '
  'contract change is intended and agreed -- never to silence drift.';

-- Seed from the views as they now stand (types restored above).
INSERT INTO core.schema_contract (view_name, column_name, ordinal, data_type)
SELECT table_name, column_name, ordinal_position, data_type
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name IN ('analytics_ga_daily_totals','analytics_ga_daily_source',
                     'analytics_ga_daily_geo','ads_google_campaign_daily',
                     'ads_meta_placement_daily')
ON CONFLICT (view_name, column_name) DO UPDATE
  SET ordinal = EXCLUDED.ordinal, data_type = EXCLUDED.data_type, recorded_at = now();

-- Empty result = contract intact. Any row is a break a consumer will hit.
CREATE OR REPLACE VIEW public.schema_contract_drift AS
  SELECT COALESCE(c.view_name, a.table_name)     AS view_name,
         COALESCE(c.column_name, a.column_name)  AS column_name,
         CASE WHEN a.column_name IS NULL THEN 'COLUMN REMOVED'
              WHEN c.column_name IS NULL THEN 'COLUMN ADDED'
              WHEN c.data_type <> a.data_type THEN 'TYPE CHANGED'
              ELSE 'POSITION CHANGED' END        AS problem,
         c.data_type                             AS expected_type,
         a.data_type                             AS actual_type,
         c.ordinal                               AS expected_ordinal,
         a.ordinal_position                      AS actual_ordinal
  FROM core.schema_contract c
  FULL OUTER JOIN (
    SELECT table_name, column_name, ordinal_position, data_type
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name IN ('analytics_ga_daily_totals','analytics_ga_daily_source',
                         'analytics_ga_daily_geo','ads_google_campaign_daily',
                         'ads_meta_placement_daily')
  ) a ON a.table_name = c.view_name AND a.column_name = c.column_name
  WHERE a.column_name IS NULL OR c.column_name IS NULL
     OR c.data_type <> a.data_type OR c.ordinal <> a.ordinal_position;

COMMENT ON VIEW public.schema_contract_drift IS
  'Rows here mean a contract view no longer matches what consumers were promised -- a column '
  'renamed, removed, reordered or retyped. Empty is healthy. Exists because five columns '
  'silently widened integer -> numeric during the Windsor migration and broke casting code '
  'in apukuski-bi-chatbot; freshness monitoring cannot see this, since the data was fresh and '
  'only the shape changed.';

GRANT SELECT ON public.schema_contract_drift TO bi_chatbot_readonly;
GRANT SELECT ON core.schema_contract TO bi_chatbot_readonly;
