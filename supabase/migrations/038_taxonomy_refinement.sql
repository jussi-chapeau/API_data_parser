-- Customer-area segmentation, stage 2b: taxonomy refinement from the real tail (2026-09-21).
--
-- After seeding migration 037, 235 distinct order_type strings covering 607 orders (5.0%) fell
-- to the catch-all. Reading the actual tail rather than guessing at it, most of that splits
-- into a handful of real groups the first pass simply did not know about:
--
--   Laatikko*/Laatikoiden   moving-box delivery and collection, Finnish   ~110 orders
--   Move / Moving service   the same services in English                   ~50 orders
--   Huutokauppa / Kiertonet auction-site pickups (recommerce by another name) ~35 orders
--   Kantopalvelu / siirto   carry and furniture-shifting help              ~30 orders
--   Tyhjennys / jate / rosk clear-outs and waste runs                      ~15 orders
--
-- WHAT IS DELIBERATELY LEFT UNCLASSIFIED. Three groups stay in the catch-all on purpose:
--
--   1. 190 orders with an EMPTY order_type. Nothing to classify. Guessing from other columns
--      would manufacture a service that was never recorded.
--   2. ~35 orders whose order_type is a driver-availability note, not a service at all
--      ("Jos tyota on yli 2 tuntia, sen voi laitta" -- "if there's more than 2 hours of work,
--      you can add it"). The field was used as a scratchpad. These are real orders with no
--      service information in them.
--   3. Test rows and junk ("asfdfq", "TEST", "hhh", "ss", "wdwdw").
--
-- Leaving these unclassified is the honest outcome: they flow into totals and report as
-- `unknown`, rather than being quietly absorbed into a bucket that looks resolved.
--
-- GENERIC TRANSPORT WORDS GO TO `other`, NOT `recommerce`. "Nouto", "Toimitus", "Transport"
-- and "Delivery" say a thing moved, not what kind of job it was. Calling them recommerce would
-- invent a finding. They are segmentation-eligible with both ends treated as customers -- which
-- is true of a generic transport job -- but they are labelled `other` so nobody reads them as
-- marketplace volume.

INSERT INTO geo.service_rule
  (rule_id, priority, match_kind, pattern, service_group,
   origin_is_customer, destination_is_customer, segmentation_eligible, sensitivity, rationale)
VALUES
  -- Business, ahead of the generic English "move" rule it would otherwise lose to.
  (20, 15, 'keyword', 'office move', 'business', false, false, false, 'business',
   'Office move in English. Must outrank the generic move/moving rules, which it contains.'),
  (21, 25, 'keyword', 'reittiajo', 'route_ops', false, false, false, 'business',
   'Route driving -- our own multi-drop logistics, not a single customer job. Counted in '
   'totals, excluded from area segmentation.'),
  (22, 26, 'keyword', 'reitti', 'route_ops', false, false, false, 'business',
   'Variants: "Reitti", "Reittia". Same reasoning as reittiajo.'),

  -- Recycling / disposal. All four are clear-out work ending at a facility, same shape as
  -- Kierratyspalvelu, so the destination is excluded for the same reason.
  (23, 42, 'keyword', 'tyhjenn', 'recycling', true, false, true, 'normal',
   'Tyhjennys / Tyhjennyspalvelu / Kuolinpesantyhjennys -- clear-outs ending at disposal.'),
  (24, 43, 'keyword', 'tyhjays', 'recycling', true, false, true, 'normal',
   'Misspelling of tyhjennys seen in the data.'),
  (25, 44, 'keyword', 'jäte', 'recycling', true, false, true, 'normal',
   'Jatekuorma / Jatenouto -- waste runs.'),
  (26, 45, 'keyword', 'rosk', 'recycling', true, false, true, 'normal',
   'Roskat / Roskien vienti -- rubbish runs.'),
  (27, 46, 'keyword', 'sortti', 'recycling', true, false, true, 'normal',
   'Sorttikuorma -- a run to a Sortti recycling station.'),

  -- Moving boxes. The depot end is left to frequency detection rather than hardcoded.
  (28, 62, 'keyword', 'laatik', 'boxes', true, true, true, 'normal',
   'Laatikkonouto / Laatikkotoimitus / Laatikoiden nouto / Laatikot. Covers both the '
   '"laatikko" and "laatikoiden" stems, which do not share a substring.'),
  (29, 63, 'keyword', 'box delivery', 'boxes', true, true, true, 'normal',
   'English box delivery.'),

  -- Carry / lift help. Single-location work; the distinct-address rule collapses it to one end.
  (30, 71, 'keyword', 'kanto', 'carry_help', true, true, true, 'normal',
   'Kantopalvelu, Kanto-/asennusapu, "Sohvan kanto", "Viinikaapin kanto".'),
  (31, 72, 'keyword', 'carrying help', 'carry_help', true, true, true, 'normal', 'English.'),
  (32, 73, 'keyword', 'lifting help', 'carry_help', true, true, true, 'normal', 'English.'),
  (33, 74, 'keyword', 'lastausap', 'carry_help', true, true, true, 'normal', 'Loading help.'),
  (34, 75, 'keyword', 'nostoap', 'carry_help', true, true, true, 'normal', 'Lifting help.'),
  (35, 76, 'keyword', 'siirto', 'carry_help', true, true, true, 'normal',
   'Furniture shifting: "Sohvan siirto", "Sankyjen siirtoa", "Pianon siirto", "Siirtotyo".'),

  -- Moving, including the English and misspelled variants the 037 rule missed.
  (36, 82, 'keyword', 'muutt', 'muutto', true, true, true, 'normal',
   'Broader stem than "muutto": catches "Muutot", "Muuttto", "muuttp", "Muuton loppuun".'),
  (37, 85, 'keyword', 'moving', 'muutto', true, true, true, 'normal',
   'English moving service, incl. the Asuntosaatio signing-bonus variants.'),
  (38, 86, 'keyword', 'move', 'muutto', true, true, true, 'normal',
   'English "Move", "House move", "Home move". Office move is caught earlier at priority 15.'),
  (39, 87, 'keyword', 'relocat', 'muutto', true, true, true, 'normal', 'English relocation.'),
  (40, 88, 'keyword', 'pakkaus', 'muutto', true, true, true, 'normal',
   'Packing service -- sold alongside moving.'),

  -- Recommerce under other names. These ARE marketplace jobs, so they keep the recommerce
  -- label rather than falling into `other`.
  (41, 91, 'keyword', 'huuto', 'recommerce', true, true, true, 'normal',
   'Huutokauppa / Huutokaupat.com / Huutonouto -- auction-site pickups, seller -> buyer.'),
  (42, 92, 'keyword', 'huudon', 'recommerce', true, true, true, 'normal',
   'Genitive form "Huudon nouto", which does not contain the "huuto" stem.'),
  (43, 93, 'keyword', 'kiertonet', 'recommerce', true, true, true, 'normal',
   'Kiertonet.fi -- a recommerce marketplace.'),

  -- Generic transport. Deliberately `other`: the string says something moved, not what kind of
  -- job it was. Both ends treated as customers, which is true of a generic transport job.
  (44, 95, 'keyword', 'kuljetu', 'other', true, true, true, 'normal',
   'Broader stem than "kuljetus": "Kuljetukset", "Kuljetuksia".'),
  (45, 96, 'keyword', 'transport', 'other', true, true, true, 'normal', 'English.'),
  (46, 97, 'keyword', 'delivery', 'other', true, true, true, 'normal', 'English.'),
  (47, 98, 'keyword', 'nouto', 'other', true, true, true, 'normal',
   'Bare "Nouto" (pickup) with no service named.'),
  (48, 99, 'keyword', 'toimitus', 'other', true, true, true, 'normal',
   'Bare "Toimitus" (delivery) with no service named.')
ON CONFLICT (rule_id) DO UPDATE SET
  priority = EXCLUDED.priority, match_kind = EXCLUDED.match_kind, pattern = EXCLUDED.pattern,
  service_group = EXCLUDED.service_group, origin_is_customer = EXCLUDED.origin_is_customer,
  destination_is_customer = EXCLUDED.destination_is_customer,
  segmentation_eligible = EXCLUDED.segmentation_eligible, sensitivity = EXCLUDED.sensitivity,
  rationale = EXCLUDED.rationale;

SELECT geo.refresh_order_type_resolution();
