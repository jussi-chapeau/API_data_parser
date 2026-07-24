#!/usr/bin/env python3
"""
Backfill orders.origin from Backoffice API into Supabase.

Platform orders only — manual /manual-order rows have no origin field.
Fetches day-by-day to avoid Lambda timeouts on large windows.

Usage:
  python3 scripts/backfill_order_origin.py [--start 2026-07-01] [--end 2026-07-24] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

API_BASE = "https://qtml5qv6uk.execute-api.eu-central-1.amazonaws.com/production"
API_KEY = "oz6Dcgxn5k3px6a3fN9NU5YHshMuYpxP4NNyMdi4"
SUPABASE_URL = "https://ybznbfezrdgzgptxkgul.supabase.co/rest/v1/orders"
BATCH = 100


def get_service_key() -> str:
    settings = json.loads(
        (Path(__file__).resolve().parents[1] / ".claude" / "settings.json").read_text()
    )
    n8n_key = settings["mcpServers"]["n8n"]["env"]["N8N_API_KEY"]
    req = urllib.request.Request(
        "https://apukuski.app.n8n.cloud/api/v1/workflows/JH2On4vSuJidzbyU",
        headers={"X-N8N-API-KEY": n8n_key},
    )
    wf = json.loads(urllib.request.urlopen(req, timeout=60).read())
    for node in wf["nodes"]:
        if node.get("name") == "Upsert Orders":
            for h in node["parameters"]["headerParameters"]["parameters"]:
                if h["name"] == "apikey":
                    return h["value"]
    raise RuntimeError("Could not read Supabase service key from live N8N workflow")


def fetch_orders(day: str) -> list[dict]:
    url = f"{API_BASE}/order?start_date={day}&end_date={day}"
    out = subprocess.check_output(
        ["curl", "-sS", "--max-time", "90", url, "-H", f"x-api-key: {API_KEY}"],
        text=True,
    )
    data = json.loads(out)
    if isinstance(data, dict):
        raise RuntimeError(f"{day}: {data.get('message', data)}")
    return data


def upsert_batch(key: str, rows: list[dict]) -> None:
    params = urllib.parse.urlencode({"on_conflict": "order_id"})
    body = json.dumps(rows).encode("utf-8")
    req = urllib.request.Request(
        f"{SUPABASE_URL}?{params}",
        data=body,
        method="POST",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        if resp.status not in (200, 201, 204):
            raise RuntimeError(resp.read().decode("utf-8")[:500])


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2026-07-01")
    p.add_argument("--end", default=date.today().isoformat())
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    key = get_service_key()
    cur = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    scanned = 0
    with_origin = 0
    upserted = 0
    batch: list[dict] = []
    values: dict[str, int] = {}

    while cur <= end:
        day = cur.isoformat()
        try:
            orders = fetch_orders(day)
        except Exception as e:
            print(f"warn {day}: {e}")
            cur += timedelta(days=1)
            continue

        for o in orders:
            scanned += 1
            origin = o.get("origin")
            if origin in (None, ""):
                continue
            with_origin += 1
            values[str(origin)] = values.get(str(origin), 0) + 1
            row = {"order_id": o["orderId"], "origin": origin}
            if args.dry_run:
                upserted += 1
            else:
                batch.append(row)
                if len(batch) >= BATCH:
                    upsert_batch(key, batch)
                    upserted += len(batch)
                    batch.clear()
        cur += timedelta(days=1)

    if batch and not args.dry_run:
        upsert_batch(key, batch)
        upserted += len(batch)

    print(
        json.dumps(
            {
                "window": {"start": args.start, "end": args.end},
                "scanned": scanned,
                "with_origin_in_api": with_origin,
                "upserted": upserted,
                "origin_values": values,
                "dry_run": args.dry_run,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
