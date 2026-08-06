-- analytics_ga_daily_source's real data proved "conversions" can be fractional (e.g. 696.21)
-- -- GA4 key-event counts go through the same data-driven attribution modeling as Google
-- Ads conversions (already handled correctly there; missed applying the same caution here).
-- Widening INTEGER -> NUMERIC is safe on existing data (no precision loss for the integers
-- already written to analytics_ga_daily_totals). Widening begin_checkout_count too, same
-- underlying GA4 key-event mechanism, same fractional risk, not yet observed to fail but not
-- worth waiting for a second live failure to fix it.

ALTER TABLE analytics_ga_daily_totals ALTER COLUMN conversions TYPE NUMERIC(12,4);

ALTER TABLE analytics_ga_daily_source ALTER COLUMN conversions TYPE NUMERIC(12,4);
ALTER TABLE analytics_ga_daily_source ALTER COLUMN begin_checkout_count TYPE NUMERIC(12,4);

ALTER TABLE analytics_ga_daily_geo ALTER COLUMN conversions TYPE NUMERIC(12,4);
ALTER TABLE analytics_ga_daily_geo ALTER COLUMN begin_checkout_count TYPE NUMERIC(12,4);
