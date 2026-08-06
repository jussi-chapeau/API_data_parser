#!/usr/bin/env python3
"""
One-time historical backfill for ads_meta_placement_daily via the Supermetrics API.

Ongoing sync is n8n-workflows/ads-meta-daily.json (rolling last_30_days). This script
covers the gap behind that window -- run once, then it's done; no schedule, no N8N workflow.
See docs/STATUS.md workstream B for why this is API-direct rather than a Google Sheets read.

Meta ad spend has been paused since May 2026 (confirmed, not a sync bug -- see STATUS.md),
so expect near-zero rows for the paused period; that's real, not a script error.

Usage (REST / service-key path):
  export SUPABASE_URL="https://ybznbfezrdgzgptxkgul.supabase.co"
  export SUPABASE_SERVICE_KEY="..."
  export SUPERMETRICS_API_KEY="API_..."
  python3 scripts/backfill_marketing_meta.py

Usage (Management API / access-token path -- no service key needed):
  export SUPABASE_ACCESS_TOKEN="..."
  export SUPERMETRICS_API_KEY="API_..."
  python3 scripts/backfill_marketing_meta.py --via sql

  python3 scripts/backfill_marketing_meta.py --start 2024-08-06 --end 2026-08-05 --dry-run
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
DS_ID = "FA"
DS_ACCOUNTS = "list.all_accounts"
DS_USER = "10166639489569112"
FIELDS = (
    "Date,adcampaign_id,adcampaign_name,placement,cost_eur,impressions,"
    "unique_action_link_click,offsite_conversions,offsite_conversion_value,"
    "offsite_conversions_fb_pixel_purchase"
)
MAX_ROWS = 100000  # confirmed headroom: 12mo at placement grain = 18,641 rows (see docs/STATUS.md)

MAX_TRIES = 5
BASE_BACKOFF = 2.0
UPSERT_BATCH_SIZE = 100
# A single 2-year query at this placement grain gets a real 500 from Supermetrics (not a
# client timeout -- confirmed the identical 12-month range works fine and returns in ~9s, so
# a 2x-larger request wouldn't plausibly hit our own 180s timeout). Chunking into windows
# comfortably under the size that failed. Google Ads doesn't need this: campaign grain over
# the full 2 years is only ~4.8k rows and succeeds as a single call.
CHUNK_DAYS = 90

COLUMNS = [
    "date", "campaign_id", "campaign_name", "placement", "impressions", "cost_eur",
    "link_clicks", "website_conversions", "website_conversion_value", "website_purchases",
    "synced_at",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def chunk_ranges(start: date, end: date, days: int):
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=days - 1), end)
        yield cur, chunk_end
        cur = chunk_end + timedelta(days=1)


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
        "campaign_id": d.get("adcampaign_id"),
        "campaign_name": d.get("adcampaign_name") or None,
        "placement": d.get("placement"),
        "impressions": to_number(d.get("impressions")),
        "cost_eur": to_number(d.get("cost_eur")),
        "link_clicks": to_number(d.get("unique_action_link_click")),
        "website_conversions": to_number(d.get("offsite_conversions")),
        "website_conversion_value": to_number(d.get("offsite_conversion_value")),
        "website_purchases": to_number(d.get("offsite_conversions_fb_pixel_purchase")),
        "synced_at": now_iso(),
    }
    if not row["date"] or not row["campaign_id"] or not row["placement"]:
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
        update_sql = ", ".join(f"{c} = EXCLUDED.{c}" for c in COLUMNS if c not in ("date", "campaign_id", "placement"))
        sql = (
            f"INSERT INTO ads_meta_placement_daily ({', '.join(COLUMNS)})\nVALUES\n{values_sql}\n"
            f"ON CONFLICT (date, campaign_id, placement) DO UPDATE SET {update_sql};"
        )
        mgmt_sql(access_token, sql)


def sql_log_sync(access_token: str, date_range: str, rows_upserted: int,
                  status: str, error_message: str | None) -> None:
    sql = (
        "INSERT INTO sync_log (workflow, started_at, completed_at, date_range, rows_upserted, status, error_message)\n"
        f"VALUES ({sql_literal('marketing-backfill-meta')}, NULL, now(), {sql_literal(date_range)}, "
        f"{sql_literal(rows_upserted)}, {sql_literal(status)}, {sql_literal(error_message)});"
    )
    mgmt_sql(access_token, sql)


def supabase_upsert(base_url: str, service_key: str, rows: list[dict]) -> None:
    for i in range(0, len(rows), UPSERT_BATCH_SIZE):
        batch = rows[i : i + UPSERT_BATCH_SIZE]
        req = urllib.request.Request(
            f"{base_url}/rest/v1/ads_meta_placement_daily",
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
        "workflow": "marketing-backfill-meta",
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

    def log_sync(chunk_label: str, rows_upserted: int, status: str, error_message: str | None) -> None:
        if args.dry_run:
            return
        if args.via == "sql":
            sql_log_sync(creds["access_token"], chunk_label, rows_upserted, status, error_message)
        else:
            supabase_log_sync(creds["base_url"], creds["service_key"], chunk_label, rows_upserted, status, error_message)

    chunks = list(chunk_ranges(start, end, CHUNK_DAYS))
    print(f"Fetching Meta {date_range} from Supermetrics in {len(chunks)} chunk(s) of ~{CHUNK_DAYS} days...")

    total_upserted = 0
    failed_chunks = []

    for chunk_start, chunk_end in chunks:
        chunk_label = f"{chunk_start.isoformat()}_{chunk_end.isoformat()}"
        try:
            raw_rows = fetch_supermetrics(api_key, chunk_start, chunk_end)
        except RuntimeError as e:
            print(f"  {chunk_label}: FETCH FAILED: {e}", file=sys.stderr)
            failed_chunks.append(chunk_label)
            log_sync(chunk_label, 0, "failed", str(e))
            continue

        rows = [r for r in (transform(d) for d in raw_rows) if r]
        dropped = len(raw_rows) - len(rows)
        if dropped:
            print(f"  {chunk_label}: WARNING {dropped} row(s) had no date/campaign_id/placement and were dropped", file=sys.stderr)

        if args.dry_run:
            print(f"  {chunk_label}: [dry-run] {len(raw_rows)} raw, would upsert {len(rows)} rows")
            total_upserted += len(rows)
            continue

        try:
            if rows:
                if args.via == "sql":
                    sql_upsert(creds["access_token"], rows)
                else:
                    supabase_upsert(creds["base_url"], creds["service_key"], rows)
            log_sync(chunk_label, len(rows), "success", None)
            print(f"  {chunk_label}: OK upserted {len(rows)} rows")
            total_upserted += len(rows)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, RuntimeError) as e:
            detail = e.read().decode()[:500] if isinstance(e, urllib.error.HTTPError) else str(e)
            print(f"  {chunk_label}: UPSERT FAILED: {detail}", file=sys.stderr)
            failed_chunks.append(chunk_label)
            log_sync(chunk_label, 0, "failed", detail)

    verb = "would upsert" if args.dry_run else "upserted"
    print(f"\n--- SUMMARY: {len(chunks)} chunk(s), {total_upserted} rows {verb}, {len(failed_chunks)} chunk(s) failed ---")
    if failed_chunks:
        print("Failed chunks:", failed_chunks)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
