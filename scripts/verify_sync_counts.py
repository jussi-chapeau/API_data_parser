#!/usr/bin/env python3
"""
Compare Supabase monthly order counts against the live Backoffice API.

Live counts are fetched day-by-day (not one multi-day range) with exponential
backoff, because 30-day ranges intermittently 500/504 on this API (~47%
failure rate observed on 2025-11..2026-04) and a naive single request would
just reproduce the same undercount this script exists to catch.

Usage:
  export SUPABASE_URL="https://ybznbfezrdgzgptxkgul.supabase.co"
  export SUPABASE_SERVICE_KEY="..."
  python3 scripts/verify_sync_counts.py --start 2024-01 --end 2026-07
  python3 scripts/verify_sync_counts.py --start 2025-11 --end 2026-04 --output data/gap_check.json
"""

from __future__ import annotations

import argparse
import calendar
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta

API_BASE = "https://qtml5qv6uk.execute-api.eu-central-1.amazonaws.com/production"
API_KEY = "oz6Dcgxn5k3px6a3fN9NU5YHshMuYpxP4NNyMdi4"
PROJECT_REF = "ybznbfezrdgzgptxkgul"
MGMT_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
MAX_TRIES = 6
BASE_BACKOFF = 1.0


def mgmt_sql(access_token: str, sql: str):
    body = json.dumps({"query": sql}).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{PROJECT_REF}/database/query",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "User-Agent": MGMT_UA,
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else []


def sql_month_count(access_token: str, y: int, m: int) -> int:
    start, end = month_bounds(y, m)
    end_exclusive = end + timedelta(days=1)
    sql = (
        f"select count(*) from orders where created_at >= '{start.isoformat()}' "
        f"and created_at < '{end_exclusive.isoformat()}';"
    )
    result = mgmt_sql(access_token, sql)
    return int(result[0]["count"])


def month_bounds(y: int, m: int) -> tuple[date, date]:
    last_day = calendar.monthrange(y, m)[1]
    return date(y, m, 1), date(y, m, last_day)


def month_range(start_label: str, end_label: str):
    sy, sm = (int(x) for x in start_label.split("-"))
    ey, em = (int(x) for x in end_label.split("-"))
    y, m = sy, sm
    while (y, m) <= (ey, em):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


def daterange(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def fetch_count_with_backoff(ep: str, day: date) -> int:
    url = f"{API_BASE}{ep}?start_date={day.isoformat()}&end_date={day.isoformat()}"
    last_err = None
    for attempt in range(1, MAX_TRIES + 1):
        req = urllib.request.Request(url, headers={"x-api-key": API_KEY})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if not isinstance(data, list):
                    raise RuntimeError(f"unexpected response: {str(data)[:200]}")
                return len(data)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, RuntimeError) as e:
            last_err = e
            if attempt < MAX_TRIES:
                time.sleep(BASE_BACKOFF * (2 ** (attempt - 1)))
    raise RuntimeError(f"{ep} {day} failed after {MAX_TRIES} tries: {last_err}")


def live_month_count(y: int, m: int) -> int:
    start, end = month_bounds(y, m)
    total = 0
    for day in daterange(start, end):
        total += fetch_count_with_backoff("/order", day)
        total += fetch_count_with_backoff("/manual-order", day)
    return total


def supabase_month_count(base_url: str, service_key: str, y: int, m: int) -> int:
    start, end = month_bounds(y, m)
    end_exclusive = end + timedelta(days=1)
    url = (
        f"{base_url}/rest/v1/orders"
        f"?created_at=gte.{start.isoformat()}T00:00:00Z"
        f"&created_at=lt.{end_exclusive.isoformat()}T00:00:00Z"
        f"&select=order_id"
    )
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Prefer": "count=exact",
            "Range-Unit": "items",
            "Range": "0-0",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        content_range = resp.headers.get("Content-Range", "")
        resp.read()
    # Content-Range format: "0-0/<total>"
    if "/" in content_range:
        total = content_range.split("/")[-1]
        if total.isdigit():
            return int(total)
    raise RuntimeError(f"could not parse Content-Range: {content_range!r}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True, help="YYYY-MM")
    p.add_argument("--end", required=True, help="YYYY-MM")
    p.add_argument("--via", choices=["rest", "sql"], default="rest",
                    help="rest = PostgREST with SUPABASE_SERVICE_KEY; sql = Management API with SUPABASE_ACCESS_TOKEN")
    p.add_argument("--output", default=None)
    args = p.parse_args()

    base_url = os.environ.get("SUPABASE_URL", "https://ybznbfezrdgzgptxkgul.supabase.co")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY")
    access_token = os.environ.get("SUPABASE_ACCESS_TOKEN")
    if args.via == "sql" and not access_token:
        print("ERROR: SUPABASE_ACCESS_TOKEN is required for --via sql", file=sys.stderr)
        return 1
    if args.via == "rest" and not service_key:
        print("ERROR: SUPABASE_SERVICE_KEY is required for --via rest (or use --via sql)", file=sys.stderr)
        return 1

    rows = []
    gaps = []
    for y, m in month_range(args.start, args.end):
        label = f"{y:04d}-{m:02d}"
        try:
            live = live_month_count(y, m)
        except RuntimeError as e:
            print(f"{label}: LIVE FETCH FAILED: {e}", file=sys.stderr)
            rows.append({"month": label, "live": None, "supabase": None, "missing": None, "error": str(e)})
            continue
        supa = sql_month_count(access_token, y, m) if args.via == "sql" else supabase_month_count(base_url, service_key, y, m)
        missing = live - supa
        status = "OK" if missing == 0 else f"MISSING {missing}"
        print(f"{label}: live={live:5d}  supabase={supa:5d}  {status}")
        rows.append({"month": label, "live": live, "supabase": supa, "missing": missing})
        if missing != 0:
            gaps.append(label)

    summary = {"months_checked": len(rows), "months_with_gaps": gaps, "rows": rows}
    text = json.dumps(summary, indent=2)
    if args.output:
        with open(args.output, "w") as f:
            f.write(text)
        print(f"\nWrote {args.output}")

    print(f"\n--- {len(rows)} months checked, {len(gaps)} with gaps ---")
    if gaps:
        print("Gap months:", gaps)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
