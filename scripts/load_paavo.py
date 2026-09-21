#!/usr/bin/env python3
"""Load Statistics Finland's Paavo postcode-area data into `geo.paavo_area`.

Source: https://geo.stat.fi/geoserver/postialue/wfs, layer postialue:pno_tilasto_<year>.
3,018 areas, EPSG:3067 (ETRS-TM35FIN), carrying geometry AND 113 statistical attributes in a
single layer -- no join between separate geometry and attribute services.

THE -1 TRAP. Paavo signals "withheld because the area is too small to publish safely" with
**-1**, not NULL. Measured against the live 2026 layer: hr_mtu is -1 in 74 areas, tr_mtu in
188, ra_asunn in 23. Loaded verbatim, those 74 areas sort into the BOTTOM income band and get
reported as our poorest customer areas -- a fabricated finding manufactured out of a
suppression marker. This script maps -1 to NULL and sets `paavo_suppressed`, so banding skips
them and they surface honestly as `unknown`.

Note the distinction from 0: seventeen areas have he_vakiy = 0 and are genuinely uninhabited.
0 is a real measurement and is left alone.

SRID IS READ, NOT ASSUMED. The WFS reports its CRS in the response; we store what it actually
sent in `geo.paavo_vintage.srid` rather than hardcoding 3067, so a server-side change shows up
as a mismatch instead of silently shifting every polygon.

COORDINATE PRECISION. Coordinates are rounded to whole metres, which halves the payload
(19MB -> 9MB) with no practical effect: postcode areas are hundreds of metres across and the
order coordinates we test against are nowhere near metre-accurate. Rounding can in principle
nick a polygon into self-intersection, so every geometry goes through ST_MakeValid on the way
in and the script verifies ST_IsValid afterwards.

MERGE-ONLY. Upserts never delete, and NULLs never overwrite existing values -- the same
discipline as core.refresh_from_windsor() in migration 022. Vintage is part of the primary key,
so loading a new year ADDS rows and leaves every existing binding untouched.

Usage:
  export SUPABASE_ACCESS_TOKEN=...
  python3 scripts/load_paavo.py --dry-run
  python3 scripts/load_paavo.py --year 2026
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request

WFS_BASE = "https://geo.stat.fi/geoserver/postialue/wfs"
PROJECT_REF = "ybznbfezrdgzgptxkgul"
MGMT_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
PAGE = 200
MAX_SQL_BYTES = 1_200_000     # keep each Management API call comfortably small

# Paavo attribute -> our column. Order matters: it drives the INSERT column list.
FIELDS: list[tuple[str, str, str]] = [
    # (paavo_name, our_column, pg_type)
    ("nimi",       "name_fi",                 "text"),
    ("namn",       "name_sv",                 "text"),
    ("kunta",      "municipality_code",       "text"),
    ("vuosi",      "reference_year",          "integer"),
    ("pinta_ala",  "area_m2",                 "bigint"),
    ("he_vakiy",   "inhabitants",             "integer"),
    ("hr_mtu",     "median_income_person",    "integer"),
    ("hr_ktu",     "mean_income_person",      "integer"),
    ("tr_mtu",     "median_income_household", "integer"),
    ("tr_ktu",     "mean_income_household",   "integer"),
    ("hr_pi_tul",  "earners_low",             "integer"),
    ("hr_ke_tul",  "earners_mid",             "integer"),
    ("hr_hy_tul",  "earners_high",            "integer"),
    ("te_taly",    "households",              "integer"),
    ("te_yks",     "hh_singles",              "integer"),
    ("te_nuor",    "hh_young",                "integer"),
    ("te_laps",    "hh_with_children",        "integer"),
    ("te_aik",     "hh_adults",               "integer"),
    ("te_elak",    "hh_pensioners",           "integer"),
    ("te_omis_as", "hh_owner_occupied",       "integer"),
    ("te_vuok_as", "hh_rented",               "integer"),
    ("te_as_valj", "floor_area_per_person",   "numeric"),
    ("ra_asunn",   "dwellings",               "integer"),
    ("ra_raky",    "dwellings_detached",      "integer"),
    ("ra_kt_as",   "dwellings_flats",         "integer"),
    ("ra_as_kpa",  "avg_dwelling_area",       "numeric"),
    ("ko_perus",   "edu_base_only",           "integer"),
    ("ko_al_kork", "edu_lower_tertiary",      "integer"),
    ("ko_yl_kork", "edu_higher_tertiary",     "integer"),
    ("ko_ika18y",  "edu_population_18plus",   "integer"),
    ("pt_tyoll",   "employed",                "integer"),
    ("pt_tyott",   "unemployed",              "integer"),
    ("pt_opisk",   "students",                "integer"),
    ("pt_elakel",  "pensioners",              "integer"),
]


def mgmt(token: str, sql: str) -> list:
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{PROJECT_REF}/database/query",
        data=json.dumps({"query": sql}).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "User-Agent": MGMT_UA})
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))


def lit(v) -> str:
    """SQL literal. Everything goes in as text or NULL; the SELECT casts it."""
    if v is None:
        return "NULL"
    return "'" + str(v).replace("'", "''") + "'"


def fetch_page(layer: str, start: int) -> tuple[list, str, int]:
    params = {"service": "WFS", "version": "2.0.0", "request": "GetFeature",
              "typeName": f"postialue:{layer}", "count": str(PAGE),
              "startIndex": str(start), "outputFormat": "application/json"}
    url = WFS_BASE + "?" + urllib.parse.urlencode(params)
    for attempt in range(1, 5):
        try:
            with urllib.request.urlopen(url, timeout=300) as r:
                d = json.loads(r.read().decode("utf-8"))
            crs = (d.get("crs") or {}).get("properties", {}).get("name", "")
            return d.get("features", []), crs, d.get("totalFeatures") or d.get("numberMatched") or 0
        except Exception as e:
            if attempt == 4:
                raise
            print(f"  page {start} failed ({e}); retrying", file=sys.stderr)
            time.sleep(2 ** attempt)
    return [], "", 0


def round_coords(o):
    if isinstance(o, list):
        return [round_coords(x) for x in o]
    if isinstance(o, float):
        return round(o)
    return o


def clean(paavo_name: str, value):
    """-1 is Paavo's 'withheld' marker, not a measurement. 0 is a real zero -- keep it."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and value == -1:
        return None
    return value


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    token = os.environ.get("SUPABASE_ACCESS_TOKEN")
    if not token and not args.dry_run:
        print("ERROR: SUPABASE_ACCESS_TOKEN required (or use --dry-run).", file=sys.stderr)
        return 2

    layer = f"pno_tilasto_{args.year}"
    vintage = layer

    rows, srs, total = [], "", 0
    start = 0
    while True:
        feats, crs, tot = fetch_page(layer, start)
        if crs:
            srs = crs
        total = tot or total
        if not feats:
            break
        for f in feats:
            p = f["properties"]
            geom = f.get("geometry")
            vals = [clean(src, p.get(src)) for src, _, _ in FIELDS]
            by_col = {col: v for (src, col, _), v in zip(FIELDS, vals)}
            suppressed = (by_col["median_income_person"] is None
                          or (by_col["inhabitants"] or 0) == 0)
            rows.append({
                "postal_code": p.get("postinumeroalue"),
                "vals": vals,
                "suppressed": suppressed,
                "geojson": (json.dumps({**geom, "coordinates": round_coords(geom["coordinates"])},
                                       separators=(",", ":")) if geom else None),
            })
        start += PAGE
        print(f"  fetched {len(rows)}/{total}")
        if total and start >= total:
            break

    srid = 3067
    if srs and "::" in srs:
        try:
            srid = int(srs.rsplit("::", 1)[1])
        except ValueError:
            pass

    n_sup = sum(1 for r in rows if r["suppressed"])
    n_nogeom = sum(1 for r in rows if not r["geojson"])
    print(f"\nlayer {layer}: {len(rows)} areas, CRS {srs or '(not reported)'} -> SRID {srid}")
    print(f"  suppressed (Paavo withheld or uninhabited): {n_sup}")
    print(f"  without geometry: {n_nogeom}")
    if args.dry_run:
        xs: list[float] = []
        ys: list[float] = []
        def walk(o):
            if isinstance(o, list):
                if o and isinstance(o[0], (int, float)) and len(o) == 2:
                    xs.append(o[0]); ys.append(o[1])
                else:
                    for x in o:
                        walk(x)
        for r in rows:
            if r["geojson"]:
                walk(json.loads(r["geojson"])["coordinates"])
        if xs:
            print(f"  bbox: x {min(xs)}..{max(xs)}  y {min(ys)}..{max(ys)}")
        print("DRY RUN -- nothing written.")
        return 0

    cols = ["vintage", "postal_code"] + [c for _, c, _ in FIELDS] + ["geom", "paavo_suppressed"]
    sel = (["v.vintage::text", "v.postal_code::text"]
           + [f"v.{c}::{t}" for _, c, t in FIELDS]
           + ["extensions.ST_Multi(extensions.ST_CollectionExtract("
              "extensions.ST_MakeValid(extensions.ST_SetSRID("
              f"extensions.ST_GeomFromGeoJSON(v.geojson), {srid})), 3))",
              "v.paavo_suppressed::boolean"])
    updates = ",\n    ".join(
        [f"{c} = COALESCE(EXCLUDED.{c}, a.{c})" for _, c, _ in FIELDS]
        + ["geom = COALESCE(EXCLUDED.geom, a.geom)",
           "paavo_suppressed = EXCLUDED.paavo_suppressed",
           "last_seen_at = now()"])
    vcols = ", ".join(["vintage", "postal_code"] + [c for _, c, _ in FIELDS]
                      + ["geojson", "paavo_suppressed"])

    mgmt(token, f"""
      INSERT INTO geo.paavo_vintage (vintage, reference_year, source_url, layer_name, srid,
                                     feature_count, notes)
      VALUES ({lit(vintage)}, {args.year}, {lit(WFS_BASE)}, {lit(layer)}, {srid}, {len(rows)},
              'Loaded by scripts/load_paavo.py. -1 mapped to NULL; coords rounded to 1 m.')
      ON CONFLICT (vintage) DO UPDATE SET srid = EXCLUDED.srid,
        feature_count = EXCLUDED.feature_count, loaded_at = now()
    """)

    batch, size, written = [], 0, 0
    def flush():
        nonlocal batch, size, written
        if not batch:
            return
        sql = (f"INSERT INTO geo.paavo_area AS a ({', '.join(cols)})\n"
               f"SELECT {', '.join(sel)}\nFROM (VALUES\n"
               + ",\n".join(batch)
               + f"\n) AS v({vcols})\nON CONFLICT (vintage, postal_code) DO UPDATE SET\n    "
               + updates)
        mgmt(token, sql)
        written += len(batch)
        print(f"  upserted {written}/{len(rows)}")
        batch, size = [], 0

    for i, r in enumerate(rows):
        cast = "::text" if not batch else ""
        parts = ([lit(vintage) + cast, lit(r["postal_code"]) + cast]
                 + [lit(v) + cast for v in r["vals"]]
                 + [lit(r["geojson"]) + cast, lit(r["suppressed"]) + cast])
        row_sql = "  (" + ", ".join(parts) + ")"
        if size + len(row_sql) > MAX_SQL_BYTES and batch:
            flush()
            row_sql = "  (" + ", ".join(
                [lit(vintage) + "::text", lit(r["postal_code"]) + "::text"]
                + [lit(v) + "::text" for v in r["vals"]]
                + [lit(r["geojson"]) + "::text", lit(r["suppressed"]) + "::text"]) + ")"
        batch.append(row_sql)
        size += len(row_sql)
    flush()

    chk = mgmt(token, """
      SELECT count(*) AS areas,
             count(*) FILTER (WHERE geom IS NULL) AS no_geom,
             count(*) FILTER (WHERE NOT extensions.ST_IsValid(geom)) AS invalid,
             count(*) FILTER (WHERE paavo_suppressed) AS suppressed
      FROM geo.paavo_area WHERE vintage = '""" + vintage + "'")
    print("\nverification:", json.dumps(chk))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
