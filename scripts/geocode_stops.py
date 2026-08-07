#!/usr/bin/env python3
"""
Geocode orders.stops via Apukuski's internal Staff API (/places/geocode), populating
pickup_city/pickup_postal_code/delivery_city/delivery_postal_code/waypoints_parsed/
stop_building_details.

Uses forward geocoding (address text -> structured city/postal via version=2) rather than
reverse geocoding from existing coordinates -- decided 2026-08-07: we mainly want city/postal
for regional/hub reporting, not survey-grade precision, and version=2 does the address-
component parsing for us (locality fallback to administrative_area, etc.) instead of us
re-implementing it against a bare formatted-address string.

IMPORTANT -- stops format is NOT uniformly structured. Confirmed against real data
(2026-08-07): only 579 of 6,941 platform orders (8.3%) have `stops` as a JSON array with an
explicit pickup/delivery role. The rest -- 91.7% of platform orders, and effectively all
manual orders -- have `stops` as a single plain address string with no role marker at all.
This script does NOT guess a role onto an unlabeled single address: those get
role='unknown' in waypoints_parsed, and pickup_city/delivery_city stay null for them. See
docs/STATUS.md workstream A for the open question this raises.

Usage (parse-only, no API calls, no writes -- safe to run anytime):
  python3 scripts/geocode_stops.py --dry-run --sample 200

Usage (real run, once STAFF_GEOCODE_API_URL is available):
  export STAFF_GEOCODE_API_URL="https://<staff-api-id>.execute-api.eu-central-1.amazonaws.com/production/places/geocode"
  export SUPABASE_ACCESS_TOKEN="..."
  python3 scripts/geocode_stops.py --start 2026-01-01 --end 2026-01-31
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

PROJECT_REF = "ybznbfezrdgzgptxkgul"
MGMT_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

MAX_TRIES = 4
BASE_BACKOFF = 1.5
PACE_SECONDS = 0.3  # deliberate throttle -- shared, unrated-limited internal endpoint


def mgmt_sql(access_token: str, sql: str):
    body = json.dumps({"query": sql}).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{PROJECT_REF}/database/query",
        data=body, method="POST",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json", "User-Agent": MGMT_UA},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else []


def sql_literal(val) -> str:
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "TRUE" if val else "FALSE"
    if isinstance(val, (int, float)):
        return repr(val)
    if isinstance(val, (dict, list)):
        escaped = json.dumps(val, ensure_ascii=False).replace("'", "''")
        return f"'{escaped}'::jsonb"
    escaped = str(val).replace("'", "''")
    return f"'{escaped}'"


def parse_stops(raw: str | None) -> list[dict]:
    """Returns a list of waypoint dicts: {role, raw_address, apartment, floor,
    elevator_in_use, carrier_count, information}. role is 'pickup'/'delivery'/'unknown'.
    Empty/unparseable-into-an-address input returns []."""
    if not raw or not raw.strip():
        return []

    text = raw.strip()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            parsed = None
        if isinstance(parsed, list):
            waypoints = []
            for stop in parsed:
                if not isinstance(stop, dict):
                    continue
                address = stop.get("address")
                if not address:
                    continue
                floors = stop.get("carryingNeeds", {}).get("floors", {}) if isinstance(stop.get("carryingNeeds"), dict) else {}
                waypoints.append({
                    "role": stop.get("role") if stop.get("role") in ("pickup", "delivery") else "unknown",
                    "raw_address": address,
                    "apartment": stop.get("apartment") or None,
                    "floor": floors.get("floor"),
                    "elevator_in_use": floors.get("elevatorInUse"),
                    "carrier_count": (stop.get("carryingNeeds", {}) or {}).get("carrierCount"),
                    "information": stop.get("information") or None,
                })
            if waypoints:
                return waypoints
        # starts with "[" but didn't parse as a usable stop list -- fall through to
        # plain-text handling rather than silently dropping it.

    # Plain text: deliberately does NOT try to split multi-line/prose text into multiple
    # addresses -- that's exactly the kind of fragile heuristic likely to produce garbage.
    # Single-line address strings (the common case, 91.7% of platform orders) geocode fine
    # as one candidate. Long freeform manual-order notes may simply fail to geocode, which
    # is fine -- geocode_status will say so rather than us fabricating structure that isn't
    # there.
    first_line = text.split("\n", 1)[0].strip()
    if not first_line:
        return []
    return [{
        "role": "unknown",
        "raw_address": first_line,
        "apartment": None,
        "floor": None,
        "elevator_in_use": None,
        "carrier_count": None,
        "information": text if "\n" in text else None,
    }]


def call_geocode(api_url: str, address: str) -> dict:
    """Returns {status, city, postal_code, latitude, longitude, geocoded_address} where
    status is 'ok' | 'not_found' | 'error'."""
    qs = urllib.parse.urlencode({"mode": "geocode", "version": "2", "address": address})
    url = f"{api_url}?{qs}"
    last_err = None
    for attempt in range(1, MAX_TRIES + 1):
        req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return {
                    "status": "ok",
                    "city": data.get("city") or None,
                    "postal_code": data.get("postalCode") or None,
                    "latitude": data.get("latitude"),
                    "longitude": data.get("longitude"),
                    "geocoded_address": data.get("address"),
                }
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {"status": "not_found", "city": None, "postal_code": None, "latitude": None, "longitude": None, "geocoded_address": None}
            last_err = e
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
        if attempt < MAX_TRIES:
            time.sleep(BASE_BACKOFF * (2 ** (attempt - 1)))
    return {"status": "error", "error": str(last_err), "city": None, "postal_code": None, "latitude": None, "longitude": None, "geocoded_address": None}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="Parse only, no geocode API calls, no writes")
    p.add_argument("--sample", type=int, default=200, help="Row sample size for --dry-run")
    p.add_argument("--start", help="YYYY-MM-DD created_at range start, for a real run")
    p.add_argument("--end", help="YYYY-MM-DD created_at range end, for a real run")
    args = p.parse_args()

    access_token = os.environ.get("SUPABASE_ACCESS_TOKEN")
    if not access_token:
        print("ERROR: SUPABASE_ACCESS_TOKEN is not set.", file=sys.stderr)
        return 1

    if args.dry_run:
        rows = mgmt_sql(access_token, f"select order_id, is_manual, stops from orders where stops is not null and stops != '' order by random() limit {int(args.sample)}")
        stats = {"total": 0, "produced_waypoints": 0, "no_address": 0, "role_known": 0, "role_unknown": 0}
        for r in rows:
            stats["total"] += 1
            wps = parse_stops(r["stops"])
            if not wps:
                stats["no_address"] += 1
                continue
            stats["produced_waypoints"] += 1
            for w in wps:
                if w["role"] in ("pickup", "delivery"):
                    stats["role_known"] += 1
                else:
                    stats["role_unknown"] += 1
        print(json.dumps(stats, indent=2))
        return 0

    api_url = os.environ.get("STAFF_GEOCODE_API_URL")
    if not api_url:
        print("ERROR: STAFF_GEOCODE_API_URL is not set. Not available yet -- see docs/STATUS.md workstream A.", file=sys.stderr)
        return 1

    if not args.start or not args.end:
        print("ERROR: pass --start and --end for a real run", file=sys.stderr)
        return 1

    rows = mgmt_sql(access_token, (
        f"select order_id, is_manual, stops from orders "
        f"where created_at >= {sql_literal(args.start)} and created_at < {sql_literal(args.end)}::date + 1 "
        f"and stops is not null and stops != '' and waypoints_parsed is null"
    ))
    print(f"Processing {len(rows)} orders...")

    updated = 0
    for r in rows:
        waypoints = parse_stops(r["stops"])
        if not waypoints:
            continue

        building_details = []
        for w in waypoints:
            geo = call_geocode(api_url, w["raw_address"]) if w["raw_address"] else {"status": "skipped_no_address"}
            w["geocode_status"] = geo["status"]
            w["city"] = geo.get("city")
            w["postal_code"] = geo.get("postal_code")
            w["latitude"] = geo.get("latitude")
            w["longitude"] = geo.get("longitude")
            w["geocoded_address"] = geo.get("geocoded_address")
            building_details.append({
                "role": w["role"], "apartment": w["apartment"], "floor": w["floor"],
                "elevator_in_use": w["elevator_in_use"], "carrier_count": w["carrier_count"],
                "information": w["information"],
            })
            time.sleep(PACE_SECONDS)

        pickup = next((w for w in waypoints if w["role"] == "pickup"), None)
        delivery = next((w for w in waypoints if w["role"] == "delivery"), None)

        update_sql = (
            f"update orders set "
            f"waypoints_parsed = {sql_literal(waypoints)}, "
            f"stop_building_details = {sql_literal(building_details)}, "
            f"pickup_city = {sql_literal(pickup['city'] if pickup else None)}, "
            f"pickup_postal_code = {sql_literal(pickup['postal_code'] if pickup else None)}, "
            f"delivery_city = {sql_literal(delivery['city'] if delivery else None)}, "
            f"delivery_postal_code = {sql_literal(delivery['postal_code'] if delivery else None)} "
            f"where order_id = {sql_literal(r['order_id'])}"
        )
        mgmt_sql(access_token, update_sql)
        updated += 1
        if updated % 50 == 0:
            print(f"  ...{updated} done")

    print(f"OK: updated {updated} orders.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
