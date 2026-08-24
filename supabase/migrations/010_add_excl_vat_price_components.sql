-- Normalizes charge sub-components that previously lived only inside the raw charge JSONB,
-- plus a computed total_excl_vat_cents -- requested by apukuski-bi-chatbot while reconciling
-- against the Master P&L sheet (VAT-exclusive throughout), which had to re-derive this with
-- fragile string-parsing on charge.vatPrice. Formula validated fresh against 973 real orders
-- (last 2 months, 2026-08-24): vatPrice / (1 + vatPercentage/100) == basePrice + platformFee
-- + servicesPrice + recyclingSurcharge, 771/772 exact (excluding the decimal-cohort cohort,
-- handled separately below). See docs/REVENUE_DEFINITIONS.md.
--
-- total_excl_vat_cents is "valitetty myynti" (the full customer-paid total) excl. VAT -- NOT
-- "liikevaihto" (Apukuski's actual cut, which additionally applies commission_rate to
-- base_price_cents and excludes the partner's share). Deliberately not computing liikevaihto
-- here -- that's a business-logic computation for the reporting layer to own from these clean
-- components, not a raw synced fact.
--
-- Manual orders: always NULL. /manual-order has never returned a fee breakdown (accepted
-- permanent API limitation, see data/AWS_API_charge_object_bug_report.md) -- there is nothing
-- to compute this from, and inventing one would be worse than the existing honest gap.

ALTER TABLE orders
  ADD COLUMN IF NOT EXISTS base_price_cents INTEGER,
  ADD COLUMN IF NOT EXISTS services_price_cents INTEGER,
  ADD COLUMN IF NOT EXISTS recycling_surcharge_cents INTEGER,
  ADD COLUMN IF NOT EXISTS total_excl_vat_cents INTEGER;
