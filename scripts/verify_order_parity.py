#!/usr/bin/env python3
"""
Per-month ID-level parity check: Supabase `orders` vs the live Backoffice API.

Deliberately NOT a count comparison. scripts/verify_sync_counts.py compares counts, and that
is precisely why one missing May 2026 order went unnoticed for three months -- a difference
of 1 is indistinguishable from ordinary sync lag when all you have is two integers. Comparing
actual order_id sets tells you *which* rows differ and in which direction, which is the
difference between "probably fine" and a specific row you can go fix.

Fetches day-by-day (30-day /order windows fail ~47% of the time -- BACKOFFICE_API_ISSUES.md
#7), passes includeUnscheduled=true to /order but not /manual-order (400s -- GOTCHAS.md), and
retries on the known intermittent 500s. Read-only: never writes, never deletes.

Usage:
  export SUPABASE_URL=... SUPABASE_SERVICE_KEY=...
  export BACKOFFICE_API_URL=... BACKOFFICE_API_KEY=...
  python3 scripts/verify_order_parity.py --start 2026-04 --end 2026-08
  python3 scripts/verify_order_parity.py --start 2026-08 --end 2026-08 --show-ids
"""

from __future__ import annotations

import argparse
import calendar
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

MAX_TRIES = 6
BASE_BACKOFF = 1.0


def _get(url: str, headers: dict, timeout: int = 60):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def live_ids_for_day(api: str, key: str, day: date) -> set[str]:
    ids = set()
    for path, extra in (("order", {"includeUnscheduled": "true"}), ("manual-order", {})):
        qs = urllib.parse.urlencode({"start_date": day.isoformat(),
                                     "end_date": day.isoformat(), **extra})
        url = f"{api.rstrip('/')}/{path}?{qs}"
        last = None
        for attempt in range(1, MAX_TRIES + 1):
            try:
                data = _get(url, {"x-api-key": key}, timeout=30)
                if not isinstance(data, list):
                    raise RuntimeError("unexpected shape")
                ids |= {o["orderId"] for o in data if o.get("orderId")}
                break
            except Exception as e:  # noqa: BLE001 -- retry everything, then fail loudly
                last = e
                if attempt < MAX_TRIES:
                    time.sleep(BASE_BACKOFF * (2 ** (attempt - 1)))
        else:
            raise RuntimeError(f"/{path} {day} failed after {MAX_TRIES} tries: {last}")
    return ids


def supabase_ids_for_month(base: str, key: str, start: date, end_excl: date) -> set[str]:
    """Paginated: PostgREST caps at 1000 rows per request and a busy month exceeds that."""
    ids, offset, page = set(), 0, 1000
    while True:
        url = (f"{base.rstrip('/')}/rest/v1/orders"
               f"?created_at=gte.{start.isoformat()}T00:00:00Z"
               f"&created_at=lt.{end_excl.isoformat()}T00:00:00Z"
               f"&select=order_id&order=order_id.asc&limit={page}&offset={offset}")
        rows = _get(url, {"apikey": key, "Authorization": f"Bearer {key}"})
        ids |= {r["order_id"] for r in rows}
        if len(rows) < page:
            return ids
        offset += page


def months(start_label: str, end_label: str):
    sy, sm = (int(x) for x in start_label.split("-"))
    ey, em = (int(x) for x in end_label.split("-"))
    y, m = sy, sm
    while (y, m) <= (ey, em):
        yield y, m
        m = 1 if m == 12 else m + 1
        y = y + 1 if m == 1 else y


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True, help="YYYY-MM")
    p.add_argument("--end", required=True, help="YYYY-MM")
    p.add_argument("--show-ids", action="store_true", help="print the differing order_ids")
    args = p.parse_args()

    api, akey = os.environ.get("BACKOFFICE_API_URL"), os.environ.get("BACKOFFICE_API_KEY")
    su, skey = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not all([api, akey, su, skey]):
        print("ERROR: BACKOFFICE_API_URL/KEY and SUPABASE_URL/SERVICE_KEY required.",
              file=sys.stderr)
        return 2

    print(f"{'Month':<9}{'AWS':>7}{'Supabase':>10}{'Extra':>8}{'Missing':>9}   verdict")
    print("-" * 60)
    total_extra = total_missing = 0
    detail = {}

    for y, m in months(args.start, args.end):
        first = date(y, m, 1)
        last = date(y, m, calendar.monthrange(y, m)[1])
        live = set()
        d = first
        while d <= last:
            live |= live_ids_for_day(api, akey, d)
            d += timedelta(days=1)
        supa = supabase_ids_for_month(su, skey, first, last + timedelta(days=1))

        extra, missing = supa - live, live - supa
        total_extra += len(extra)
        total_missing += len(missing)
        detail[f"{y}-{m:02d}"] = {"extra": sorted(extra), "missing": sorted(missing)}
        verdict = "OK" if not extra and not missing else "DRIFT"
        print(f"{y}-{m:02d}{len(live):>10}{len(supa):>10}{len(extra):>8}{len(missing):>9}   {verdict}")
        if args.show_ids:
            for oid in sorted(extra):
                print(f"           extra (Supabase-only): {oid}")
            for oid in sorted(missing):
                print(f"           missing (Backoffice-only): {oid}")

    print("-" * 60)
    print(f"TOTAL drift: {total_extra} extra, {total_missing} missing")
    if total_extra == 0 and total_missing == 0:
        print("PARITY: Supabase matches Backoffice 1:1 across the checked range.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
