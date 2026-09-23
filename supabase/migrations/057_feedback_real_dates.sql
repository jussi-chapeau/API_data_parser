-- Use Airtable's createdTime as the feedback date (2026-09-23).
--
-- Migration 049 dated feedback by `Last Modified`, because that was the only date field in the
-- table's own schema, and flagged the resulting time series as untrustworthy. Asked what period
-- the feedback actually covers, the answer turned out to be available all along: the Airtable
-- REST API returns `createdTime` on every record, outside the user-defined fields.
--
-- The two disagree badly, and Last Modified is the wrong one:
--
--                    createdTime     Last Modified
--   2024-09                   4       (none)
--   2025-02 / 2025-03    ~100 ea      (none -- swallowed)
--   2025-07                 107          26
--   2025-09 / 2025-10   103 / 68       (none -- swallowed)
--   2025-11                  69         344   <- a bulk edit re-stamped four months into here
--
-- createdTime is complete (1345/1345), monotonic and plausible: a steady 60-120 responses per
-- month through 2025 tapering to 15-30 in 2026. The `Month` free-text field corroborates it --
-- 91 rows say "heinäkuu 2025" while Last Modified places only 26 there.
--
-- So the real coverage is 2024-09-12 .. 2026-09-22, about two years, not the "2025-2026" that
-- the Last Modified filter implied.
--
-- `submitted_at` is kept rather than dropped: it is still the honest answer to "when was this
-- row last touched", and keeping both makes the next bulk edit visible instead of silent.

ALTER TABLE core.feedback ADD COLUMN IF NOT EXISTS created_at timestamptz;

COMMENT ON COLUMN core.feedback.created_at IS
  'Airtable createdTime -- when the response was recorded. THE date field to use. Complete on '
  'every row and unaffected by later edits.';
COMMENT ON COLUMN core.feedback.submitted_at IS
  'Airtable Last Modified. NOT a submission date: a bulk edit in 2025-11 re-stamped four '
  'months of rows into it. Kept only so a future bulk edit is visible. Use created_at.';

CREATE OR REPLACE FUNCTION public.upsert_feedback(rows jsonb)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = core, public, pg_temp
AS $fn$
DECLARE n integer;
BEGIN
  INSERT INTO core.feedback AS f (
    record_id, created_at, submitted_at, partner_raw, city, csat, nps, found_us,
    comment_good, comment_improve, comment_staff, comment_other, comment_other_uses,
    scrub_hits, synced_at)
  SELECT x.record_id, x.created_at, x.submitted_at, x.partner_raw, x.city, x.csat, x.nps,
         x.found_us, x.comment_good, x.comment_improve, x.comment_staff, x.comment_other,
         x.comment_other_uses, COALESCE(x.scrub_hits, 0), x.synced_at
  FROM jsonb_to_recordset(rows) AS x(
    record_id text, created_at timestamptz, submitted_at timestamptz, partner_raw text,
    city text, csat integer, nps integer, found_us text, comment_good text,
    comment_improve text, comment_staff text, comment_other text, comment_other_uses text,
    scrub_hits integer, synced_at timestamptz)
  ON CONFLICT (record_id) DO UPDATE SET
    created_at         = COALESCE(EXCLUDED.created_at, f.created_at),
    submitted_at       = COALESCE(EXCLUDED.submitted_at, f.submitted_at),
    partner_raw        = COALESCE(EXCLUDED.partner_raw, f.partner_raw),
    city               = COALESCE(EXCLUDED.city, f.city),
    csat               = COALESCE(EXCLUDED.csat, f.csat),
    nps                = COALESCE(EXCLUDED.nps, f.nps),
    found_us           = COALESCE(EXCLUDED.found_us, f.found_us),
    comment_good       = COALESCE(EXCLUDED.comment_good, f.comment_good),
    comment_improve    = COALESCE(EXCLUDED.comment_improve, f.comment_improve),
    comment_staff      = COALESCE(EXCLUDED.comment_staff, f.comment_staff),
    comment_other      = COALESCE(EXCLUDED.comment_other, f.comment_other),
    comment_other_uses = COALESCE(EXCLUDED.comment_other_uses, f.comment_other_uses),
    scrub_hits         = EXCLUDED.scrub_hits,
    synced_at          = COALESCE(EXCLUDED.synced_at, f.synced_at),
    last_seen_at       = now();
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END
$fn$;

REVOKE ALL ON FUNCTION public.upsert_feedback(jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.upsert_feedback(jsonb) TO service_role;

-- DROP + CREATE, not CREATE OR REPLACE: submitted_at becomes feedback_date, and
-- CREATE OR REPLACE VIEW cannot rename a column (same wall as migration 047 hit on a type
-- change). Wrapped in a DO block so the drop, create and grants are ONE statement -- otherwise
-- the Management API sends them separately and the view sits dropped, or ungranted, between.
DO $do$
BEGIN
  DROP VIEW IF EXISTS public.customer_feedback;

  EXECUTE $view$
CREATE VIEW public.customer_feedback AS
SELECT f.record_id,
       f.created_at AS feedback_date,
       CASE WHEN COALESCE(m.is_test, false) THEN NULL
            ELSE COALESCE(m.partner, f.partner_raw) END AS partner,
       f.city, f.csat, f.nps, f.found_us,
       f.comment_good, f.comment_improve, f.comment_staff, f.comment_other, f.comment_other_uses,
       'Response bias is severe: CSAT is 5 for 88% of responses and NPS is 10 for 86%. Only 57 '
       'of 1,345 sit below CSAT 4, so a mean of ~4.8 measures who chose to answer, not how the '
       'service went -- report the distribution, never the mean alone. There is NO order key: '
       'this cannot be joined to orders, so satisfaction by service type, order value or '
       'revenue is not answerable from here (use public.customer_review for that). `partner` '
       'is NULL on ~78% of rows, so partner comparisons describe a minority -- always say how '
       'many rows one rests on. feedback_date is Airtable createdTime and is reliable; '
       'coverage is 2024-09 to 2026-09.'::text AS feedback_caveat
FROM core.feedback f
LEFT JOIN core.feedback_partner_map m ON m.partner_raw = f.partner_raw;
$view$;

  GRANT SELECT ON public.customer_feedback TO bi_chatbot_readonly;
  REVOKE ALL ON public.customer_feedback FROM anon, authenticated, PUBLIC;
END
$do$;

DELETE FROM core.schema_contract WHERE view_name='customer_feedback';
INSERT INTO core.schema_contract (view_name, column_name, ordinal, data_type)
SELECT c.table_name, c.column_name, c.ordinal_position, c.data_type
FROM information_schema.columns c
WHERE c.table_schema='public' AND c.table_name='customer_feedback'
ON CONFLICT DO NOTHING;
