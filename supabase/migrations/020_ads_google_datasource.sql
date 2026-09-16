-- Add `datasource` to the Google Ads staging table (2026-09-16).
--
-- Windsor would add this itself on first run -- it issues ALTER TABLE ADD COLUMN for any
-- requested field with no matching column, and windsor_writer owns the table so it now has
-- the privilege. But Windsor guesses types badly (the handover records bounce_rate arriving
-- as TEXT), so declare it deliberately rather than inherit a guess.
--
-- `datasource` is a CONSTANT per connector ('google_ads' here), not a dimension -- it cannot
-- multiply rows or collide on the primary key. That distinction is why it is safe to include
-- while `source` is not: source IS a dimension, and any field that varies but is absent from
-- the PK (date, campaign_id) causes rows to collide and be silently discarded.
--
-- Kept because it makes the planned cross-platform ads view self-describing: rows carry
-- their own platform rather than the view hardcoding it per branch.

ALTER TABLE windsor.ads_google_daily ADD COLUMN IF NOT EXISTS datasource text;

COMMENT ON COLUMN windsor.ads_google_daily.datasource IS
  'Windsor connector id, constant per feed (google_ads). Safe to include: constant, so it '
  'cannot collide on the (date, campaign_id) key the way a real dimension would.';
