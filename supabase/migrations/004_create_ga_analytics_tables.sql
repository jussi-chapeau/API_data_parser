-- Google Analytics daily data, read from the existing GA -> Google Sheets export (native
-- Google integration, not Supermetrics) — see docs/STATUS.md workstream B. Unlike the
-- ads_* tables, GA source values are clean integers/decimals already (confirmed via a live
-- Sheets API read with valueRenderOption=UNFORMATTED_VALUE); the one transform-time gotcha is
-- that date cells come back as Sheets serial numbers (epoch 1899-12-30), not date strings.

CREATE TABLE IF NOT EXISTS analytics_ga_daily_totals (
  date DATE NOT NULL,
  sessions INTEGER,
  total_users INTEGER,
  new_users INTEGER,
  views INTEGER,
  views_per_session NUMERIC(10,4),
  bounce_rate NUMERIC(10,4),
  avg_session_length_sec NUMERIC(10,2),
  conversions INTEGER,
  user_conversion_rate NUMERIC(10,4),
  synced_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (date)
);

CREATE TABLE IF NOT EXISTS analytics_ga_daily_source (
  date DATE NOT NULL,
  source_medium TEXT NOT NULL,
  sessions INTEGER,
  total_users INTEGER,
  conversions INTEGER,
  engagement_rate NUMERIC(10,4),
  engaged_sessions INTEGER,
  begin_checkout_count INTEGER,
  synced_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (date, source_medium)
);

CREATE TABLE IF NOT EXISTS analytics_ga_daily_geo (
  date DATE NOT NULL,
  city TEXT NOT NULL,
  region TEXT NOT NULL,
  country TEXT NOT NULL,
  sessions INTEGER,
  total_users INTEGER,
  new_users INTEGER,
  conversions INTEGER,
  user_conversion_rate NUMERIC(10,4),
  begin_checkout_count INTEGER,
  synced_at TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (date, city, region, country)
);
