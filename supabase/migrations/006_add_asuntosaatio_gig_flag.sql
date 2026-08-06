-- Asuntosäätiö B2B deals: flat-rate 400€ (ALV 0) / 502€ (incl. VAT) moves that the source
-- system always records as 0€, since they're free-of-charge to the tenant -- Asuntosäätiö
-- pays Apukuski outside the order record. Business rule from Jussi 2026-08-06.
--
-- Manual orders only (is_manual=true) -- platform orders don't have an equivalent single
-- "gig value" field to check against zero, so the mention+zero-value rule doesn't translate
-- there without guessing.
--
-- Text value 'Yes'/'No' (not boolean) per explicit spec, for readability in downstream
-- tools/spreadsheets that consume this column directly.

ALTER TABLE orders ADD COLUMN IF NOT EXISTS is_asuntosaatio_gig TEXT NOT NULL DEFAULT 'No';

CREATE INDEX IF NOT EXISTS idx_orders_is_asuntosaatio_gig
  ON orders (is_asuntosaatio_gig) WHERE is_asuntosaatio_gig = 'Yes';
