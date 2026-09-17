-- GA4 conversion-type breakdown (2026-09-17).
--
-- Requested after the BI bot could only report a single total `conversions` figure with no
-- split by event type. That total is close to meaningless on its own -- 305,559 key events
-- against 37,224 sessions over six months, ~8.2 per session, more than page views -- because
-- nearly every event in the property is flagged as a key event.
--
-- THIS TABLE IS THE DIAGNOSTIC THAT MAKES THAT FIXABLE. `is_conversion_event` per event name
-- shows exactly which events are inflating the number, so whoever owns the GA4 config can
-- unflag the ones that are not really conversions. Until now the only visible symptom was an
-- implausible aggregate with no way to see inside it.
--
-- Scope compatibility, checked against Windsor's catalogue rather than assumed: event_name,
-- is_conversion_event, conversions, event_count and event_value are ALL [Event]-scoped, so
-- they combine legally. This avoids the failure that forced session_source_medium in
-- migration 029, where an Attribution-scoped dimension was paired with Session metrics and
-- GA4 rejected the request outright.
--
-- Key is (date, event_name) -- two columns, comfortably inside Windsor's 3-column limit that
-- forced grain reductions on both geo (028) and Meta (021).
--
-- is_conversion_event is a dimension but constant per event name (an event either is or is
-- not a key event), so it cannot multiply rows against this key.

CREATE TABLE IF NOT EXISTS windsor.ga4_daily_events (
  date                date NOT NULL,
  event_name          text NOT NULL,
  is_conversion_event text,
  event_count         numeric,
  conversions         numeric,
  event_value         numeric,
  synced_at           timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (date, event_name)
);

-- RLS with no policy returns zero rows SILENTLY rather than erroring -- the failure shape
-- that hid the five-week GA4 outage. Off here; access via grants.
ALTER TABLE windsor.ga4_daily_events DISABLE ROW LEVEL SECURITY;

-- Owned by windsor_writer per migration 016: Windsor issues ALTER TABLE ADD COLUMN for any
-- unmatched field, which needs ownership, or one typo takes the whole feed down.
ALTER TABLE windsor.ga4_daily_events OWNER TO windsor_writer;

COMMENT ON TABLE windsor.ga4_daily_events IS
  'Windsor.ai GA4 events, one row per date x event_name. Purpose: break down the otherwise '
  'uninterpretable total `conversions` figure and show which events are flagged as key '
  'events. See migration 032.';

-- Durable copy, same contract as migration 022: merge-only, NULLs never overwrite values,
-- nothing is ever deleted by a sync.
CREATE TABLE IF NOT EXISTS core.ga4_daily_events (
  date                date NOT NULL,
  event_name          text NOT NULL,
  is_conversion_event text,
  event_count         numeric,
  conversions         numeric,
  event_value         numeric,
  synced_at           timestamptz,
  first_seen_at       timestamptz NOT NULL DEFAULT now(),
  last_seen_at        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (date, event_name)
);

CREATE INDEX IF NOT EXISTS idx_core_events_date ON core.ga4_daily_events (date);
CREATE INDEX IF NOT EXISTS idx_core_events_name ON core.ga4_daily_events (event_name);

CREATE OR REPLACE FUNCTION core.refresh_windsor_events()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
AS $fn$
DECLARE n integer := 0;
BEGIN
  INSERT INTO core.ga4_daily_events AS c
    (date, event_name, is_conversion_event, event_count, conversions, event_value, synced_at)
  SELECT date, event_name, is_conversion_event, event_count, conversions, event_value, synced_at
  FROM windsor.ga4_daily_events
  WHERE event_name IS NOT NULL
  ON CONFLICT (date, event_name) DO UPDATE SET
    is_conversion_event = COALESCE(EXCLUDED.is_conversion_event, c.is_conversion_event),
    event_count         = COALESCE(EXCLUDED.event_count, c.event_count),
    conversions         = COALESCE(EXCLUDED.conversions, c.conversions),
    event_value         = COALESCE(EXCLUDED.event_value, c.event_value),
    synced_at           = COALESCE(EXCLUDED.synced_at, c.synced_at),
    last_seen_at        = now();
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN jsonb_build_object('ran_at', now(), 'event_rows', n);
END
$fn$;

SELECT cron.unschedule('core-refresh-events')
  WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'core-refresh-events');
SELECT cron.schedule('core-refresh-events', '*/30 * * * *',
                     $cron$SELECT core.refresh_windsor_events()$cron$);

GRANT SELECT ON windsor.ga4_daily_events, core.ga4_daily_events TO bi_chatbot_readonly;
