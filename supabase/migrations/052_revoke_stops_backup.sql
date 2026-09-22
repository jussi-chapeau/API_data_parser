-- Close a PII leak created by our own repair snapshot (2026-09-22).
--
-- scripts/repair_stops_structure.py wrote public.orders_stops_backup_20260921 before the
-- 2026-09-21 repair, deliberately in-database rather than to a file because `stops` is PII and
-- a file would be a second copy outside the trust boundary. That reasoning was right. What was
-- missed is that creating ANY table in `public` inherits Supabase's default privileges, so the
-- snapshot was immediately readable by bi_chatbot_readonly, anon and authenticated.
--
-- Measured before revoking: 12,298 rows, 11,798 carrying stops text, containing 612 phone
-- numbers, 9,738 address-like strings and 8 email addresses.
--
-- This defeated migration 043 entirely. That migration removed `stops` from the BI consumer's
-- reach and replaced it with a housing_type label, on the reasoning that the BI repo's only
-- live use of stops was a regex -- but a verbatim copy of the same column sat one table away,
-- ungated. Revoking `orders` in migration 044 would not have helped either.
--
-- Same trapdoor as migrations 033 and 043: every new object in `public` reopens it. This is now
-- the third time it has bitten, which is an argument for a standing assertion rather than
-- remembering each time.
--
-- The snapshot itself is retained for now -- the repair completed on 2026-09-21 and a rollback
-- window is reasonable -- but it is a fresh copy of exactly the data GDPR_REVIEW #1 is about,
-- and it should be dropped once the repair is trusted. Flagged there rather than dropped here,
-- because deleting data is the owner's call.

REVOKE ALL ON public.orders_stops_backup_20260921
  FROM bi_chatbot_readonly, anon, authenticated, PUBLIC;

COMMENT ON TABLE public.orders_stops_backup_20260921 IS
  'Pre-repair snapshot of orders.stops taken 2026-09-21 (migration-free, written by '
  'scripts/repair_stops_structure.py). Contains customer addresses and phone numbers. '
  'Readable by postgres/service_role only -- deliberately revoked from every BI and PostgREST '
  'role in migration 052. DROP once the repair is trusted; see GDPR_REVIEW #1.';
