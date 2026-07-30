#!/usr/bin/env python3
"""
Day-by-day backfill for months where Supabase undercounts the live Backoffice API.

Root cause being patched around: the N8N sync workflows had zero retry on the
Backoffice API's intermittent 500/504, and Log Sync hardcoded status='success'
regardless of outcome, so failed chunks silently never landed in Supabase and
no one could tell from sync_log. This script re-fetches day-by-day (smaller
blast radius per request than the 30-day chunks backfill.json uses) with real
exponential backoff, upserts in small batches, and writes an honest sync_log
row per day — failures are logged as 'failed', not swallowed.

Usage (REST / service-key path):
  export SUPABASE_URL="https://ybznbfezrdgzgptxkgul.supabase.co"
  export SUPABASE_SERVICE_KEY="..."
  python3 scripts/backfill_missing_months.py --months 2025-11,2025-12,2026-01,2026-02,2026-04

Usage (Management API / access-token path -- no service key needed):
  export SUPABASE_ACCESS_TOKEN="..."
  python3 scripts/backfill_missing_months.py --months 2025-11,2025-12,2026-01,2026-02,2026-04 --via sql

  python3 scripts/backfill_missing_months.py --start 2025-11-01 --end 2025-11-30 --dry-run
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
from datetime import date, datetime, timedelta, timezone

API_BASE = "https://qtml5qv6uk.execute-api.eu-central-1.amazonaws.com/production"
API_KEY = "oz6Dcgxn5k3px6a3fN9NU5YHshMuYpxP4NNyMdi4"
PROJECT_REF = "ybznbfezrdgzgptxkgul"
MGMT_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

MAX_TRIES = 6
BASE_BACKOFF = 1.0  # seconds; doubles each retry -> 1,2,4,8,16,32
UPSERT_BATCH_SIZE = 100


def mgmt_sql(access_token: str, sql: str):
    """Run raw SQL via the Supabase Management API (requires a browser-like
    User-Agent -- Cloudflare WAF blocks the default urllib UA with a 403).
    Retries with backoff: this endpoint times out intermittently too."""
    body = json.dumps({"query": sql}).encode("utf-8")
    last_err = None
    for attempt in range(1, MAX_TRIES + 1):
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
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else []
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
            last_err = e
            if attempt < MAX_TRIES:
                wait = BASE_BACKOFF * (2 ** (attempt - 1))
                print(f"    mgmt_sql attempt {attempt}/{MAX_TRIES} failed ({e}); retrying in {wait:.0f}s", file=sys.stderr)
                time.sleep(wait)
    raise RuntimeError(f"mgmt_sql failed after {MAX_TRIES} tries: {last_err}")


def sql_literal(val) -> str:
    """Render a Python value as a PostgreSQL literal. Standard single-quote
    doubling; standard_conforming_strings is on by default so backslashes are
    literal, not escapes -- doubling single quotes alone is sufficient."""
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


def month_bounds(label: str) -> tuple[date, date]:
    y, m = (int(x) for x in label.split("-"))
    last_day = calendar.monthrange(y, m)[1]
    return date(y, m, 1), date(y, m, last_day)


def daterange(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def fetch_with_backoff(ep: str, day: date) -> list[dict]:
    url = f"{API_BASE}{ep}?start_date={day.isoformat()}&end_date={day.isoformat()}"
    last_err = None
    for attempt in range(1, MAX_TRIES + 1):
        req = urllib.request.Request(url, headers={"x-api-key": API_KEY})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if not isinstance(data, list):
                    raise RuntimeError(f"unexpected non-list response: {str(data)[:200]}")
                return data
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, RuntimeError) as e:
            last_err = e
            if attempt < MAX_TRIES:
                wait = BASE_BACKOFF * (2 ** (attempt - 1))
                print(f"    {ep} {day} attempt {attempt}/{MAX_TRIES} failed ({e}); retrying in {wait:.0f}s", file=sys.stderr)
                time.sleep(wait)
    raise RuntimeError(f"{ep} {day} failed after {MAX_TRIES} tries: {last_err}")


def ts_to_iso(val) -> str | None:
    if val in (None, ""):
        return None
    s = str(val)
    if "T" in s or (len(s) >= 4 and s[:4].isdigit() and s[4:5] == "-"):
        return s
    try:
        n = float(val)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(n, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def fee_to_cents(val):
    if val in (None, ""):
        return None
    try:
        return round(float(val))
    except (TypeError, ValueError):
        return None


def parse_manual_total_incl_vat(charge: dict | None):
    if not charge or charge.get("charge") in (None, ""):
        return None, None
    raw = str(charge["charge"]).strip().replace(" ", "").replace(",", ".")
    import re
    if not re.fullmatch(r"[-+]?\d+(\.\d+)?", raw):
        return None, None
    n = float(raw)
    return n, round(n * 100)


def is_manual_order(d: dict) -> bool:
    if d.get("isManual") in (True, "true"):
        return True
    if d.get("customer") is not None and d.get("additionalInfo") is not None:
        return True
    charge = d.get("charge") or {}
    if (
        d.get("orgId") is not None
        and charge.get("charge") is not None
        and charge.get("workPrice") is None
    ):
        return True
    return False


def transform(d: dict) -> dict | None:
    is_manual = is_manual_order(d)
    charge = d.get("charge") or {}

    manual_data = None
    platform_fee = None
    service_fee = None

    if is_manual:
        total_eur, total_cents = parse_manual_total_incl_vat(charge)
        manual_data = {
            "customer": d.get("customer"),
            "additionalInfo": d.get("additionalInfo"),
            "serviceFeeApplied": d.get("serviceFeeApplied"),
            "payment_method": charge.get("paymentMethod"),
            "total_incl_vat_eur": total_eur,
            "total_incl_vat_cents": total_cents,
            "price_basis": "gross_incl_vat",
            "source_field": "charge.charge",
            "raw_charge_total": str(charge["charge"]) if charge.get("charge") is not None else None,
        }
    else:
        platform_fee = fee_to_cents(d.get("platformFee"))
        service_fee = fee_to_cents(d.get("serviceFee"))

    commission_rate = d.get("commissionRate")

    row = {
        "order_id": d.get("orderId"),
        "is_manual": is_manual,
        "created_at": ts_to_iso(d.get("created") or d.get("createdAt")),
        "organization_name": d.get("organizationName"),
        "org_id": d.get("orgId"),
        "hub_id": d.get("hubId"),
        "order_state": d.get("orderState"),
        "order_type": d.get("orderType"),
        "origin": None if is_manual else d.get("origin"),
        "first_schedule": ts_to_iso(d.get("firstSchedule")),
        "schedule": d.get("schedule"),
        "content": d.get("content"),
        "stops": d.get("stops"),
        "charge": charge or None,
        "platform_fee": platform_fee,
        "service_fee": service_fee,
        "route_id": d.get("routeId"),
        "commission_rate": float(commission_rate) if commission_rate not in (None, "") else None,
        "underway_at": ts_to_iso(d.get("underwayAt")),
        "in_transit_at": ts_to_iso(d.get("inTransitAt")),
        "delivered_at": ts_to_iso(d.get("deliveredAt")),
        "review": d.get("review"),
        "manual_data": manual_data,
        "synced_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if not row["order_id"]:
        return None
    return row


ORDERS_COLUMNS = [
    "order_id", "is_manual", "created_at", "organization_name", "org_id", "hub_id",
    "order_state", "order_type", "origin", "first_schedule", "schedule", "content",
    "stops", "charge", "platform_fee", "service_fee", "route_id", "commission_rate",
    "underway_at", "in_transit_at", "delivered_at", "review", "manual_data", "synced_at",
]


def sql_upsert(access_token: str, rows: list[dict]) -> None:
    for i in range(0, len(rows), UPSERT_BATCH_SIZE):
        batch = rows[i : i + UPSERT_BATCH_SIZE]
        values_sql = ",\n".join(
            "(" + ", ".join(sql_literal(r.get(c)) for c in ORDERS_COLUMNS) + ")"
            for r in batch
        )
        update_sql = ", ".join(f"{c} = EXCLUDED.{c}" for c in ORDERS_COLUMNS if c != "order_id")
        sql = (
            f"INSERT INTO orders ({', '.join(ORDERS_COLUMNS)})\nVALUES\n{values_sql}\n"
            f"ON CONFLICT (order_id) DO UPDATE SET {update_sql};"
        )
        mgmt_sql(access_token, sql)


def sql_log_sync(access_token: str, workflow: str, date_range: str,
                  rows_upserted: int, status: str, error_message: str | None) -> None:
    sql = (
        "INSERT INTO sync_log (workflow, started_at, completed_at, date_range, rows_upserted, status, error_message)\n"
        f"VALUES ({sql_literal(workflow)}, NULL, now(), {sql_literal(date_range)}, "
        f"{sql_literal(rows_upserted)}, {sql_literal(status)}, {sql_literal(error_message)});"
    )
    mgmt_sql(access_token, sql)


def supabase_upsert(base_url: str, service_key: str, rows: list[dict]) -> None:
    for i in range(0, len(rows), UPSERT_BATCH_SIZE):
        batch = rows[i : i + UPSERT_BATCH_SIZE]
        body = json.dumps(batch).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/rest/v1/orders",
            data=body,
            method="POST",
            headers={
                "apikey": service_key,
                "Authorization": f"Bearer {service_key}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            resp.read()


def supabase_log_sync(base_url: str, service_key: str, workflow: str, date_range: str,
                       rows_upserted: int, status: str, error_message: str | None) -> None:
    body = json.dumps([{
        "workflow": workflow,
        "started_at": None,
        "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "date_range": date_range,
        "rows_upserted": rows_upserted,
        "status": status,
        "error_message": error_message,
    }]).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/rest/v1/sync_log",
        data=body,
        method="POST",
        headers={
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


def backfill_day(day: date, creds: dict, via: str, dry_run: bool) -> dict:
    def log_sync(rows_upserted: int, status: str, error_message: str | None) -> None:
        if dry_run:
            return
        try:
            if via == "sql":
                sql_log_sync(creds["access_token"], "manual-backfill", day.isoformat(), rows_upserted, status, error_message)
            else:
                supabase_log_sync(creds["base_url"], creds["service_key"], "manual-backfill", day.isoformat(), rows_upserted, status, error_message)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, RuntimeError) as e:
            # Never let a logging failure crash the backfill itself -- the stderr line and the
            # returned per-day result dict are still authoritative even if sync_log missed this one.
            print(f"  {day}: WARNING could not write sync_log: {e}", file=sys.stderr)

    try:
        orders = fetch_with_backoff("/order", day)
        manual = fetch_with_backoff("/manual-order", day)
    except RuntimeError as e:
        print(f"  {day}: FETCH FAILED after retries: {e}", file=sys.stderr)
        log_sync(0, "failed", str(e))
        return {"day": day.isoformat(), "status": "failed", "error": str(e), "fetched": 0, "upserted": 0}

    rows = []
    for d in orders:
        r = transform(d)
        if r:
            rows.append(r)
    for d in manual:
        r = transform(d)
        if r:
            rows.append(r)

    fetched = len(orders) + len(manual)
    dropped = fetched - len(rows)
    if dropped:
        print(f"  {day}: WARNING {dropped} record(s) had no orderId and were dropped", file=sys.stderr)

    if dry_run:
        print(f"  {day}: [dry-run] would upsert {len(rows)} rows ({len(orders)} platform + {len(manual)} manual)")
        return {"day": day.isoformat(), "status": "dry_run", "fetched": fetched, "upserted": len(rows)}

    try:
        if rows:
            if via == "sql":
                sql_upsert(creds["access_token"], rows)
            else:
                supabase_upsert(creds["base_url"], creds["service_key"], rows)
        log_sync(len(rows), "success", None)
        print(f"  {day}: OK upserted {len(rows)} rows ({len(orders)} platform + {len(manual)} manual)")
        return {"day": day.isoformat(), "status": "success", "fetched": fetched, "upserted": len(rows)}
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, RuntimeError) as e:
        detail = e.read().decode()[:300] if isinstance(e, urllib.error.HTTPError) else str(e)
        print(f"  {day}: UPSERT FAILED: {detail}", file=sys.stderr)
        log_sync(0, "failed", detail)
        return {"day": day.isoformat(), "status": "upsert_failed", "error": detail, "fetched": fetched, "upserted": 0}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--months", help="Comma-separated YYYY-MM list, e.g. 2025-11,2025-12,2026-01,2026-02,2026-04")
    p.add_argument("--start", help="YYYY-MM-DD (alternative to --months)")
    p.add_argument("--end", help="YYYY-MM-DD (alternative to --months)")
    p.add_argument("--dry-run", action="store_true", help="Fetch and transform but do not write to Supabase")
    p.add_argument("--via", choices=["rest", "sql"], default="rest",
                    help="rest = PostgREST with SUPABASE_SERVICE_KEY; sql = Management API with SUPABASE_ACCESS_TOKEN")
    p.add_argument("--output", default=None, help="Path to write JSON summary")
    args = p.parse_args()

    creds: dict = {}
    if not args.dry_run:
        if args.via == "sql":
            access_token = os.environ.get("SUPABASE_ACCESS_TOKEN")
            if not access_token:
                print("ERROR: SUPABASE_ACCESS_TOKEN is required for --via sql", file=sys.stderr)
                return 1
            creds["access_token"] = access_token
        else:
            base_url = os.environ.get("SUPABASE_URL", "https://ybznbfezrdgzgptxkgul.supabase.co")
            service_key = os.environ.get("SUPABASE_SERVICE_KEY")
            if not service_key:
                print("ERROR: SUPABASE_SERVICE_KEY is required for --via rest (or use --via sql / --dry-run)", file=sys.stderr)
                return 1
            creds["base_url"] = base_url
            creds["service_key"] = service_key

    days: list[date] = []
    if args.months:
        for label in args.months.split(","):
            start, end = month_bounds(label.strip())
            days.extend(daterange(start, end))
    elif args.start and args.end:
        days.extend(daterange(date.fromisoformat(args.start), date.fromisoformat(args.end)))
    else:
        print("ERROR: pass --months or --start/--end", file=sys.stderr)
        return 1

    results = []
    for day in days:
        results.append(backfill_day(day, creds, args.via, args.dry_run))

    summary = {
        "days_processed": len(results),
        "failed_days": [r["day"] for r in results if r["status"] in ("failed", "upsert_failed")],
        "total_upserted": sum(r["upserted"] for r in results),
        "results": results,
    }
    text = json.dumps(summary, indent=2)
    if args.output:
        with open(args.output, "w") as f:
            f.write(text)
        print(f"\nWrote {args.output}")
    print(f"\n--- SUMMARY: {summary['days_processed']} days, "
          f"{summary['total_upserted']} rows upserted, "
          f"{len(summary['failed_days'])} day(s) failed ---")
    if summary["failed_days"]:
        print("Failed days:", summary["failed_days"])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
