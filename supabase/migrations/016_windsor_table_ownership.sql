-- Give windsor_writer ownership of its own staging tables (2026-09-14).
--
-- Windsor does more DDL than the handover documented. It was known that it issues an
-- unconditional CREATE TABLE IF NOT EXISTS before every insert. It ALSO issues
-- ALTER TABLE ... ADD COLUMN for any requested field that has no matching column:
--
--   (psycopg2.errors.InsufficientPrivilege) must be owner of table ga4_daily_totals
--   [SQL: ALTER TABLE windsor.ga4_daily_totals ADD COLUMN conversions_purchase FLOAT]
--
-- ALTER requires ownership, not just INSERT/UPDATE. With the tables owned by postgres, ANY
-- mismatch between a task's field list and the table's columns takes the whole feed down --
-- not one column, the entire upload, retrying every 30 minutes indefinitely. That is the
-- failure mode this pipeline keeps producing: broken, retrying, and invisible unless someone
-- happens to open the vendor's UI.
--
-- Trade-off, made deliberately:
--   * The handover's rule "create tables yourself; never let Windsor create them" stands, and
--     is unaffected. These tables already exist with our column types and primary keys, and
--     ALTER ... ADD COLUMN cannot change an existing column's type or drop a key. Windsor's
--     bad type guesses (bounce_rate TEXT, everything else FLOAT) only ever applied to tables
--     it CREATEs from nothing, which it will not be doing here.
--   * What we accept: a genuinely new field arrives as a Windsor-typed column (FLOAT) rather
--     than failing. Mild schema drift, correctable in a later migration.
--   * What we gain: a field-list typo degrades to one oddly-typed column instead of a total
--     feed outage. With four more tasks to configure by hand, that is the right way round.
--
-- Ownership does not widen read access: bi_chatbot_readonly keeps its explicit SELECT grants,
-- and windsor_writer still cannot see anything in `public`.

ALTER TABLE windsor.ga4_daily_totals  OWNER TO windsor_writer;
ALTER TABLE windsor.ga4_daily_source  OWNER TO windsor_writer;
ALTER TABLE windsor.ga4_daily_geo     OWNER TO windsor_writer;

-- Re-assert the consumer's read access explicitly. Grants survive an ownership change, but
-- being explicit here means this migration alone is enough to reason about who can read what.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bi_chatbot_readonly') THEN
    GRANT SELECT ON windsor.ga4_daily_totals, windsor.ga4_daily_source, windsor.ga4_daily_geo
      TO bi_chatbot_readonly;
  END IF;
END
$$;
