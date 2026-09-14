-- Resolve the append-only approvals log to ONE current decision per order (2026-09-02).
--
-- `orders_delete_approvals` is append-only for the BI bot by design (SELECT + INSERT, no
-- UPDATE) -- it is an audit trail behind an irreversible destructive action, and an UPDATE
-- would erase the fact that an earlier decision ever existed. The consequence is that a
-- decision can never be edited, only contradicted by a later row. Nothing previously defined
-- which row wins, so `delete_approved_orders.py` treated "an approval row exists" as
-- authority and silently ignored revocations:
--
--   Mon  insert (X, 'approved')       -- Jussi approves
--   Tue  insert (X, 'rejected')       -- revokes; cannot UPDATE Monday's row
--   Wed  04:30 delete script sees Monday's row, deletes X. Revocation ignored.
--
-- That pair is produced by ORDINARY use, not misuse: recording an approval does not remove
-- the candidate from the queue (status stays 'pending' until the delete script flips it), so
-- between approval and the next 04:30 run the order is still listed as pending to the BI bot
-- and its commit path can legitimately record a 'rejected' for it.
--
-- This view defines the winner once, in SQL, next to the data -- rather than leaving each
-- reader to re-implement the ordering and get it subtly wrong.
--
-- `id DESC` is LOAD-BEARING, not defensive padding: approved_at defaults to now(), which is
-- transaction time, so every row inserted in one transaction shares an identical timestamp.
-- The BI bot commits batches with executemany, so identical timestamps are the normal case.
-- Without the tiebreaker the winner would be arbitrary.
--
-- No UPDATE grant is added anywhere. The table stays append-only; only the read changes.

CREATE OR REPLACE VIEW orders_delete_approvals_current AS
SELECT DISTINCT ON (order_id)
  order_id,
  decision,
  approved_by,
  approved_at,
  id AS approval_id,
  note
FROM orders_delete_approvals
ORDER BY order_id, approved_at DESC, id DESC;

COMMENT ON VIEW orders_delete_approvals_current IS
  'Current decision per order_id from the append-only orders_delete_approvals log. '
  'Winner = latest approved_at, tiebroken by highest id (approved_at is transaction time, '
  'so batch inserts share a timestamp). Read this, never the raw table, when deciding '
  'whether an order may be deleted. See migration 012.';

-- Same read access as the underlying table: the BI bot may read the resolved decision so it
-- can show current state without re-implementing the resolution rule.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bi_chatbot_tracker') THEN
    GRANT SELECT ON orders_delete_approvals_current TO bi_chatbot_tracker;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bi_chatbot_readonly') THEN
    GRANT SELECT ON orders_delete_approvals_current TO bi_chatbot_readonly;
  END IF;
END
$$;
