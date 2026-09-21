-- Customer-area segmentation, stage 2: service taxonomy and facility detection (2026-09-21).
--
-- THE QUESTION THIS ANSWERS. "Where are our customers?" needs to know which end of a job is a
-- customer. That differs by service, and getting it wrong does not fail loudly -- it quietly
-- reports a recycling depot's postcode as a customer neighbourhood. Established empirically by
-- address reuse (a household address appears once; a facility repeats):
--
--   Kierratyspalvelu   pickup 92.0% unique, delivery 15.9% unique (one address reused 62x)
--   Kuljetus           pickup 76.8%,        delivery 78.4%        -> seller -> buyer, both customers
--   Muuttopalvelu      pickup 88.5%,        delivery 85.7%        -> one household, two addresses
--   Rahti / Yrityskulj pickup 25.0%,        delivery 32.1%        -> depots, not customers
--
-- RULES AS DATA, NOT A CASE EXPRESSION. Platform orders use 8 tidy types; manual orders are
-- free text with 811 distinct values and growing. A CASE would be unreviewable and every
-- correction would be a migration. Here a correction is one INSERT at priority 0, and the
-- human review surface is one row per distinct string (774-ish) rather than one per order.
--
-- `role` IS AUTHORITATIVE. Every stop in the API's array carries role = 'pickup' | 'delivery'
-- (15,409 stops; 3 stray 'waypoint'). We use it rather than inferring from array position.
--
-- COUNT DISTINCT ADDRESSES PER ORDER. `Pelkka kantoapu` (carry help) has the SAME address on
-- both stops in 98.3% of its 175 orders -- it is work at one location, not a journey. Counting
-- both ends would double-count it. Rather than a per-service special case, customer ends are
-- counted as DISTINCT addresses, which handles carry help automatically and also fixes the 3.4%
-- of moves where the same address was typed into both stops. Note `Tuntityo` is only 6.3%
-- same-address, so it is NOT single-location -- which is why this is measured, not assumed.

CREATE TABLE IF NOT EXISTS geo.service_rule (
  rule_id                 integer PRIMARY KEY,
  priority                integer NOT NULL,
  match_kind              text    NOT NULL CHECK (match_kind IN ('exact','prefix','keyword','regex')),
  pattern                 text    NOT NULL,
  service_group           text    NOT NULL,
  origin_is_customer      boolean NOT NULL DEFAULT true,
  destination_is_customer boolean NOT NULL DEFAULT true,
  segmentation_eligible   boolean NOT NULL DEFAULT true,
  sensitivity             text    NOT NULL DEFAULT 'normal',
  rationale               text,
  effective_from          date    NOT NULL DEFAULT current_date,
  retired_at              date
);

CREATE INDEX IF NOT EXISTS idx_service_rule_priority ON geo.service_rule (priority);

COMMENT ON TABLE geo.service_rule IS
  'Order-type classification as data. Strictly first match by ascending priority, with a '
  'catch-all at 9999, so there is never a "which rule won?" ambiguity. To correct a '
  'misclassification, insert an exact-match rule at priority 0 -- no migration needed.';

-- ---------------------------------------------------------------------------
-- Seeds. Priority ordering matters: business and sensitive patterns must match BEFORE the
-- generic ones, because "Yritysmuutto" contains "muutto" and "Muutto (sosiaalityo)" contains
-- both "muutto" and "sosiaality".
-- ---------------------------------------------------------------------------
INSERT INTO geo.service_rule
  (rule_id, priority, match_kind, pattern, service_group,
   origin_is_customer, destination_is_customer, segmentation_eligible, sensitivity, rationale)
VALUES
  (1, 10, 'keyword', 'sosiaality', 'social_services', false, false, false, 'social_services',
   'Social-services moves (94 orders). Crossing a home income band with social-services '
   'involvement is an inference about an identifiable household. Counted in totals under its '
   'own group so reconciliation holds; area labels withheld.'),

  (2, 20, 'keyword', 'yritysmuutto', 'business', false, false, false, 'business',
   'Company move. Matched before the generic muutto rule, which it contains.'),
  (3, 21, 'keyword', 'yrityskulj', 'business', false, false, false, 'business',
   'Business transport. Pickup 25.0% / delivery 32.1% unique -- depots, not homes.'),
  (4, 22, 'keyword', 'rahti', 'business', false, false, false, 'business',
   'Freight. Same depot evidence as yrityskuljetus.'),

  (5, 40, 'keyword', 'kierr', 'recycling', true, false, true, 'normal',
   'Recycling: customer -> facility. Delivery end 15.9% unique with one address reused 62x. '
   'Destination excluded explicitly; frequency detection is the backstop. Pattern is the '
   'diacritic-free stem so it survives both "kierratys" and "kierrätys" spellings.'),

  (7, 60, 'keyword', 'muuttolaatiko', 'boxes', true, true, true, 'normal',
   'Moving-box delivery/collection (121 orders). One end is our own box depot, but it is left '
   'to frequency detection rather than hardcoded -- the depot address may change.'),
  (8, 61, 'keyword', 'boksi', 'boxes', true, true, true, 'normal',
   'Boksinouto / Boksitoimitus (42 orders). Same reasoning as muuttolaatikot.'),

  (9, 70, 'keyword', 'kantoapu', 'carry_help', true, true, true, 'normal',
   'Carry help. Same address on both stops in 98.3% of orders, so the distinct-address rule '
   'collapses it to one customer end without a special case.'),

  (10, 80, 'keyword', 'muutto', 'muutto', true, true, true, 'normal',
   'Moving. Origin and destination are the same household at two addresses -- the move vector.'),

  (11, 90, 'keyword', 'kuljetus', 'recommerce', true, true, true, 'normal',
   'Recommerce transport: seller -> buyer. Both ends 76-78% unique, so both are customers.'),

  (12, 100, 'exact', 'Tuntityö', 'other', true, true, true, 'normal',
   'Hourly work. Only 6.3% same-address, so it behaves as transport, not single-location work.'),

  (13, 9999, 'regex', '.*', 'unclassified', false, false, false, 'normal',
   'Catch-all. Anything landing here is unreviewed and must not be published -- it is an '
   'alertable event, not a silent bucket.')
ON CONFLICT (rule_id) DO UPDATE SET
  priority = EXCLUDED.priority, match_kind = EXCLUDED.match_kind, pattern = EXCLUDED.pattern,
  service_group = EXCLUDED.service_group, origin_is_customer = EXCLUDED.origin_is_customer,
  destination_is_customer = EXCLUDED.destination_is_customer,
  segmentation_eligible = EXCLUDED.segmentation_eligible, sensitivity = EXCLUDED.sensitivity,
  rationale = EXCLUDED.rationale;

-- ---------------------------------------------------------------------------
-- Resolver. Returns the winning rule_id for a raw order_type string.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION geo.classify_order_type(raw text)
RETURNS integer
LANGUAGE sql
STABLE
SET search_path = geo, pg_temp
AS $fn$
  SELECT r.rule_id
  FROM geo.service_rule r
  WHERE r.retired_at IS NULL
    AND CASE r.match_kind
          WHEN 'exact'   THEN lower(btrim(COALESCE(raw,''))) = lower(r.pattern)
          WHEN 'prefix'  THEN lower(btrim(COALESCE(raw,''))) LIKE lower(r.pattern) || '%'
          WHEN 'keyword' THEN lower(btrim(COALESCE(raw,''))) LIKE '%' || lower(r.pattern) || '%'
          WHEN 'regex'   THEN COALESCE(raw,'') ~* r.pattern
        END
  ORDER BY r.priority, r.rule_id
  LIMIT 1;
$fn$;

-- ---------------------------------------------------------------------------
-- One row per DISTINCT raw order_type, not per order. This is what makes 811 free-text
-- variants reviewable by a human and makes "a new unclassified string appeared" a single
-- detectable row rather than a slow drift nobody notices.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS geo.order_type_resolution (
  raw_order_type text    NOT NULL,
  is_manual      boolean NOT NULL,
  rule_id        integer REFERENCES geo.service_rule(rule_id),
  order_count    integer NOT NULL DEFAULT 0,
  first_seen     timestamptz,
  last_seen      timestamptz,
  reviewed_by    text,
  reviewed_at    timestamptz,
  resolved_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (raw_order_type, is_manual)
);

CREATE OR REPLACE FUNCTION geo.refresh_order_type_resolution()
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = geo, public, pg_temp
AS $fn$
DECLARE n integer; n_unclassified integer;
BEGIN
  INSERT INTO geo.order_type_resolution AS t
    (raw_order_type, is_manual, rule_id, order_count, first_seen, last_seen, resolved_at)
  -- classify_order_type() is applied to the GROUP BY key itself, not the bare column, so it
  -- resolves once per distinct type rather than once per order.
  SELECT COALESCE(o.order_type,''), o.is_manual,
         geo.classify_order_type(COALESCE(o.order_type,'')),
         count(*), min(o.created_at), max(o.created_at), now()
  FROM public.orders o
  WHERE o.order_state IS DISTINCT FROM 'CANCELLED'
  GROUP BY 1,2
  ON CONFLICT (raw_order_type, is_manual) DO UPDATE SET
    rule_id     = EXCLUDED.rule_id,
    order_count = EXCLUDED.order_count,
    first_seen  = LEAST(t.first_seen, EXCLUDED.first_seen),
    last_seen   = GREATEST(t.last_seen, EXCLUDED.last_seen),
    resolved_at = now();
  GET DIAGNOSTICS n = ROW_COUNT;

  SELECT count(*) INTO n_unclassified
  FROM geo.order_type_resolution WHERE rule_id = 13 AND reviewed_at IS NULL;

  RETURN jsonb_build_object('ran_at', now(), 'distinct_types', n,
                            'unclassified_unreviewed', n_unclassified);
END
$fn$;

-- ---------------------------------------------------------------------------
-- Facility detection by address frequency. A household address appears once or twice; a depot
-- appears dozens of times. This catches facilities in ANY service, including ones no
-- per-service rule anticipates, and it is what stops a single depot postcode inflating an
-- income band.
--
-- The address itself is NEVER stored -- only a salted SHA-256 of the normalised form. The salt
-- is generated here and lives in the database, never in this file or in git, so the hashes are
-- not reversible with a public rainbow table of Finnish addresses.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS geo.hash_salt (
  id         integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  salt       bytea   NOT NULL DEFAULT extensions.gen_random_bytes(32),
  created_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO geo.hash_salt (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

CREATE OR REPLACE FUNCTION geo.address_hash(addr text)
RETURNS bytea
LANGUAGE sql
STABLE
SET search_path = geo, extensions, pg_temp
AS $fn$
  SELECT extensions.digest(
           (SELECT salt FROM geo.hash_salt WHERE id = 1)
             || convert_to(lower(regexp_replace(btrim(COALESCE(addr,'')), '\s+', ' ', 'g')), 'UTF8'),
           'sha256');
$fn$;

CREATE TABLE IF NOT EXISTS geo.frequent_address (
  address_hash     bytea PRIMARY KEY,
  occurrence_count integer NOT NULL,
  distinct_services integer NOT NULL DEFAULT 0,
  first_seen       timestamptz,
  last_seen        timestamptz,
  classification   text NOT NULL DEFAULT 'facility',
  computed_at      timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE geo.frequent_address IS
  'Addresses appearing often enough to be a facility rather than a household. Stores a salted '
  'SHA-256 only -- never the address. Primary facility mechanism; the per-service rules in '
  'service_rule are the fallback.';
