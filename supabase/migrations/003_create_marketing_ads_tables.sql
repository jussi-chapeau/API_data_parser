-- Google Ads and Meta Ads daily performance, ingested via Supermetrics API directly
-- (HTTP Request in N8N, not Google Sheets — see docs/STATUS.md workstream B). GA stays on
-- its existing Sheets-based ingestion, not covered by this migration.
--
-- Money columns use NUMERIC EUR (not integer cents like orders.platform_fee/service_fee):
-- both APIs return decimal EUR with up to 4 decimal places natively (e.g. 17.5772), finer
-- than whole cents. Converting to integer cents would round away real source precision for
-- no benefit — this data feeds spend/ROI reporting, not settlement, so there's no reason to
-- match the orders table's cents convention here.

CREATE TABLE IF NOT EXISTS ads_google_campaign_daily (
  date DATE NOT NULL,
  campaign_id TEXT NOT NULL,
  campaign_name TEXT,
  impressions INTEGER,
  clicks INTEGER,
  cost_eur NUMERIC(14,4),
  conversions NUMERIC(14,4),
  conversion_value NUMERIC(14,4),
  synced_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (date, campaign_id)
);

CREATE TABLE IF NOT EXISTS ads_meta_placement_daily (
  date DATE NOT NULL,
  campaign_id TEXT NOT NULL,
  campaign_name TEXT,
  placement TEXT NOT NULL,
  impressions INTEGER,
  cost_eur NUMERIC(14,4),
  link_clicks INTEGER,
  website_conversions NUMERIC(14,4),
  website_conversion_value NUMERIC(14,4),
  website_purchases NUMERIC(14,4),
  synced_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (date, campaign_id, placement)
);
