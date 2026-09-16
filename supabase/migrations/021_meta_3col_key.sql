-- Reshape the Meta staging key to fit Windsor's 3-column match limit (2026-09-16).
--
-- Windsor's "Columns to Match" accepts at most 3 columns. The intended key was
-- (date, campaign_id, publisher_platform, platform_position, device_platform) -- five --
-- which reconstructed the legacy `placement` composite ('facebook|feed|mobile_app') exactly.
-- That is not available, so the grain has to drop.
--
-- Chosen: (date, campaign_id, publisher_platform) -- exactly 3.
-- Keeping publisher_platform preserves the Facebook / Instagram / Audience Network split,
-- which is the part of placement anyone actually reports on. platform_position (feed vs
-- stories) and device_platform are dropped; per the handover, app/dashboard.py:199 only
-- aggregates placement away, so nothing consumes that detail today.
--
-- The alternative was campaign grain alone (date, campaign_id), which the handover originally
-- proposed. Rejected because publisher_platform is free to keep within the limit and is the
-- one placement dimension with real reporting value.
--
-- CONSEQUENCE, stated plainly: Windsor-era Meta rows are coarser than legacy ones. Legacy
-- `ads_meta_placement_daily` keeps its full 3-part placement history; new rows have only the
-- platform. The view must therefore pre-aggregate legacy rows to this grain rather than union
-- them directly, or a GROUP BY placement would mix two different shapes.
--
-- The table is truncated first: its 1,795 existing rows are at the old 5-column grain and
-- would violate the new 3-column key. They are staging only -- Windsor re-backfills them.

TRUNCATE TABLE windsor.ads_meta_daily;

ALTER TABLE windsor.ads_meta_daily DROP CONSTRAINT IF EXISTS ads_meta_daily_pkey;
ALTER TABLE windsor.ads_meta_daily DROP COLUMN IF EXISTS platform_position;
ALTER TABLE windsor.ads_meta_daily DROP COLUMN IF EXISTS device_platform;
ALTER TABLE windsor.ads_meta_daily
  ADD CONSTRAINT ads_meta_daily_pkey PRIMARY KEY (date, campaign_id, publisher_platform);

COMMENT ON TABLE windsor.ads_meta_daily IS
  'Windsor.ai Meta ads, one row per date x campaign x publisher_platform. Coarser than the '
  'legacy placement grain because Windsor allows at most 3 match columns -- see migration 021.';
