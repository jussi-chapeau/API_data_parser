-- Adds discount/promo-code tracking to payments_stripe, requested by apukuski-bi-chatbot's
-- upsell_discount_used funnel stage (see docs/STATUS.md workstream E). Confirmed live
-- 2026-08-12: Stripe's checkout.session.discounts field gives a promotion_code object ID
-- (promo_...), not a human-readable code -- the actual code (e.g. "TESTAA10", "MP20") lives
-- on the resolved coupon object, reachable via expand[]=total_details.breakdown, not the
-- bare discounts field alone. 5 of 100 sampled live sessions had a real discount applied.
--
-- discount_coupon_code is the primary, directly-filterable column for the common single-
-- discount case (WHERE discount_coupon_code = 'X') -- deliberately not JSONB-only, per an
-- already-documented BI chatbot pain point (vault/BI.md: too many exploratory db_read calls
-- spent guessing JSONB paths is a real cost, not a one-off). discounts holds the full detail
-- for the rare multi-discount session.

ALTER TABLE payments_stripe
  ADD COLUMN IF NOT EXISTS discount_coupon_code TEXT,
  ADD COLUMN IF NOT EXISTS discount_percent_off NUMERIC,
  ADD COLUMN IF NOT EXISTS discount_amount_off_cents INTEGER,
  ADD COLUMN IF NOT EXISTS discount_applied_cents INTEGER,
  ADD COLUMN IF NOT EXISTS discounts JSONB;

CREATE INDEX IF NOT EXISTS idx_payments_stripe_discount_coupon_code
  ON payments_stripe (discount_coupon_code) WHERE discount_coupon_code IS NOT NULL;
