-- Narrow what bi_chatbot_readonly can read from orders, and hand them housing_type (2026-09-21).
--
-- Built from apukuski-bi-chatbot's own docs/orders_grant.sql, which scanned every SQL site
-- touching `orders` including interpolated constants. Their column list is adopted almost
-- verbatim -- including the warning NOT to tidy away `is_asuntosaatio_gig`, which no compiled
-- tool names but vault/BI.md documents with a worked example, and db_read writes free-form SQL.
-- "No tool names it" is not "not needed".
--
-- THREE DEPARTURES FROM THEIR LIST, EACH FOR A REASON:
--
-- 1. `manual_data` IS REDACTED, NOT PASSED THROUGH. Measured against the live table, it holds
--    4,462 customer names, 3,613 email addresses and 4,036 phone numbers, plus a free-text
--    `additionalInfo` on 3,422 orders (45 of which contain an email, 70 a phone number).
--    They already block `manual_data->'customer'` and `->>'additionalInfo'` -- but in
--    APPLICATION CODE, a regex at app/tools.py:51. That is exactly what their own segmentation
--    spec told us was insufficient for k-anonymity:
--
--        "suppression must live in the database, not our application code"
--
--    The same principle applied consistently: `- 'customer' - 'additionalInfo'` here turns
--    their app-layer block into a database guarantee that a hand-written db_read cannot walk
--    around. They read only total_incl_vat_cents and total_incl_vat_eur from this column
--    (verified by grep across their repo), so nothing they use is lost.
--
-- 2. `review` IS DROPPED. jsonb free text (rating/comment/created) on 38 orders. Their own
--    PII regex already blocks `\breview\b`, and no code path reads it.
--
-- 3. `stops` IS DROPPED AND REPLACED BY `housing_type`. This was their ask, and it is the
--    best trade in this whole exchange: `stops` has exactly one live use on their side, the
--    housing-type regex. Serving the label instead removes street addresses, apartment
--    numbers, contact names and phone numbers from this service's reach entirely -- closing
--    GDPR #9 rather than mitigating it.
--
-- housing_type REPLICATES _HOUSING_TYPE_CASE_SQL EXACTLY, including the [oö] variants and the
-- precedence order (yksio before kaksio before ... before kerrostalo). Same inputs, same
-- answers, same ~15% coverage ceiling -- the information genuinely is not in the text for the
-- other 85%, which their tool description already states honestly and at length. This is
-- parity, not improvement: the point is to let them drop `stops` without any number moving.
--
-- A REVENUE GAP SURFACED WHILE DOING THIS, NOT CAUSED BY IT. 2,983 of 4,597 manual orders have
-- no numeric total_incl_vat_cents, and 2,777 of those carry a non-numeric `raw_charge_total`
-- instead ("119e/h" and similar -- an hourly rate, not a total). The BI repo's
-- REVENUE_CENTS_CASE_SQL does COALESCE(..., 0), so all 2,777 are already counted as EUR 0 today,
-- before this view existed. Dropping the column therefore changes no number they currently
-- produce. Parsing those strings into totals would be wrong -- docs/GOTCHAS.md records the
-- deliberate decision not to, because 119 EUR/hour is not 119 EUR -- so the honest fix is a
-- settlement source, not a regex. Flagged to the BI repo as its own piece of work.
--
-- SEQUENCING -- READ BEFORE APPLYING 044. This migration only CREATES and GRANTS. It does not
-- revoke anything. Revoking SELECT on public.orders in the same breath would break every
-- `FROM orders` query in the BI repo the moment it ran. The revoke is migration 044, to be
-- applied only after they confirm their SQL has moved to orders_reporting.

CREATE OR REPLACE VIEW public.orders_reporting AS
SELECT
  o.order_id,
  o.is_manual,
  o.created_at,
  o.organization_name,
  o.org_id,
  o.hub_id,
  o.order_state,
  o.order_type,
  o.first_schedule,
  o.schedule,
  o.delivered_at,
  o.content,
  o.charge,

  -- financial keys only; customer identity and free text removed at the database boundary.
  -- `raw_charge_total` goes too: it is a free-text field ops type into (max 492 chars, 473
  -- rows over 60, 3 containing an email address) and the BI repo does not read it -- their
  -- revenue CASE uses total_incl_vat_cents with COALESCE(...,0). See the note below about the
  -- 2,777 orders that already score zero because of it.
  (o.manual_data - 'customer' - 'additionalInfo' - 'raw_charge_total') AS manual_data,

  o.platform_fee,
  o.service_fee,
  o.commission_rate,
  o.base_price_cents,
  o.services_price_cents,
  o.recycling_surcharge_cents,
  o.total_excl_vat_cents,
  o.route_id,
  o.synced_at,
  o.origin,
  o.is_asuntosaatio_gig,
  o.pickup_city,
  o.delivery_city,

  -- byte-for-byte the logic of _HOUSING_TYPE_CASE_SQL, so the label cannot drift from what
  -- their tool produced while it still read `stops` directly
  CASE
    WHEN o.stops ~* 'yksi[oö]'    OR o.content::text ~* 'yksi[oö]'    THEN 'yksio'
    WHEN o.stops ~* 'kaksio'      OR o.content::text ~* 'kaksio'      THEN 'kaksio'
    WHEN o.stops ~* 'kolmio'      OR o.content::text ~* 'kolmio'      THEN 'kolmio'
    WHEN o.stops ~* 'neli[oö]'    OR o.content::text ~* 'neli[oö]'    THEN 'nelio'
    WHEN o.stops ~* 'omakotitalo' OR o.content::text ~* 'omakotitalo' THEN 'omakotitalo'
    WHEN o.stops ~* 'rivitalo'    OR o.content::text ~* 'rivitalo'    THEN 'rivitalo'
    WHEN o.stops ~* 'kerrostalo'  OR o.content::text ~* 'kerrostalo'  THEN 'kerrostalo_unspecified_size'
    ELSE NULL
  END AS housing_type

FROM public.orders o;

COMMENT ON VIEW public.orders_reporting IS
  'What bi_chatbot_readonly may read from orders. No stops, no review, and manual_data with '
  'customer identity and additionalInfo removed IN THE DATABASE rather than by an application '
  'regex. housing_type replicates the BI repo''s _HOUSING_TYPE_CASE_SQL exactly (~15% coverage '
  'ceiling -- the signal is absent from the text for the rest). See migration 043.';

COMMENT ON COLUMN public.orders_reporting.housing_type IS
  'Keyword-derived dwelling type. NULL for ~85% of orders because the address text carries no '
  'housing signal at all -- a missing data source, not a classifier limitation. Always report '
  'coverage alongside it; never present the classified subset as if it were all orders.';

-- ---------------------------------------------------------------------------
-- TWO THINGS THAT MUST HAPPEN BEFORE THE GRANT, BOTH LEARNED THE HARD WAY HERE.
--
-- 1. security_invoker. A Postgres view defaults to running as its OWNER, which is postgres --
--    so it would read `orders` with RLS bypassed entirely. `orders` has RLS enabled with a
--    policy, and a view that quietly steps around it is worse than no view: the protection
--    looks present and is not. With security_invoker = on the view is evaluated as whoever
--    queries it, so the policy still applies.
--
-- 2. Revoke anon/authenticated. Supabase's default privileges on `public` grant the PostgREST
--    roles rights on every new object, so simply creating this view published it on the REST
--    API. Verified immediately after creation: anon and authenticated had SELECT, INSERT,
--    UPDATE, DELETE and TRUNCATE on it. That is the same trapdoor migration 033 PART 2 cleaned
--    up for `windsor`/`core`, and it reopens for every new object in `public`.
-- ---------------------------------------------------------------------------
ALTER VIEW public.orders_reporting SET (security_invoker = on);

REVOKE ALL ON public.orders_reporting FROM anon, authenticated, PUBLIC;

GRANT SELECT ON public.orders_reporting TO bi_chatbot_readonly;
