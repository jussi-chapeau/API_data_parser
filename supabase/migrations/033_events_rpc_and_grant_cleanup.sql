-- RPC for the GA4 events feed, plus removing stray anon grants (2026-09-17).
--
-- PART 1 -- why an RPC rather than exposing a schema.
-- The GA4 events feed is PULLED by our own N8N workflow (Windsor's plan caps destination
-- tasks at 5 and all five are used), so N8N needs to write via PostgREST. PostgREST only
-- exposes `public`, and the target tables live in `windsor`/`core`.
--
-- Exposing one of those schemas would work but widens the REST surface permanently just to
-- let one workflow insert. A SECURITY DEFINER function in `public` is narrower: it accepts
-- exactly one shape of payload, writes exactly one table, and nothing else in those schemas
-- becomes reachable.
--
-- It also writes straight to core, skipping `windsor` entirely. That schema exists to
-- quarantine a vendor that deletes and rewrites rows at will; our own pull needs no such
-- quarantine, and the function is merge-only by construction.
--
-- PART 2 -- a latent exposure found while checking this.
-- `anon` and `authenticated` each hold 7 table grants in the `windsor` schema. They are
-- currently unusable because neither role has USAGE on the schema, so nothing leaks today.
-- But RLS is disabled on those tables (deliberately -- RLS with no policy returns zero rows
-- silently, which is how a five-week outage stayed invisible), so the moment anyone exposes
-- that schema or grants USAGE, staging becomes publicly readable. Revoking now removes the
-- dependency on nobody ever making that change.

-- ---------------------------------------------------------------------------
-- 1. Revoke the stray grants
-- ---------------------------------------------------------------------------
REVOKE ALL ON ALL TABLES IN SCHEMA windsor FROM anon, authenticated;
REVOKE ALL ON ALL TABLES IN SCHEMA core    FROM anon, authenticated;
REVOKE ALL ON SCHEMA windsor FROM anon, authenticated;
REVOKE ALL ON SCHEMA core    FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA windsor REVOKE ALL ON TABLES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA core    REVOKE ALL ON TABLES FROM anon, authenticated;

-- ---------------------------------------------------------------------------
-- 2. The write path
-- ---------------------------------------------------------------------------
-- Takes the whole batch as one jsonb array -- one request per run rather than one per row,
-- which also means the upsert is a single statement and cannot half-apply.
--
-- Merge-only with COALESCE, matching core.refresh_from_windsor() (migration 022): nothing is
-- ever deleted, and a NULL never overwrites an existing value. Same reasoning -- a narrowed
-- field list should degrade to "no new data", never to erased history.
CREATE OR REPLACE FUNCTION public.upsert_ga4_events(payload jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = core, public, pg_temp
AS $fn$
DECLARE n integer := 0;
BEGIN
  IF jsonb_typeof(payload) <> 'array' THEN
    RAISE EXCEPTION 'payload must be a JSON array, got %', jsonb_typeof(payload);
  END IF;

  INSERT INTO core.ga4_daily_events AS c
    (date, event_name, is_conversion_event, event_count, conversions, event_value, synced_at)
  SELECT (e->>'date')::date,
         e->>'event_name',
         e->>'is_conversion_event',
         (e->>'event_count')::numeric,
         (e->>'conversions')::numeric,
         (e->>'event_value')::numeric,
         COALESCE((e->>'synced_at')::timestamptz, now())
  FROM jsonb_array_elements(payload) e
  WHERE e->>'date' IS NOT NULL AND e->>'event_name' IS NOT NULL
  ON CONFLICT (date, event_name) DO UPDATE SET
    is_conversion_event = COALESCE(EXCLUDED.is_conversion_event, c.is_conversion_event),
    event_count         = COALESCE(EXCLUDED.event_count, c.event_count),
    conversions         = COALESCE(EXCLUDED.conversions, c.conversions),
    event_value         = COALESCE(EXCLUDED.event_value, c.event_value),
    synced_at           = COALESCE(EXCLUDED.synced_at, c.synced_at),
    last_seen_at        = now();
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN jsonb_build_object('rows_upserted', n, 'ran_at', now());
END
$fn$;

-- service_role only. Deliberately NOT granted to anon or authenticated: this is a write path
-- for one internal workflow, not a public endpoint.
REVOKE ALL ON FUNCTION public.upsert_ga4_events(jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.upsert_ga4_events(jsonb) TO service_role;

COMMENT ON FUNCTION public.upsert_ga4_events(jsonb) IS
  'Write path for the N8N-pulled GA4 events feed. Merge-only, NULL-safe, service_role only. '
  'Exists so the windsor/core schemas need not be exposed to PostgREST. See migration 033.';
