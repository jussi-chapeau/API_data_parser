-- Customer-area segmentation, stage 1: the `geo` schema and Paavo reference data (2026-09-21).
--
-- WHY A NEW SCHEMA. Migration 022 defines `core` as durable AND consumer-readable by default:
--
--     GRANT SELECT ON ALL TABLES IN SCHEMA core TO bi_chatbot_readonly;
--     ALTER DEFAULT PRIVILEGES IN SCHEMA core GRANT SELECT ON TABLES TO bi_chatbot_readonly;
--
-- That second line is the dangerous one -- any table added to `core` later becomes readable by
-- the BI consumer on creation, with no grant statement in the migration for a reviewer to
-- notice. Per-stop postcodes are exactly the kind of data that must not inherit that. `geo` is
-- a separate schema with no grants at all, so the blanket cannot reach it and no future
-- migration has to remember to revoke.
--
-- WHY PostGIS GOES IN `extensions`. It installs ~1,000 functions and several types. In `public`
-- they would widen the PostgREST-exposed surface permanently -- the same argument migration 033
-- makes for using an RPC instead of exposing a schema. `extensions` is already the convention
-- here (pgcrypto, uuid-ossp).
--
-- PAAVO ENCODES SUPPRESSION AS -1, AND IT MATTERS. Statistics Finland withholds figures for
-- areas too small to publish safely. It signals that with **-1**, not NULL. Measured against
-- the live 2026 layer:
--
--     hr_mtu   = -1 in  74 areas      tr_mtu   = -1 in 188 areas
--     ra_asunn = -1 in  23 areas      he_vakiy =  0 in  17 areas (genuinely uninhabited)
--
-- Loaded verbatim, those 74 areas would sort into the BOTTOM income band and be reported as
-- our poorest customer areas. That is a fabricated finding, not a rounding error. The loader
-- maps -1 to NULL and sets `paavo_suppressed`; banding then ignores them and they surface as
-- `income_band = 'unknown'`. 0 is left alone -- an uninhabited postcode really does have zero
-- inhabitants.
--
-- Source: https://geo.stat.fi/geoserver/postialue/wfs, layer postialue:pno_tilasto_2026.
-- 3,018 areas, EPSG:3067 (ETRS-TM35FIN), geometry and 113 attributes in one layer.

CREATE EXTENSION IF NOT EXISTS postgis SCHEMA extensions;

CREATE SCHEMA IF NOT EXISTS geo;

-- Deliberately no grant to bi_chatbot_readonly, here or ever. Stated as an explicit REVOKE so
-- the intent is visible in the file rather than implied by the absence of a GRANT.
REVOKE ALL ON SCHEMA geo FROM PUBLIC;

COMMENT ON SCHEMA geo IS
  'Internal geospatial reference and per-stop postcode resolution. NOT consumer-readable: '
  'deliberately outside `core`, whose ALTER DEFAULT PRIVILEGES would auto-grant SELECT to '
  'bi_chatbot_readonly. Nothing in here may be granted to a BI role. See migration 036.';

-- ---------------------------------------------------------------------------
-- Vintage registry. Paavo publishes a new layer each year; we keep every vintage we have
-- loaded and bind each order to the one current when it was created, so a new release adds
-- rows rather than restating history.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS geo.paavo_vintage (
  vintage        text PRIMARY KEY,               -- e.g. 'pno_tilasto_2026'
  reference_year integer NOT NULL,
  source_url     text NOT NULL,
  layer_name     text NOT NULL,
  srid           integer NOT NULL,               -- as reported by the WFS, not assumed
  feature_count  integer,
  loaded_at      timestamptz NOT NULL DEFAULT now(),
  notes          text
);

-- ---------------------------------------------------------------------------
-- The areas themselves. A deliberate subset of Paavo's 113 attributes -- the ones the
-- segmentation actually uses -- rather than a verbatim copy.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS geo.paavo_area (
  vintage           text    NOT NULL REFERENCES geo.paavo_vintage(vintage),
  postal_code       text    NOT NULL,            -- postinumeroalue, e.g. '00100'
  name_fi           text,                        -- nimi
  name_sv           text,                        -- namn
  municipality_code text,                        -- kunta, e.g. '091'; the roll-up key
  reference_year    integer,                     -- vuosi
  area_m2           bigint,                      -- pinta_ala

  -- population
  inhabitants       integer,                     -- he_vakiy

  -- income. hr_* is per inhabitant, tr_* per household. Both medians kept: household income
  -- is the better proxy for a moving customer, individual income the better one for a
  -- recommerce buyer, and which we band on is a decision we should be able to revisit
  -- without reloading.
  median_income_person    integer,               -- hr_mtu
  mean_income_person      integer,               -- hr_ktu
  median_income_household integer,               -- tr_mtu
  mean_income_household   integer,               -- tr_ktu
  earners_low             integer,               -- hr_pi_tul
  earners_mid             integer,               -- hr_ke_tul
  earners_high            integer,               -- hr_hy_tul

  -- household life stage (te_*), the basis for the dominant-life-stage label
  households              integer,               -- te_taly
  hh_singles              integer,               -- te_yks
  hh_young                integer,               -- te_nuor
  hh_with_children        integer,               -- te_laps
  hh_adults               integer,               -- te_aik
  hh_pensioners           integer,               -- te_elak
  hh_owner_occupied       integer,               -- te_omis_as
  hh_rented               integer,               -- te_vuok_as
  floor_area_per_person   numeric,               -- te_as_valj

  -- dwellings (ra_*)
  dwellings               integer,               -- ra_asunn
  dwellings_detached      integer,               -- ra_raky
  dwellings_flats         integer,               -- ra_kt_as
  avg_dwelling_area       numeric,               -- ra_as_kpa

  -- education (ko_*) and employment (pt_*)
  edu_base_only           integer,               -- ko_perus
  edu_lower_tertiary      integer,               -- ko_al_kork
  edu_higher_tertiary     integer,               -- ko_yl_kork
  edu_population_18plus   integer,               -- ko_ika18y
  employed                integer,               -- pt_tyoll
  unemployed              integer,               -- pt_tyott
  students                integer,               -- pt_opisk
  pensioners              integer,               -- pt_elakel

  geom              extensions.geometry(MultiPolygon, 3067),

  -- True when Statistics Finland withheld this area's figures (the -1 sentinel) or the area
  -- is uninhabited. Such areas must never be banded -- see the header note.
  paavo_suppressed  boolean NOT NULL DEFAULT false,

  first_seen_at     timestamptz NOT NULL DEFAULT now(),
  last_seen_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (vintage, postal_code)
);

CREATE INDEX IF NOT EXISTS idx_paavo_area_geom ON geo.paavo_area USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_paavo_area_postal ON geo.paavo_area (postal_code);

COMMENT ON COLUMN geo.paavo_area.paavo_suppressed IS
  'Statistics Finland withheld this area''s figures (signalled upstream as -1, mapped to NULL '
  'on load) or the area is uninhabited. 74 areas in the 2026 vintage. Excluded from band '
  'cutting and labelled income_band = ''unknown'' -- banding them would invent a bottom-income '
  'finding out of a suppression marker.';

-- ---------------------------------------------------------------------------
-- Band definitions as DATA, not a CASE expression buried in a view. "Income quintile 3"
-- should be answerable -- which euro range, cut from which vintage, on what date -- without
-- reading SQL or a git blame.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS geo.paavo_band_def (
  vintage     text    NOT NULL REFERENCES geo.paavo_vintage(vintage),
  dimension   text    NOT NULL,                  -- 'income_household' | 'area_type' | ...
  band_label  text    NOT NULL,
  band_order  integer NOT NULL,
  lower_bound numeric,                           -- inclusive; NULL = open below
  upper_bound numeric,                           -- exclusive; NULL = open above
  source_note text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (vintage, dimension, band_label)
);

COMMENT ON TABLE geo.paavo_band_def IS
  'Banding thresholds, cut against the NATIONAL distribution of all 3,018 areas -- never '
  'against Apukuski''s own order footprint. Footprint-derived quantiles shift every time the '
  'business enters a new city, silently relabelling history that nobody edited.';

-- ---------------------------------------------------------------------------
-- Derived labels, one row per area per vintage. This is the only thing downstream reads;
-- raw Paavo values never leave `geo`.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS geo.paavo_label (
  vintage          text NOT NULL REFERENCES geo.paavo_vintage(vintage),
  postal_code      text NOT NULL,
  income_band      text NOT NULL DEFAULT 'unknown',
  life_stage_label text NOT NULL DEFAULT 'unknown',
  area_type        text NOT NULL DEFAULT 'unknown',
  label_confidence text NOT NULL DEFAULT 'ok',   -- 'ok' | 'suppressed'
  computed_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (vintage, postal_code),
  FOREIGN KEY (vintage, postal_code) REFERENCES geo.paavo_area (vintage, postal_code)
);
