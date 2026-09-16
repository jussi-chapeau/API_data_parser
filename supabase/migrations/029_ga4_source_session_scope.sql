-- Switch the GA4 source feed to session_source_medium (2026-09-16).
--
-- GA4 refused the request outright:
--   "the selected dimensions and metrics are incompatible ... If the request contains
--    source_medium, use session_source_medium instead"
--
-- GA4 dimensions and metrics carry a SCOPE, and not all combinations are legal. Confirmed
-- against Windsor's field catalogue:
--     source_medium          [Attribution]     <- what we asked for
--     session_source_medium  [Traffic Source]  <- session-scoped, compatible
--     sessions, engagement_rate, engaged_sessions   [Session]
-- Pairing an attribution-scoped dimension with session-scoped metrics is not a Windsor
-- limitation; GA4 itself rejects it.
--
-- The staging column must be named for Windsor's field, since Windsor writes columns by field
-- name and would otherwise ALTER a new `session_source_medium` column in beside an untouched,
-- NOT NULL `source_medium` -- failing the insert. The consumer's contract column stays
-- `source_medium`; the view aliases it back.
--
-- Table is empty (its task has never successfully run), so the rename costs nothing.

ALTER TABLE windsor.ga4_daily_source DROP CONSTRAINT IF EXISTS ga4_daily_source_pkey;
ALTER TABLE windsor.ga4_daily_source RENAME COLUMN source_medium TO session_source_medium;
ALTER TABLE windsor.ga4_daily_source
  ADD CONSTRAINT ga4_daily_source_pkey PRIMARY KEY (date, session_source_medium);

COMMENT ON TABLE windsor.ga4_daily_source IS
  'Windsor.ai GA4 traffic sources, one row per date x session_source_medium. Uses the '
  'session-scoped dimension because GA4 rejects attribution-scoped source_medium alongside '
  'session metrics. Aliased back to `source_medium` for the consumer. See migration 029.';
