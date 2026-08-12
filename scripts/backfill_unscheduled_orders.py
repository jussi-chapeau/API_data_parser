#!/usr/bin/env python3
"""
One-time historical backfill for unscheduled orders (orders without firstSchedule).

Root cause (found 2026-08-08, see docs/GOTCHAS.md and BACKOFFICE_API_ISSUES.md #12):
the Backoffice /order endpoint omits orders lacking firstSchedule ("schedule later" /
offer-based freight gigs) unless includeUnscheduled=true is passed. Our N8N Fetch Orders
nodes never passed it, so these orders were silently missing from Supabase for the
project's entire history -- confirmed via a live probe: Supabase had exactly 1 order with
first_schedule IS NULL out of 11,379 total before this fix.

n8n-workflows/orders-{hot,warm,cool}.json and backfill.json now pass
includeUnscheduled=true going forward (fixed 2026-08-08). This script covers the
historical gap behind that fix in one pass -- it deliberately does NOT re-fetch/re-upsert
scheduled orders (those are already correct), and does NOT touch /route (known-broken
Lambda, unrelated -- see CLAUDE.md).

Two of the four Aug-7 unscheduled orders found during root-causing this were
huutokaupat.com-tagged (see content.title.category) -- likely explains some/all of the
"Huutokaupat.com: 0 tagged orders" gap in BACKOFFICE_API_ISSUES.md; not re-verified here,
flagged as a follow-up.

Usage:
  export SUPABASE_URL="https://ybznbfezrdgzgptxkgul.supabase.co"
  export SUPABASE_SERVICE_KEY="..."
  export BACKOFFICE_API_URL="https://qtml5qv6uk.execute-api.eu-central-1.amazonaws.com/production/"
  export BACKOFFICE_API_KEY="..."
  python3 scripts/backfill_unscheduled_orders.py --start 2024-01-01 --end 2026-08-08
  python3 scripts/backfill_unscheduled_orders.py --start 2024-01-01 --end 2026-08-08 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta

CHUNK_DAYS = 30
MAX_TRIES = 5
BASE_BACKOFF = 2.0
UPSERT_BATCH_SIZE = 100


def daterange_chunks(start: date, end: date):
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=CHUNK_DAYS - 1), end)
        yield cur, chunk_end
        cur = chunk_end + timedelta(days=1)


def fetch_unscheduled(api_url: str, api_key: str, start: date, end: date) -> list[dict]:
    import urllib.parse

    qs = urllib.parse.urlencode({
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "includeUnscheduled": "true",
    })
    url = f"{api_url.rstrip('/')}/order?{qs}"
    last_err = None
    for attempt in range(1, MAX_TRIES + 1):
        req = urllib.request.Request(url, headers={"x-api-key": api_key})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                orders = json.loads(resp.read().decode("utf-8"))
                return [o for o in orders if not o.get("firstSchedule")]
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            last_err = e
            if attempt < MAX_TRIES:
                time.sleep(BASE_BACKOFF * (2 ** (attempt - 1)))
    raise RuntimeError(f"Failed fetching {start}..{end} after {MAX_TRIES} tries: {last_err}")


def ts_to_iso(val) -> str | None:
    if val in (None, "", 0):
        return None
    # A real ISO date has a '-' in the first 10 chars (2026-03-21...); a raw epoch-seconds
    # string (e.g. "1776321548.709699") does not, but its leading digits look enough like a
    # year to fool a bare val[:4].isdigit() check -- caught via a live 400 from Postgres.
    if isinstance(val, str) and ("T" in val or "-" in val[:10]):
        return val
    try:
        n = float(val)
    except (TypeError, ValueError):
        return None
    from datetime import datetime, timezone
    return datetime.fromtimestamp(n, tz=timezone.utc).isoformat()


def transform(o: dict) -> dict:
    return {
        "order_id": o.get("orderId"),
        "is_manual": False,
        "created_at": ts_to_iso(o.get("created") or o.get("createdAt")),
        "organization_name": o.get("organizationName"),
        "org_id": o.get("orgId"),
        "hub_id": o.get("hubId"),
        "order_state": o.get("orderState"),
        "order_type": o.get("orderType"),
        "origin": o.get("origin"),
        "first_schedule": None,
        "schedule": o.get("schedule"),
        "content": o.get("content"),
        "stops": o.get("stops"),
        "charge": o.get("charge"),
        "platform_fee": round(float(o["platformFee"])) if o.get("platformFee") not in (None, "") else None,
        "service_fee": round(float(o["serviceFee"])) if o.get("serviceFee") not in (None, "") else None,
        "route_id": o.get("routeId"),
        "commission_rate": float(o["commissionRate"]) if o.get("commissionRate") not in (None, "") else None,
        "underway_at": ts_to_iso(o.get("underwayAt")),
        "in_transit_at": ts_to_iso(o.get("inTransitAt")),
        "delivered_at": ts_to_iso(o.get("deliveredAt")),
        "review": o.get("review"),
        "manual_data": None,
        "is_asuntosaatio_gig": "No",
        "synced_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }


def upsert(supabase_url: str, service_key: str, rows: list[dict]) -> None:
    if not rows:
        return
    for i in range(0, len(rows), UPSERT_BATCH_SIZE):
        batch = rows[i:i + UPSERT_BATCH_SIZE]
        body = json.dumps(batch).encode("utf-8")
        req = urllib.request.Request(
            f"{supabase_url.rstrip('/')}/rest/v1/orders?on_conflict=order_id",
            data=body, method="POST",
            headers={
                "apikey": service_key,
                "Authorization": f"Bearer {service_key}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            resp.read()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYY-MM-DD")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    api_url = os.environ.get("BACKOFFICE_API_URL")
    api_key = os.environ.get("BACKOFFICE_API_KEY")
    supabase_url = os.environ.get("SUPABASE_URL")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not all([api_url, api_key, supabase_url, service_key]):
        print("ERROR: BACKOFFICE_API_URL, BACKOFFICE_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY must all be set.", file=sys.stderr)
        return 1

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    total_found = 0
    total_upserted = 0
    by_month: dict[str, int] = {}

    for chunk_start, chunk_end in daterange_chunks(start, end):
        unscheduled = fetch_unscheduled(api_url, api_key, chunk_start, chunk_end)
        total_found += len(unscheduled)
        for o in unscheduled:
            month = (ts_to_iso(o.get("created") or o.get("createdAt")) or "unknown")[:7]
            by_month[month] = by_month.get(month, 0) + 1

        if unscheduled and not args.dry_run:
            rows = [transform(o) for o in unscheduled]
            upsert(supabase_url, service_key, rows)
            total_upserted += len(rows)

        print(f"{chunk_start} .. {chunk_end}: {len(unscheduled)} unscheduled orders" + (" (dry-run, not written)" if args.dry_run else ""))

    print()
    print("By month (created_at):")
    for month in sorted(by_month):
        print(f"  {month}: {by_month[month]}")
    print()
    print(f"Total unscheduled orders found: {total_found}")
    if not args.dry_run:
        print(f"Total upserted: {total_upserted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
