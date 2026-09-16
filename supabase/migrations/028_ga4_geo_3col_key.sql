-- Reshape the GA4 geo key to fit Windsor's 3-column match limit (2026-09-16).
--
-- Intended key was (date, city, region, country) -- four columns. Windsor's "Columns to Match"
-- accepts at most 3, the same limit that forced the Meta regrain in migration 021.
--
-- Chosen: (date, city, country), with `region` dropped from the request entirely.
--
-- Dropping region rather than keeping it as a non-key column is deliberate. GA4 buckets
-- low-volume geography into the literal string '(not set)', so a single (date, city, country)
-- combination can legitimately span several regions -- and any dimension that varies while
-- sitting outside the key causes rows to collide and be silently discarded. By NOT requesting
-- region, GA4 aggregates across it and returns exactly one row per (date, city, country).
-- The alternative, keeping it and hoping it never varies, is the kind of assumption that
-- fails quietly months later.
--
-- Cost: no region breakdown in the new feed. `analytics_ga_daily_geo` is currently dead
-- anyway (the Supermetrics sync died 2026-08-10), and city + country is the grain the
-- dashboard's geography reporting actually uses.
--
-- Table is empty -- its task has never existed -- so no data is lost by rekeying.

ALTER TABLE windsor.ga4_daily_geo DROP CONSTRAINT IF EXISTS ga4_daily_geo_pkey;
ALTER TABLE windsor.ga4_daily_geo DROP COLUMN IF EXISTS region;
ALTER TABLE windsor.ga4_daily_geo
  ADD CONSTRAINT ga4_daily_geo_pkey PRIMARY KEY (date, city, country);

COMMENT ON TABLE windsor.ga4_daily_geo IS
  'Windsor.ai GA4 geography, one row per date x city x country. No region: Windsor allows at '
  'most 3 match columns, and an unrequested dimension is aggregated away by GA4 rather than '
  'colliding on the key. See migration 028.';
