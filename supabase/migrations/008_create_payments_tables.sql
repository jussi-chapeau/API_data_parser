-- Payment/transaction data from Stripe and Paytrail, for reconciliation against `orders`
-- and real cost/margin reporting (currently blocked entirely -- see docs/STATUS.md
-- workstream E). One table per provider, mirroring how `orders` separates platform vs
-- manual via a flag rather than forcing a shared shape too early -- Stripe and Paytrail
-- have materially different payment lifecycles (see comments below), not just different
-- field names.
--
-- GDPR: deliberately NOT syncing cardholder PII or customer email/name/address, even
-- though both provider APIs return it. Only what aggregate financial analytics needs:
-- amounts, statuses, timestamps, method type, order-reference keys. `raw_payload` holds
-- the full API response for forward-compatibility, but callers populating it must strip
-- customer/billing_details objects before insert -- see the N8N Transform node for each
-- workflow, not just this schema comment.
--
-- Money columns use INTEGER cents, matching orders.platform_fee/service_fee -- unlike the
-- marketing ads tables (003), both providers' native amount fields are already integer
-- cents (confirmed live for Paytrail: {"amount": 1} for a real EUR 0.01 payment), so no
-- unit conversion is needed and cents is the natural fit for settlement-grade data.

CREATE TABLE IF NOT EXISTS payments_paytrail (
  transaction_id TEXT PRIMARY KEY,
  -- Raw Paytrail `reference` field. NOT reliably an orders.order_id -- confirmed live 2026-08-12
  -- that its format differs by which flow created the payment: a direct order-settlement
  -- flow sets it to the real order_id UUID, but the "schedule later" freight/offer flow
  -- (Airtable Offers -> Paytrail -> Backoffice /order/import, only AFTER payment) sets it to
  -- a short Airtable OfferID instead (e.g. "94694"), which isn't a Backoffice order_id at all.
  -- Kept verbatim here; `order_id` below is the resolved join key.
  reference TEXT,
  stamp TEXT,
  -- Resolved Backoffice order_id. Direct copy when `reference` is already a UUID; otherwise
  -- resolved via an Airtable Offers lookup (reference = OfferID -> "Order Link" field's
  -- orderId query param) done in the sync workflow, NOT re-derived by downstream readers.
  -- NULL when no order was ever created for this payment (offer never converted) or the
  -- Airtable lookup found nothing -- left null rather than guessed, same discipline as
  -- pickup_city/delivery_city in migration 007.
  order_id TEXT,
  status TEXT,
  amount_cents INTEGER,
  currency TEXT,
  provider TEXT,           -- paying bank/method, e.g. "osuuspankki"
  created_at TIMESTAMPTZ,
  paid_at TIMESTAMPTZ,
  -- NOT populated by the confirmed-working GET /payments/{id} endpoint (verified live
  -- 2026-08-12 -- no fee or refund field in that response). Left null until a real Paytrail
  -- endpoint for these is found and confirmed; do not backfill by guessing a formula.
  fee_cents INTEGER,
  refund_amount_cents INTEGER,
  filing_code TEXT,
  settlement_reference TEXT,
  raw_payload JSONB,
  synced_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_payments_paytrail_order_id ON payments_paytrail (order_id) WHERE order_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_payments_paytrail_paid_at ON payments_paytrail (paid_at);

CREATE TABLE IF NOT EXISTS payments_stripe (
  id TEXT PRIMARY KEY,             -- Stripe checkout session id (cs_...)
  payment_intent_id TEXT,
  -- Join key to orders.order_id -- Stripe checkout sessions carry caller-supplied metadata;
  -- confirm the actual key name live before relying on this (see docs/STATUS.md
  -- workstream E -- unresolved as of 2026-08-12, no live Stripe key available yet).
  order_id TEXT,
  status TEXT,
  amount_cents INTEGER,
  currency TEXT,
  payment_method_type TEXT,
  created_at TIMESTAMPTZ,
  paid_at TIMESTAMPTZ,
  fee_cents INTEGER,
  refund_amount_cents INTEGER,
  raw_payload JSONB,
  synced_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_payments_stripe_order_id ON payments_stripe (order_id) WHERE order_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_payments_stripe_paid_at ON payments_stripe (paid_at);
