-- Fix the routes unit trap and lock the table down (2026-09-23).
--
-- Prompted by the backend team's updated /route specification. Three things measured against
-- production while adopting it, all of which needed fixing here rather than there.
--
-- 1. THE UNIT TRAP, LIVE RIGHT NOW. `internalCost` is EUROS; `totalSales` is CENTS. Proven two
--    ways: the values carry decimals (3985.97, 786.3, 359.25) so they cannot be integer cents,
--    and read as euros the cost is 78% of sales across 25 routes, which is a plausible partner
--    cost, while read as cents it is 0.8%, which is not.
--
--    `public.routes.internal_cost` is an `integer` column whose name states no unit, sitting
--    next to `total_sales` in cents. `total_sales - internal_cost` is therefore wrong by 100x,
--    and the integer type silently rounds away the decimals on top. Nothing consumed it yet,
--    which is the only reason this is a fix and not an incident.
--
--    The backend's rename to `internalCostCents` fixes it at source but has NOT shipped -- the
--    live response still sends `internalCost`. So the sync converts, and this schema stores
--    cents under a name that says cents.
--
-- 2. THE SPEC'S OWN PREMISE IS WRONG. It states "the Supabase routes table holds 0 rows" and
--    uses that to argue dropping `internalCost` outright is safe. The table holds 25 rows
--    (2026-08-05..2026-09-21) written by the N8N Routes Sync workflow. Still cheap to rebuild,
--    but the premise should be corrected before it is reused for a bigger table.
--
-- 3. anon AND authenticated HELD FULL DML -- INSERT, UPDATE, DELETE, TRUNCATE, SELECT -- on
--    this table, from Supabase's `public` default privileges. Fourth occurrence of that
--    trapdoor in this repo (033 windsor/core, 043 the reporting view, 052 the stops snapshot,
--    now here). Routes are not personal data, but write access from the anon key is not
--    something to leave lying around.
--
-- OLD COLUMNS ARE KEPT, NOT DROPPED. The N8N Routes Sync workflow still writes `internal_cost`
-- and `total_sales`, and breaking it from this side would take routes down with no warning.
-- They are commented as deprecated and the new columns are authoritative. PostgREST's
-- merge-duplicates upsert only touches columns present in its payload, so the two writers do
-- not clobber each other.

ALTER TABLE public.routes
  ADD COLUMN IF NOT EXISTS internal_cost_cents bigint,
  ADD COLUMN IF NOT EXISTS total_sales_cents   bigint,
  ADD COLUMN IF NOT EXISTS order_count         integer,
  ADD COLUMN IF NOT EXISTS warehouse_count     integer,
  ADD COLUMN IF NOT EXISTS contract_version    text;

COMMENT ON COLUMN public.routes.internal_cost IS
  'DEPRECATED, UNIT-AMBIGUOUS: EUROS as an integer, rounded from a decimal the API sends. '
  'Written by the N8N Routes Sync workflow. Use internal_cost_cents.';
COMMENT ON COLUMN public.routes.total_sales IS
  'DEPRECATED: cents as double precision. Use total_sales_cents.';
COMMENT ON COLUMN public.routes.internal_cost_cents IS
  'Per-route internal cost in CENTS. Converted from the API''s euro `internalCost` until the '
  'backend ships `internalCostCents`. VAT STATUS UNVERIFIED -- staff type it free-hand from a '
  'partner invoice, so do NOT compute a margin against total_sales_cents, which is VAT-'
  'inclusive. Per-route only: no per-order allocation rule exists and this repo will not '
  'invent one.';
COMMENT ON COLUMN public.routes.contract_version IS
  'Which /route response shape produced this row: `internalCost_eur` (pre-rename, current) or '
  '`internalCostCents` (post-rename). Lets the cutover be observed rather than assumed.';

REVOKE ALL ON public.routes FROM anon, authenticated, PUBLIC;

-- ---------------------------------------------------------------------------
-- BI contract. Cents throughout, and the VAT warning travels as a column because the consumer
-- is an LLM whose column descriptions come from a hardcoded dict in its own repo -- the same
-- reasoning as migrations 045, 048 and 051.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW public.route_economics AS
SELECT r.route_id,
       r.date,
       r.hub_id,
       r.partner,
       r.total_sales_cents,
       r.internal_cost_cents,
       r.order_count,
       r.warehouse_count,
       r.synced_at,
       'Do NOT subtract internal_cost_cents from total_sales_cents to get margin. total_sales '
       'is VAT-INCLUSIVE customer revenue; internal cost is a free-text figure staff copy from '
       'a partner invoice and its VAT status is unverified by the backend. The difference is '
       'therefore not a margin and may be out by the VAT rate. Cost is per ROUTE, never per '
       'order -- no allocation rule exists. Coverage is thin: routes only exist from 2026-08.'
         ::text AS route_cost_caveat
FROM public.routes r;

COMMENT ON VIEW public.route_economics IS
  'Route-level economics in cents. See docs/api/BACKOFFICE_ROUTE_ENDPOINT.md. Per-order '
  'revenue (include=orders) is not yet available from the Backoffice API.';

GRANT SELECT ON public.route_economics TO bi_chatbot_readonly;
REVOKE ALL ON public.route_economics FROM anon, authenticated, PUBLIC;

INSERT INTO core.schema_contract (view_name, column_name, ordinal, data_type)
SELECT c.table_name, c.column_name, c.ordinal_position, c.data_type
FROM information_schema.columns c
WHERE c.table_schema='public' AND c.table_name='route_economics'
ON CONFLICT DO NOTHING;
