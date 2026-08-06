#!/usr/bin/env python3
"""
One-time historical backfill for ads_google_campaign_daily via the Supermetrics API.

Ongoing sync is n8n-workflows/ads-google-daily.json (rolling last_30_days). This script
covers the gap behind that window -- run once, then it's done; no schedule, no N8N workflow.
See docs/STATUS.md workstream B for why this is API-direct rather than a Google Sheets read.

Usage (REST / service-key path):
  export SUPABASE_URL="https://ybznbfezrdgzgptxkgul.supabase.co"
  export SUPABASE_SERVICE_KEY="..."
  export SUPERMETRICS_API_KEY="API_..."
  python3 scripts/backfill_marketing_google_ads.py

Usage (Management API / access-token path -- no service key needed):
  export SUPABASE_ACCESS_TOKEN="..."
  export SUPERMETRICS_API_KEY="API_..."
  python3 scripts/backfill_marketing_google_ads.py --via sql

  python3 scripts/backfill_marketing_google_ads.py --start 2024-08-06 --end 2026-08-05 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

PROJECT_REF = "ybznbfezrdgzgptxkgul"
# Cloudflare WAF blocks the default urllib User-Agent with error 1010 on the Management API --
# see scripts/apply_supabase_migration.py and scripts/backfill_missing_months.py.
MGMT_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

SUPERMETRICS_URL = "https://api.supermetrics.com/enterprise/v2/query/data/keyjson"
DS_ID = "AW"
DS_ACCOUNTS = "list.all_accounts"
DS_USER = "jussi@apukuski.com"
FIELDS = "Date,CampaignID,Campaignname,Impressions,Clicks,Cost_eur,ConversionValue,Conversions"
MAX_ROWS = 50000  # confirmed headroom: 12mo at this grain = 2,693 rows (see docs/STATUS.md)

MAX_TRIES = 5
BASE_BACKOFF = 2.0
UPSERT_BATCH_SIZE = 100

COLUMNS = [
    "date", "campaign_id", "campaign_name", "impressions", "clicks",
    "cost_eur", "conversions", "conversion_value", "synced_at",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def fetch_supermetrics(api_key: str, start: date, end: date) -> list[dict]:
    body = json.dumps({
        "ds_id": DS_ID,
        "ds_accounts": DS_ACCOUNTS,
        "ds_user": DS_USER,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "fields": FIELDS,
        "max_rows": MAX_ROWS,
    }).encode("utf-8")

    last_err = None
    for attempt in range(1, MAX_TRIES + 1):
        req = urllib.request.Request(
            SUPERMETRICS_URL,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if not isinstance(data, list):
                    raise RuntimeError(f"unexpected non-list /keyjson response: {str(data)[:200]}")
                return data
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, RuntimeError) as e:
            last_err = e
            if attempt < MAX_TRIES:
                wait = BASE_BACKOFF * (2 ** (attempt - 1))
                print(f"  Supermetrics fetch attempt {attempt}/{MAX_TRIES} failed ({e}); retrying in {wait:.0f}s", file=sys.stderr)
                time.sleep(wait)
    raise RuntimeError(f"Supermetrics fetch failed after {MAX_TRIES} tries: {last_err}")


def to_number(val):
    if val in (None, ""):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def transform(d: dict) -> dict | None:
    row = {
        "date": d.get("Date"),
        "campaign_id": d.get("CampaignID"),
        "campaign_name": d.get("Campaignname") or None,
        "impressions": to_number(d.get("Impressions")),
        "clicks": to_number(d.get("Clicks")),
        "cost_eur": to_number(d.get("Cost_eur")),
        "conversions": to_number(d.get("Conversions")),
        "conversion_value": to_number(d.get("ConversionValue")),
        "synced_at": now_iso(),
    }
    if not row["date"] or not row["campaign_id"]:
        return None
    return row


def mgmt_sql(access_token: str, sql: str):
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
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "TRUE" if val else "FALSE"
    if isinstance(val, (int, float)):
        return repr(val)
    escaped = str(val).replace("'", "''")
    return f"'{escaped}'"


def sql_upsert(access_token: str, rows: list[dict]) -> None:
    for i in range(0, len(rows), UPSERT_BATCH_SIZE):
        batch = rows[i : i + UPSERT_BATCH_SIZE]
        values_sql = ",\n".join(
            "(" + ", ".join(sql_literal(r.get(c)) for c in COLUMNS) + ")"
            for r in batch
        )
        update_sql = ", ".join(f"{c} = EXCLUDED.{c}" for c in COLUMNS if c not in ("date", "campaign_id"))
        sql = (
            f"INSERT INTO ads_google_campaign_daily ({', '.join(COLUMNS)})\nVALUES\n{values_sql}\n"
            f"ON CONFLICT (date, campaign_id) DO UPDATE SET {update_sql};"
        )
        mgmt_sql(access_token, sql)


def sql_log_sync(access_token: str, date_range: str, rows_upserted: int,
                  status: str, error_message: str | None) -> None:
    sql = (
        "INSERT INTO sync_log (workflow, started_at, completed_at, date_range, rows_upserted, status, error_message)\n"
        f"VALUES ({sql_literal('marketing-backfill-google-ads')}, NULL, now(), {sql_literal(date_range)}, "
        f"{sql_literal(rows_upserted)}, {sql_literal(status)}, {sql_literal(error_message)});"
    )
    mgmt_sql(access_token, sql)


def supabase_upsert(base_url: str, service_key: str, rows: list[dict]) -> None:
    for i in range(0, len(rows), UPSERT_BATCH_SIZE):
        batch = rows[i : i + UPSERT_BATCH_SIZE]
        req = urllib.request.Request(
            f"{base_url}/rest/v1/ads_google_campaign_daily",
            data=json.dumps(batch).encode("utf-8"),
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


def supabase_log_sync(base_url: str, service_key: str, date_range: str,
                       rows_upserted: int, status: str, error_message: str | None) -> None:
    body = json.dumps([{
        "workflow": "marketing-backfill-google-ads",
        "started_at": None,
        "completed_at": now_iso(),
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


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", help="YYYY-MM-DD, default 2 years before --end")
    p.add_argument("--end", help="YYYY-MM-DD, default yesterday")
    p.add_argument("--dry-run", action="store_true", help="Fetch and transform but do not write to Supabase")
    p.add_argument("--via", choices=["rest", "sql"], default="rest",
                    help="rest = PostgREST with SUPABASE_SERVICE_KEY; sql = Management API with SUPABASE_ACCESS_TOKEN")
    args = p.parse_args()

    end = date.fromisoformat(args.end) if args.end else date.today() - timedelta(days=1)
    start = date.fromisoformat(args.start) if args.start else end - timedelta(days=730)
    date_range = f"{start.isoformat()}_{end.isoformat()}"

    api_key = os.environ.get("SUPERMETRICS_API_KEY")
    if not api_key:
        print("ERROR: SUPERMETRICS_API_KEY is not set.", file=sys.stderr)
        return 1

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

    def log_sync(rows_upserted: int, status: str, error_message: str | None) -> None:
        if args.dry_run:
            return
        if args.via == "sql":
            sql_log_sync(creds["access_token"], date_range, rows_upserted, status, error_message)
        else:
            supabase_log_sync(creds["base_url"], creds["service_key"], date_range, rows_upserted, status, error_message)

    print(f"Fetching Google Ads {date_range} from Supermetrics...")
    try:
        raw_rows = fetch_supermetrics(api_key, start, end)
    except RuntimeError as e:
        print(f"FETCH FAILED: {e}", file=sys.stderr)
        log_sync(0, "failed", str(e))
        return 1

    rows = [r for r in (transform(d) for d in raw_rows) if r]
    dropped = len(raw_rows) - len(rows)
    if dropped:
        print(f"WARNING: {dropped} row(s) had no date/campaign_id and were dropped", file=sys.stderr)

    print(f"Fetched {len(raw_rows)} raw rows, {len(rows)} transformable.")

    if args.dry_run:
        print(f"[dry-run] would upsert {len(rows)} rows into ads_google_campaign_daily")
        return 0

    try:
        if rows:
            if args.via == "sql":
                sql_upsert(creds["access_token"], rows)
            else:
                supabase_upsert(creds["base_url"], creds["service_key"], rows)
        log_sync(len(rows), "success", None)
        print(f"OK: upserted {len(rows)} rows.")
        return 0
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, RuntimeError) as e:
        detail = e.read().decode()[:500] if isinstance(e, urllib.error.HTTPError) else str(e)
        print(f"UPSERT FAILED: {detail}", file=sys.stderr)
        log_sync(0, "failed", detail)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
