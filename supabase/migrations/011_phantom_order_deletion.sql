-- Phantom-order deletion pipeline (2026-09-02).
--
-- Problem: the orders sync is upsert-only and has no delete path at all. When Backoffice
-- deletes an order, the row stays in Supabase forever -- still counted in BI reports, still
-- holding customer street addresses in `stops`. August 2026 overstated by 34 gigs /
-- EUR 16,520 for exactly this reason (NB-Palvelut hub ran a route import 4x on 2026-08-14
-- with no idempotency key; Backoffice later deleted three of those batches, we kept them).
-- Also GDPR: retaining personal data the controller has already deleted, Art. 5(1)(e)
-- storage limitation -- see apukuski-bi-chatbot/docs/GDPR_REVIEW.md issue #8.
--
-- Design: detection proposes, a human approves, only then does anything get deleted.
-- Deletion is HARD delete (explicit decision -- not soft-delete), but ONLY of rows already
-- reviewed and marked 'approved'. The reconcile job never deletes from a freshly computed
-- set, because a stalled sync is indistinguishable from mass upstream deletion at the point
-- of computation (precedent: the 2026-08 Supabase key rotation silently killed 8 workflows
-- for two days -- a naive rule would have proposed deleting everything).
--
-- Roles: the BI service reads candidates and writes approvals; it never *writes* to
-- `orders`. That separation is deliberate -- bi_chatbot_readonly is read-only by design and
-- is NOT gaining DELETE on orders. (It does keep its pre-existing SELECT on orders, which
-- is what makes it a BI role in the first place -- see the note at the bottom of this file.)

-- ---------------------------------------------------------------------------
-- 1. Candidates: what the reconcile job proposes for deletion
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS orders_delete_candidates (
  order_id     TEXT PRIMARY KEY,
  created_at   TIMESTAMPTZ,               -- the order's own created_at, for review context
  detected_at  TIMESTAMPTZ DEFAULT now(),
  -- Full evidence for the human reviewing this: which day was checked, what the live API
  -- returned vs. what Supabase held, the order's org/hub/value. Deliberately verbose --
  -- the reviewer should not have to go re-query anything to make the call.
  evidence     JSONB,
  -- pending -> approved (by a human, via the BI bot) -> deleted | rejected
  status       TEXT NOT NULL DEFAULT 'pending',
  CONSTRAINT orders_delete_candidates_status_chk
    CHECK (status IN ('pending', 'approved', 'rejected', 'deleted'))
);

CREATE INDEX IF NOT EXISTS idx_odc_status ON orders_delete_candidates (status);
CREATE INDEX IF NOT EXISTS idx_odc_detected_at ON orders_delete_candidates (detected_at);

-- ---------------------------------------------------------------------------
-- 2. Approvals: the human decision, written by the BI bot, kept as an audit trail
-- ---------------------------------------------------------------------------
-- Separate table rather than just flipping candidates.status directly, so the approval
-- record survives the candidate row being deleted/cleaned up, and so the BI role can be
-- granted INSERT here without any write access to the candidates table itself.
CREATE TABLE IF NOT EXISTS orders_delete_approvals (
  id            BIGSERIAL PRIMARY KEY,
  order_id      TEXT NOT NULL,
  approved_by   TEXT NOT NULL,            -- Slack user id/handle of the authorizing human
  approved_at   TIMESTAMPTZ DEFAULT now(),
  decision      TEXT NOT NULL DEFAULT 'approved',
  note          TEXT,
  CONSTRAINT orders_delete_approvals_decision_chk
    CHECK (decision IN ('approved', 'rejected'))
);

CREATE INDEX IF NOT EXISTS idx_oda_order_id ON orders_delete_approvals (order_id);

-- ---------------------------------------------------------------------------
-- 3. Independent hardening: indexes the reconcile query needs
-- ---------------------------------------------------------------------------
-- Neither existed before today. The reconcile job filters `orders` by created_at day and
-- cross-checks synced_at; without these it's a seq scan over ~10k+ rows per day checked,
-- 45 times per run.
CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders (created_at);
CREATE INDEX IF NOT EXISTS idx_orders_synced_at ON orders (synced_at);

-- ---------------------------------------------------------------------------
-- 4. RLS + grants for the BI service roles
-- ---------------------------------------------------------------------------
-- Guarded on role existence: this migration must not fail on an environment where the BI
-- roles haven't been provisioned. Role names per apukuski-bi-chatbot's own setup
-- (bi_chatbot_readonly is the DATABASE_URL role; bi_chatbot_tracker is its write-scoped
-- counterpart). VERIFY these names before relying on the policies -- see the "questions"
-- note in docs/STATUS.md workstream G.
ALTER TABLE orders_delete_candidates ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders_delete_approvals  ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bi_chatbot_readonly') THEN
    GRANT USAGE ON SCHEMA public TO bi_chatbot_readonly;
    GRANT SELECT ON orders_delete_candidates TO bi_chatbot_readonly;

    DROP POLICY IF EXISTS odc_select_bi_readonly ON orders_delete_candidates;
    CREATE POLICY odc_select_bi_readonly
      ON orders_delete_candidates FOR SELECT TO bi_chatbot_readonly USING (true);
  END IF;

  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bi_chatbot_tracker') THEN
    GRANT USAGE ON SCHEMA public TO bi_chatbot_tracker;
    GRANT SELECT, INSERT ON orders_delete_approvals TO bi_chatbot_tracker;
    GRANT USAGE, SELECT ON SEQUENCE orders_delete_approvals_id_seq TO bi_chatbot_tracker;

    DROP POLICY IF EXISTS oda_select_bi_tracker ON orders_delete_approvals;
    CREATE POLICY oda_select_bi_tracker
      ON orders_delete_approvals FOR SELECT TO bi_chatbot_tracker USING (true);

    DROP POLICY IF EXISTS oda_insert_bi_tracker ON orders_delete_approvals;
    CREATE POLICY oda_insert_bi_tracker
      ON orders_delete_approvals FOR INSERT TO bi_chatbot_tracker WITH CHECK (true);
  END IF;
END
$$;

-- Explicitly NOT granted anywhere in this migration: any privilege on `orders` for either
-- BI role. Note what that does and does not mean, verified live 2026-09-02:
-- `bi_chatbot_readonly` DOES already hold SELECT on `orders` (pre-existing, and necessary --
-- it is how the BI chatbot reads orders for reporting at all). What neither BI role holds is
-- any WRITE privilege on `orders`: DELETE/UPDATE/INSERT/TRUNCATE all confirmed absent. That
-- is the property this design depends on -- the BI service proposes and approves, and only
-- the sync pipeline's service role can actually delete.
