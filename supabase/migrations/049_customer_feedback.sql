-- Customer feedback from Airtable `Palautteet` (2026-09-22).
--
-- SOURCE. Airtable base appGkn53DdtjFGmWV, table tblPTaUOxvIPLUu83 ("Palautteet"), 1,344
-- records. Note the neighbouring table "Feedback" (tblWGxD5qyFskuU9M) is EMPTY -- it has an
-- OrderID field and looks like the intended schema, but no rows were ever written to it. All
-- real feedback is in Palautteet, which has no order key at all.
--
-- CONSEQUENCE, STATED UP FRONT: feedback CANNOT be joined to `orders`. There is no order id,
-- no customer id, nothing to key on. It is analysable by date, partner and city only -- never
-- by service type, order value or revenue. Any request for "satisfaction by service" cannot be
-- answered from this data and should not be approximated.
--
-- WHAT IS AND IS NOT SYNCED
--
--   Dropped entirely: `Sähköposti` -- a real email address on 1,342 of 1,344 records, and by
--   far the largest direct identifier in this table. Nothing downstream needs it; the Airtable
--   record id is a perfectly good primary key. This follows the payments_stripe/payments_paytrail
--   allowlist discipline: an identifier that no consumer requires does not enter Supabase.
--
--   Synced, scrubbed: the five free-text fields, including `Mitä mieltä olit työntekijöistä?`
--   (opinions of staff). That field was measured before the decision rather than assumed:
--   across 1,339 filled comments it contains 0 emails, 0 phone numbers, 0 street addresses,
--   and exactly TWO personal names. 45 distinct capitalised tokens, 42 of them appearing once,
--   almost all ordinary Finnish words, place names or company names. The scrubber handles the
--   residue. Retained because it is where service-quality signal actually lives; the residual
--   risk is indirect (with few partners per city a specific complaint could still point at a
--   person), which is why no worker identifier is carried alongside it.
--
-- TEST ROWS AND PARTNER NAMES. `Partner` is filled on only 42.6% of rows and is polluted: the
-- single most common value is "Testi lähetys Tilitykset / Laskutukset" at 281 records -- about
-- half of all filled partner values. There are also spelling variants of the same company
-- (Vilhi / Vilhi Oy, ELD / Eld Muutot Oy, Saarela Palvelut / Saarela Palvelut Oy). Both are
-- handled by a lookup table rather than a CASE, so a correction is an INSERT and not a
-- migration -- the same reasoning as geo.service_rule.
--
-- `Month` is deliberately not used: 21.8% filled and inconsistent ("elokuu 2025" vs a bare
-- "joulukuu" with no year). `Last Modified` is 100% populated and is the date of record.
--
-- RESPONSE BIAS IS THE BIGGEST ANALYTICAL TRAP HERE, so it ships as a column. CSAT is 5 for
-- 1,185 of 1,344 responses (88%) and NPS is 10 for 1,158 (86%). Only 57 responses sit below
-- CSAT 4. A mean score of 4.8 measures who chose to answer, not how the service went, and any
-- "what is going wrong" analysis runs off those 57 rows, not off 1,344.

CREATE TABLE IF NOT EXISTS core.feedback_partner_map (
  partner_raw text PRIMARY KEY,
  partner     text,                       -- canonical name; NULL when the row is a test
  is_test     boolean NOT NULL DEFAULT false,
  note        text
);

INSERT INTO core.feedback_partner_map (partner_raw, partner, is_test, note) VALUES
  ('Testi lähetys Tilitykset / Laskutukset', NULL, true,
   'Test harness output, 281 records -- about half of all filled Partner values.'),
  ('ELD',                   'ELD',              false, NULL),
  ('Eld Muutot Oy',         'ELD',              false, 'Spelling variant.'),
  ('Vilhi',                 'Vilhi',            false, NULL),
  ('Vilhi Oy',              'Vilhi',            false, 'Spelling variant.'),
  ('Saarela Palvelut',      'Saarela Palvelut', false, NULL),
  ('Saarela Palvelut Oy',   'Saarela Palvelut', false, 'Spelling variant.')
ON CONFLICT (partner_raw) DO UPDATE SET
  partner = EXCLUDED.partner, is_test = EXCLUDED.is_test, note = EXCLUDED.note;

COMMENT ON TABLE core.feedback_partner_map IS
  'Canonical partner names and test-row markers for Airtable feedback. Unmapped values pass '
  'through unchanged and are treated as real. Add a row to correct a name -- no migration.';

CREATE TABLE IF NOT EXISTS core.feedback (
  record_id          text PRIMARY KEY,     -- Airtable record id; the only stable key available
  submitted_at       timestamptz,          -- Last Modified (Month is 21.8% filled and unreliable)
  partner_raw        text,
  city               text,                 -- Paikkakunta
  csat               integer,              -- 1-5
  nps                integer,              -- 1-10
  found_us           text,                 -- Mistä löysit meidät? -- self-reported attribution

  comment_good       text,                 -- Mitä hyvää palvelussa oli?
  comment_improve    text,                 -- Mitä kehitettävää palvelussa oli?
  comment_staff      text,                 -- Mitä mieltä olit työntekijöistä?
  comment_other      text,                 -- Muita huomioita?
  comment_other_uses text,                 -- Missä muissa tilanteissa voisit käyttää...

  scrub_hits         integer NOT NULL DEFAULT 0,  -- redactions applied across all text fields
  synced_at          timestamptz,
  first_seen_at      timestamptz NOT NULL DEFAULT now(),
  last_seen_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_feedback_submitted ON core.feedback (submitted_at);

COMMENT ON COLUMN core.feedback.scrub_hits IS
  'Number of redactions the ingest scrubber applied to this row. Non-zero is the signal to '
  'watch: it should stay near zero, and a rise means the form is collecting something new.';

-- ---------------------------------------------------------------------------
-- Write path. PostgREST only exposes `public`, so writing to core.feedback over the REST API
-- returns 406 (docs/GOTCHAS.md). Rather than expose the `core` schema -- which would publish
-- every durable table on the REST API permanently -- the sync calls a SECURITY DEFINER RPC,
-- exactly the pattern migration 033 PART 1 used for public.upsert_ga4_events.
--
-- The REVOKE below is not decoration. A SECURITY DEFINER function that writes is precisely the
-- thing that must not be reachable with the anon key, and creating any function in `public`
-- picks up Supabase's default EXECUTE grant to anon and authenticated.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.upsert_feedback(rows jsonb)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = core, public, pg_temp
AS $fn$
DECLARE n integer;
BEGIN
  INSERT INTO core.feedback AS f (
    record_id, submitted_at, partner_raw, city, csat, nps, found_us,
    comment_good, comment_improve, comment_staff, comment_other, comment_other_uses,
    scrub_hits, synced_at)
  SELECT x.record_id, x.submitted_at, x.partner_raw, x.city, x.csat, x.nps, x.found_us,
         x.comment_good, x.comment_improve, x.comment_staff, x.comment_other,
         x.comment_other_uses, COALESCE(x.scrub_hits, 0), x.synced_at
  FROM jsonb_to_recordset(rows) AS x(
    record_id text, submitted_at timestamptz, partner_raw text, city text,
    csat integer, nps integer, found_us text, comment_good text, comment_improve text,
    comment_staff text, comment_other text, comment_other_uses text,
    scrub_hits integer, synced_at timestamptz)
  ON CONFLICT (record_id) DO UPDATE SET
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

-- ---------------------------------------------------------------------------
-- Contract view. Test rows are excluded here, not in the base table, so the raw record is
-- still available for a human to audit.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW public.customer_feedback AS
SELECT f.record_id,
       f.submitted_at,
       COALESCE(m.partner, f.partner_raw) AS partner,
       f.city,
       f.csat,
       f.nps,
       f.found_us,
       f.comment_good,
       f.comment_improve,
       f.comment_staff,
       f.comment_other,
       f.comment_other_uses,
       'Response bias is severe: CSAT is 5 for 88% of responses and NPS is 10 for 86%. Only 57 '
       'of 1,344 responses sit below CSAT 4, so a mean of ~4.8 measures who chose to answer, '
       'not how the service went -- report the distribution, never the mean alone. There is NO '
       'order key on this data: it cannot be joined to orders, so satisfaction by service type, '
       'order value or revenue is not answerable. Partner is filled on only 42.6% of rows.'
         ::text AS feedback_caveat
FROM core.feedback f
LEFT JOIN core.feedback_partner_map m ON m.partner_raw = f.partner_raw
WHERE COALESCE(m.is_test, false) = false;

COMMENT ON VIEW public.customer_feedback IS
  'Customer feedback from Airtable Palautteet, test rows excluded and partner names '
  'normalised. Email is never synced; free text is scrubbed at ingest. No order key exists on '
  'the source, so this cannot be joined to orders. See migration 049.';

GRANT SELECT ON public.customer_feedback TO bi_chatbot_readonly;
REVOKE ALL ON public.customer_feedback FROM anon, authenticated, PUBLIC;

INSERT INTO core.schema_contract (view_name, column_name, ordinal, data_type)
SELECT c.table_name, c.column_name, c.ordinal_position, c.data_type
FROM information_schema.columns c
WHERE c.table_schema = 'public' AND c.table_name = 'customer_feedback'
ON CONFLICT DO NOTHING;
