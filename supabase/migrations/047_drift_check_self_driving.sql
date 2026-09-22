-- Make the schema-contract drift check derive its own scope (2026-09-22).
--
-- THE BUG. public.schema_contract_drift (migration 035) hardcoded the five views it knew about
-- in an ARRAY[...] inside the view body, while the expected columns live in
-- core.schema_contract. Registering two new views in the contract therefore produced 19 false
-- "COLUMN REMOVED" rows: the contract had them, the hardcoded list did not, and a FULL JOIN
-- with nothing on the actual side reads as deletion.
--
-- Worse than the noise is the silent direction. A view registered in the contract but missing
-- from the array would be **completely unchecked** -- the drift monitor would report clean
-- while watching nothing. That is the same failure shape as `analytics_freshness` monitoring
-- the wrong table after a rename (migrations 025 and 026, twice), and it is worth fixing
-- structurally rather than by appending two more names to a list that will go stale again.
--
-- THE FIX. The contract itself is the list. `core.schema_contract` already names every view
-- that is supposed to be checked, so the drift view now drives from
-- SELECT DISTINCT view_name FROM core.schema_contract. Registering a view is then the single
-- act that puts it under monitoring -- there is no second place to remember.

-- DROP + CREATE, not CREATE OR REPLACE: the old view returned `actual_type` as
-- information_schema.character_data and this one returns text, and CREATE OR REPLACE VIEW
-- cannot change a column's type (docs/GOTCHAS.md; migration 035 hit the same wall). Wrapped in
-- a DO block so the drop, the create and the grants are ONE statement -- otherwise the
-- Management API sends them separately and the monitoring view sits dropped, or ungranted,
-- in between.
DO $do$
BEGIN
  DROP VIEW IF EXISTS public.schema_contract_drift;

  EXECUTE $view$
CREATE VIEW public.schema_contract_drift AS
WITH watched AS (
  -- the contract IS the scope; no hardcoded list to fall out of date
  SELECT DISTINCT view_name FROM core.schema_contract
),
actual AS (
  SELECT c.table_name::text AS table_name,
         c.column_name::text AS column_name,
         c.ordinal_position::integer AS ordinal_position,
         c.data_type::text AS data_type
  FROM information_schema.columns c
  JOIN watched w ON w.view_name = c.table_name::text
  WHERE c.table_schema = 'public'
)
SELECT COALESCE(c.view_name, a.table_name) AS view_name,
       COALESCE(c.column_name, a.column_name) AS column_name,
       CASE
         WHEN a.column_name IS NULL THEN 'COLUMN REMOVED'
         WHEN c.column_name IS NULL THEN 'COLUMN ADDED'
         WHEN c.data_type <> a.data_type THEN 'TYPE CHANGED'
         ELSE 'POSITION CHANGED'
       END AS problem,
       c.data_type AS expected_type,
       a.data_type AS actual_type,
       c.ordinal AS expected_ordinal,
       a.ordinal_position AS actual_ordinal
FROM core.schema_contract c
FULL JOIN actual a
       ON a.table_name = c.view_name AND a.column_name = c.column_name
WHERE a.column_name IS NULL
   OR c.column_name IS NULL
   OR c.data_type <> a.data_type
   OR c.ordinal <> a.ordinal_position
$view$;

  GRANT SELECT ON public.schema_contract_drift TO bi_chatbot_readonly;
  REVOKE ALL ON public.schema_contract_drift FROM anon, authenticated, PUBLIC;
END
$do$;

COMMENT ON VIEW public.schema_contract_drift IS
  'Empty = every contract view matches its recorded column names, positions and types. '
  'Scope is derived from core.schema_contract, so registering a view there is the only step '
  'needed to put it under monitoring -- migration 035 kept a separate hardcoded list, which '
  'meant a registered-but-unlisted view was silently unchecked. See migration 047.';

