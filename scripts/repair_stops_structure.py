#!/usr/bin/env python3
"""
Repair `orders.stops` for orders synced before the Backoffice API changed its response shape.

WHAT HAPPENED. Around 2026-08 the Backoffice API changed `/order`'s `stops` field from a flat
address string to a structured array -- one element per stop, each with `address`, `location`
(lat/long), `apartment`, `carryingNeeds` and contact info. Our sync passes `stops` through
unchanged, so it stored whatever shape it was given. But the scheduled syncs only touch a
rolling 45-day window, so every order last synced before the change kept the old flat string
permanently:

    synced 2026-09   990 array    0 string
    synced 2026-08   351 array   64 string
    synced 2026-07     0 array 1895 string
    synced 2026-06     0 array 4401 string

Nothing was lost at source. Verified across 170 platform orders sampled over 25 days spanning
2024-01..2026-09: the API returns the array form with lat/long coordinates on 100% of them,
for 2024 orders as readily as for last week's.

WHY IT MATTERS. Postcode coverage in Supabase is 38.8% and badly biased -- 81.2% on manual
orders (ops typed the address, including a postcode) against 17.0% on platform orders (the
address autocomplete stores "Street, City, Suomi" with none). Coordinates are the way out:
they are present on ~100% of platform orders and resolve to a postcode by point-in-polygon
against Paavo's published area geometry, with no geocoding API and no address leaving the
system. The truncated string cannot be recovered any other way.

WHY `stops` ONLY. This does not re-run the full transform. Re-upserting every column would
touch money fields, flags and manual_data for 6,000+ historical rows to fix one column -- a
far wider blast radius than the problem warrants. Everything needed (both stops, coordinates,
floors, apartments) lives inside `stops`.

SAFETY
  * Day-by-day fetches with backoff. 30-day windows fail ~47% of the time with 500/504
    (docs/BACKOFFICE_API_ISSUES.md #7), and a failed chunk here would look like "no orders".
  * A row is only updated when the API gives an ARRAY and the stored value is not already one.
    A day the API cannot serve is skipped, never blanked.
  * Never widens: if the API returns a string for an order we hold as a string, nothing is
    written.
  * Snapshot lives in public.orders_stops_backup_20260921 (in-database deliberately -- `stops`
    is PII and a file would be a second copy outside the trust boundary).

PII NOTE. `stops` carries customer street addresses, and in the array form also contact names
and phone numbers. docs/GDPR_REVIEW.md #9 rates an address plus a moving date as higher-risk
than a name. This script moves that data between two systems that already hold it and prints
only counts -- never an address, never a name.

Usage:
  export SUPABASE_URL=... SUPABASE_SERVICE_KEY=... BACKOFFICE_API_URL=... BACKOFFICE_API_KEY=...
  python3 scripts/repair_stops_structure.py --dry-run
  python3 scripts/repair_stops_structure.py
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
from datetime import date, datetime, timedelta, timezone

MAX_TRIES = 5
BASE_BACKOFF = 1.5


def api_orders_for_day(api_base: str, api_key: str, day: date) -> list | None:
    """Platform orders created on `day`. None means the day could not be read.

    None is distinct from [] on purpose: an unreadable day must be skipped, not treated as
    "this day has no orders", which would be indistinguishable from real emptiness.
    """
    params = {"start_date": day.isoformat(), "end_date": day.isoformat(),
              "includeUnscheduled": "true"}
    url = f"{api_base.rstrip('/')}/order?{urllib.parse.urlencode(params)}"
    for attempt in range(1, MAX_TRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"x-api-key": api_key})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data if isinstance(data, list) else None
        except Exception:
            if attempt < MAX_TRIES:
                time.sleep(BASE_BACKOFF * (2 ** (attempt - 1)))
    return None


def supa(url: str, key: str, path: str, method: str = "GET", body=None, prefer=None):
    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "Content-Type": "application/json"}
    if prefer:
        headers["Prefer"] = prefer
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"{url.rstrip('/')}/rest/v1/{path}", data=data,
                                 method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=90) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else None


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--start", default="2024-01-01")
    p.add_argument("--end", default=None, help="default: today")
    args = p.parse_args()

    api_base = os.environ.get("BACKOFFICE_API_URL")
    api_key = os.environ.get("BACKOFFICE_API_KEY")
    su = os.environ.get("SUPABASE_URL")
    sk = os.environ.get("SUPABASE_SERVICE_KEY")
    if not all([api_base, api_key, su, sk]):
        print("ERROR: BACKOFFICE_API_URL/KEY and SUPABASE_URL/SERVICE_KEY required.",
              file=sys.stderr)
        return 2

    # Which orders still hold the flat string. Fetched once so the loop needs no per-day reads.
    needs: set[str] = set()
    offset = 0
    while True:
        rows = supa(su, sk, "orders?select=order_id&is_manual=eq.false"
                            f"&stops=not.like.[%25&limit=1000&offset={offset}") or []
        needs.update(r["order_id"] for r in rows)
        if len(rows) < 1000:
            break
        offset += 1000
    print(f"orders holding the flat string: {len(needs)}")

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else date.today()
    total_days = (end - start).days + 1

    repaired = skipped_days = unchanged = 0
    batch: list[dict] = []
    day = start
    i = 0
    while day <= end:
        i += 1
        rows = api_orders_for_day(api_base, api_key, day)
        if rows is None:
            skipped_days += 1
            print(f"  [{i}/{total_days}] {day} UNREADABLE after {MAX_TRIES} tries -- skipped")
            day += timedelta(days=1)
            continue

        for o in rows:
            oid = o.get("orderId")
            s = o.get("stops")
            # Only ever widen: array from the API, and we do not already hold an array.
            if oid in needs and isinstance(s, list) and s:
                batch.append({"order_id": oid, "stops": json.dumps(s, ensure_ascii=False),
                              "synced_at": datetime.now(timezone.utc).isoformat()})
            elif oid in needs:
                unchanged += 1

        if len(batch) >= 200:
            if not args.dry_run:
                supa(su, sk, "orders?on_conflict=order_id", "POST", batch,
                     prefer="resolution=merge-duplicates,return=minimal")
            repaired += len(batch)
            print(f"  [{i}/{total_days}] {day}  repaired so far: {repaired}")
            batch = []
        day += timedelta(days=1)

    if batch:
        if not args.dry_run:
            supa(su, sk, "orders?on_conflict=order_id", "POST", batch,
                 prefer="resolution=merge-duplicates,return=minimal")
        repaired += len(batch)

    print(f"\n{'DRY RUN -- ' if args.dry_run else ''}repaired: {repaired}")
    print(f"still string at source (API gave no array): {unchanged}")
    print(f"days unreadable and skipped: {skipped_days}")
    if skipped_days:
        print("  -> re-run for those days; nothing was blanked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
