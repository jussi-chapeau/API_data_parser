-- Windsor staging table for Meta/Facebook ads (2026-09-14).
--
-- The Supermetrics Meta sync stopped producing data on 2026-09-02 while continuing to report
-- `success` nightly and re-stamping synced_at on old rows -- 12 days of false freshness
-- before `analytics_freshness` surfaced it. This table is the replacement feed.
--
-- Column names are Windsor field ids from https://connectors.windsor.ai/facebook/fields
-- (847 fields), verified against the catalogue rather than guessed. Windsor's names are the
-- contract and differ from both Meta's API and ours: `spend` not `cost_eur`, and purchases
-- come from the action-breakdown family (`actions_offsite_conversion_fb_pixel_purchase`)
-- rather than any plain `conversions` field, which Meta does not expose.
--
-- GRAIN: placement, matching the legacy `ads_meta_placement_daily` exactly.
-- The handover proposed dropping to campaign grain on the basis that nothing reports by
-- placement (`app/dashboard.py:199` only aggregates it away). That was sound, but it turns
-- out to be unnecessary: Windsor supplies publisher_platform / platform_position /
-- device_platform, which together reconstruct the legacy composite `placement` string
-- (e.g. 'facebook|feed|mobile_app'). Keeping the grain means the legacy union needs no
-- pre-aggregation and nothing is irreversibly discarded -- 'unused today' is not the same as
-- 'safe to destroy forever'.
--
-- ONE TABLE PER PLATFORM, not the single unified ads table the handover proposed. Windsor's
-- "Columns to Match" must correspond to a real unique index, and Meta needs
-- (date, campaign_id, placement...) while Google Ads needs (date, campaign_id). Two different
-- match sets cannot both satisfy one primary key. Cross-platform unification belongs in the
-- view layer, where it costs nothing -- see the planned public.ads_spend_daily.

CREATE TABLE IF NOT EXISTS windsor.ads_meta_daily (
  date                 date NOT NULL,
  account_name         text,
  campaign_id          text NOT NULL,
  campaign             text,
  -- The three placement components. NOT NULL with a default because they are key columns:
  -- a null in a key silently defeats the upsert (null <> null in a unique index) and appends
  -- duplicates forever, which is far worse than failing loudly.
  publisher_platform   text NOT NULL DEFAULT '(not set)',
  platform_position    text NOT NULL DEFAULT '(not set)',
  device_platform      text NOT NULL DEFAULT '(not set)',
  impressions          numeric,
  clicks               numeric,
  link_clicks          numeric,
  spend                numeric,
  -- Meta's "Website Purchases" and its value. The legacy table's `website_conversions` and
  -- `website_purchases` are near-duplicates (258 vs 253 over 13 months) and both map here.
  -- Its `website_conversion_value` has NEVER been populated -- 0 non-zero rows out of 18,745
  -- -- so requesting the correct value field may make Meta ROAS computable for the first
  -- time. Verify once data lands rather than assuming.
  actions_offsite_conversion_fb_pixel_purchase       numeric,
  action_values_offsite_conversion_fb_pixel_purchase numeric,
  synced_at            timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (date, campaign_id, publisher_platform, platform_position, device_platform)
);

-- RLS off: Supabase enables it automatically on create, and RLS with no policy returns ZERO
-- ROWS SILENTLY rather than erroring -- the same shape of failure that hid the five-week GA4
-- outage. Access is controlled by the grants below.
ALTER TABLE windsor.ads_meta_daily DISABLE ROW LEVEL SECURITY;

-- Owned by windsor_writer, per the lesson in migration 016: Windsor issues
-- ALTER TABLE ... ADD COLUMN for any requested field with no matching column, and that needs
-- ownership. Without it, one wrong field name in a hand-typed list takes the entire feed down
-- in a silent retry loop instead of adding one stray column.
ALTER TABLE windsor.ads_meta_daily OWNER TO windsor_writer;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bi_chatbot_readonly') THEN
    GRANT SELECT ON windsor.ads_meta_daily TO bi_chatbot_readonly;
  END IF;
END
$$;
