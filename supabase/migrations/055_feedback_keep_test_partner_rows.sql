-- Stop discarding 281 real customer feedback rows (2026-09-23).
--
-- Migration 049 excluded rows whose Partner is "Testi lähetys Tilitykset / Laskutukset" -- 281
-- of 1,344 -- on the reasoning that they were test harness output. Checked properly when asked
-- where the data came from, that reasoning was wrong:
--
--                              rows  with text  avg len  CSAT  cities
--   "test" rows                 281        281       83  4.86       6
--   everything else           1,063      1,062       80  4.79       6
--
-- They are indistinguishable from real feedback because they ARE real feedback. Every one has
-- customer-written text, the length distribution matches, the scores match, the cities match.
-- Only the Partner field carries a test value -- most likely a stale Airtable linked-record
-- pointing at a row in the Tilitykset table.
--
-- The mistake was scope: a junk value in ONE field was treated as grounds to discard the whole
-- record. It cost the consumer 21% of all customer feedback, silently, in the direction of
-- looking clean.
--
-- Correct behaviour: keep the row, drop only the attribution. Partner becomes NULL, which the
-- data already says on 771 other rows anyway, so "partner unknown" is an ordinary state rather
-- than a new one. Partner-level analysis stays honest and nothing is thrown away.

CREATE OR REPLACE VIEW public.customer_feedback AS
SELECT f.record_id,
       f.submitted_at,
       -- test-marked partners become "unknown attribution", NOT a dropped row
       CASE WHEN COALESCE(m.is_test, false) THEN NULL
            ELSE COALESCE(m.partner, f.partner_raw) END AS partner,
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
       'order value or revenue is not answerable. `partner` is NULL on roughly 78% of rows '
       '(never recorded, or recorded as a test value), so partner-level figures describe a '
       'minority of feedback -- always state how many rows a partner comparison rests on. '
       'Dates are Airtable Last Modified, not submission time: whole months are missing and '
       '2025-11 holds a 344-row bulk-edit spike, so monthly trends are not trustworthy.'
         ::text AS feedback_caveat
FROM core.feedback f
LEFT JOIN core.feedback_partner_map m ON m.partner_raw = f.partner_raw;

COMMENT ON VIEW public.customer_feedback IS
  'Customer feedback from Airtable Palautteet. ALL rows -- migration 055 stopped dropping the '
  '281 test-marked ones, which turned out to be real feedback with a junk Partner value. '
  'Partner is NULL where attribution is unknown. Email never synced; free text scrubbed at '
  'ingest. No order key exists on the source, so this cannot be joined to orders.';
