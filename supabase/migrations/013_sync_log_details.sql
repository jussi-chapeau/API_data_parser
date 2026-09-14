-- Add a structured `details` column to sync_log (2026-09-03).
--
-- orders-reconcile needs to report TWO counts per run (phantoms proposed, rows repaired) so
-- that "drift = 0" is a visible fact rather than an assumption. The first cut put that
-- summary in `error_message`, which is wrong: it makes every successful run look like it
-- carries an error, and quietly breaks the obvious way anyone (a human, the BI bot, a future
-- monitor) would search for real failures -- `WHERE error_message IS NOT NULL`.
--
-- JSONB rather than text so the counts stay queryable, e.g.
--   SELECT completed_at, details->>'phantom_candidates', details->>'repaired'
--   FROM sync_log WHERE workflow = 'orders-reconcile' ORDER BY completed_at DESC;
--
-- Nullable and unused by every existing workflow, so this is additive and non-breaking.

ALTER TABLE sync_log ADD COLUMN IF NOT EXISTS details JSONB;

COMMENT ON COLUMN sync_log.details IS
  'Optional structured run summary. error_message stays reserved for actual failures.';
