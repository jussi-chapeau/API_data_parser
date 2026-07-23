#!/usr/bin/env python3
"""
Backfill manual_data.total_incl_vat_* on existing Supabase manual orders.

Uses charge.charge already stored in Supabase — no Backoffice API required.
Run after deploy when N8N sync cannot complete (API outage / backfill failure).

Usage:
  python3 scripts/backfill_manual_pricing_supabase.py [--dry-run] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

SUPABASE_URL = "https://ybznbfezrdgzgptxkgul.supabase.co/rest/v1/orders"
PAGE_SIZE = 500


def get_service_key() -> str:
    import json as _json
    from pathlib import Path

    settings = Path(__file__).resolve().parents[1] / ".claude" / "settings.json"
    n8n_key = _json.loads(settings.read_text())["mcpServers"]["n8n"]["env"]["N8N_API_KEY"]
    req = urllib.request.Request(
        "https://apukuski.app.n8n.cloud/api/v1/workflows/JH2On4vSuJidzbyU",
        headers={"X-N8N-API-KEY": n8n_key},
    )
    wf = _json.loads(urllib.request.urlopen(req, timeout=60).read())
    for node in wf["nodes"]:
        if node.get("name") == "Upsert Orders":
            for h in node["parameters"]["headerParameters"]["parameters"]:
                if h["name"] == "apikey":
                    return h["value"]
    raise RuntimeError("Could not read Supabase service key from live N8N workflow")


def parse_manual_total(charge: dict | None) -> dict[str, Any]:
    if not charge or charge.get("charge") in (None, ""):
        return {
            "total_incl_vat_eur": None,
            "total_incl_vat_cents": None,
            "raw_charge_total": None,
        }

    raw_original = str(charge["charge"]).strip()
    cleaned = (
        raw_original.replace("\xa0", "")
        .replace("€", "")
        .replace("EUR", "")
        .replace("eur", "")
        .strip()
    )
    cleaned = re.sub(r"\s+", "", cleaned).replace(",", ".")

    if not re.fullmatch(r"[-+]?\d+(\.\d+)?", cleaned):
        return {
            "total_incl_vat_eur": None,
            "total_incl_vat_cents": None,
            "raw_charge_total": raw_original,
        }

    total = float(cleaned)
    return {
        "total_incl_vat_eur": total,
        "total_incl_vat_cents": round(total * 100),
        "raw_charge_total": raw_original,
    }


def supabase_request(
    key: str,
    method: str,
    url: str,
    body: list | dict | None = None,
    extra_headers: dict | None = None,
) -> tuple[int, str, dict[str, str]]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read().decode("utf-8"), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8"), dict(e.headers)


def fetch_page(key: str, offset: int, limit: int) -> list[dict]:
    params = urllib.parse.urlencode(
        {
            "select": "order_id,charge,manual_data",
            "is_manual": "eq.true",
            "order": "order_id.asc",
            "limit": str(limit),
            "offset": str(offset),
        }
    )
    status, body, _ = supabase_request(key, "GET", f"{SUPABASE_URL}?{params}")
    if status != 200:
        raise RuntimeError(f"Fetch failed {status}: {body[:300]}")
    return json.loads(body)


def upsert_batch(key: str, rows: list[dict]) -> None:
    params = urllib.parse.urlencode({"on_conflict": "order_id"})
    status, body, _ = supabase_request(
        key,
        "POST",
        f"{SUPABASE_URL}?{params}",
        rows,
        extra_headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
    )
    if status not in (200, 201, 204):
        raise RuntimeError(f"Upsert failed {status}: {body[:500]}")


def build_patch(row: dict) -> dict | None:
    manual_data = dict(row.get("manual_data") or {})
    pricing = parse_manual_total(row.get("charge") or {})
    updated = {
        **manual_data,
        "total_incl_vat_eur": pricing["total_incl_vat_eur"],
        "total_incl_vat_cents": pricing["total_incl_vat_cents"],
        "price_basis": "gross_incl_vat",
        "raw_charge_total": pricing["raw_charge_total"],
    }
    if manual_data == updated:
        return None
    return {"order_id": row["order_id"], "manual_data": updated}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Max rows to process (0=all)")
    args = parser.parse_args()

    key = get_service_key()
    offset = 0
    scanned = 0
    patched = 0
    with_cents = 0
    non_numeric = 0
    batch: list[dict] = []

    while True:
        page_limit = PAGE_SIZE
        if args.limit:
            remaining = args.limit - scanned
            if remaining <= 0:
                break
            page_limit = min(PAGE_SIZE, remaining)

        rows = fetch_page(key, offset, page_limit)
        if not rows:
            break

        for row in rows:
            scanned += 1
            patch = build_patch(row)
            if patch is None:
                if (row.get("manual_data") or {}).get("total_incl_vat_cents") is not None:
                    with_cents += 1
                continue
            pricing = patch["manual_data"]
            if pricing["total_incl_vat_cents"] is not None:
                with_cents += 1
            else:
                non_numeric += 1
            if not args.dry_run:
                batch.append(patch)
                if len(batch) >= 100:
                    upsert_batch(key, batch)
                    patched += len(batch)
                    batch.clear()
            else:
                patched += 1

        offset += len(rows)
        if len(rows) < page_limit:
            break

    if batch and not args.dry_run:
        upsert_batch(key, batch)
        patched += len(batch)

    summary = {
        "scanned": scanned,
        "patched": patched,
        "would_have_cents": with_cents,
        "non_numeric_charge": non_numeric,
        "dry_run": args.dry_run,
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
