#!/usr/bin/env python3
"""
Reconcile Airtable settlement CSV against Backoffice API / Supabase data.

Usage:
  python3 scripts/reconcile_airtable_financials.py \\
    --csv "data/Tilitykset _ Laskutukset-Grid view (4).csv" \\
    --month "toukokuu 2026" \\
    --api-snapshot data/may_api_snapshot.json \\
    --output data/reconciliation_may_2026.json

Fetches live API data if --api-snapshot is omitted (slower).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

API_BASE = "https://qtml5qv6uk.execute-api.eu-central-1.amazonaws.com/production"
API_KEY = "oz6Dcgxn5k3px6a3fN9NU5YHshMuYpxP4NNyMdi4"
VAT_MULT = 1.255


def eur(val: str | None) -> float | None:
    s = (val or "").strip()
    if not s:
        return None
    s = s.replace("€", "").replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def num(val: str | None) -> float | None:
    s = (val or "").strip().replace("%", "").replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def cents_to_eur(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v) / 100.0
    except (TypeError, ValueError):
        return None


def parse_manual_total(charge: dict | None) -> tuple[float | None, int | None, str | None]:
    if not charge or charge.get("charge") in (None, ""):
        return None, None, None
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
        return None, None, raw_original
    total = float(cleaned)
    return total, round(total * 100), raw_original


def load_csv_rows(path: Path, month: str) -> list[dict[str, str]]:
    rows = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            if (r.get("Month") or "").strip().lower() != month.strip().lower():
                continue
            oid = (r.get("OrderId") or "").strip()
            if not oid:
                m = re.search(r"orderId=([0-9a-f\-]{36})", r.get("Staff Link", ""))
                oid = m.group(1) if m else ""
            r["_order_id"] = oid
            rows.append(r)
    return rows


def fetch_api_snapshot(start: str, end: str) -> dict[str, dict]:
    by_id: dict[str, dict] = {}

    def fetch(ep: str) -> list[dict]:
        url = f"{API_BASE}{ep}?start_date={start}&end_date={end}"
        out = subprocess.check_output(
            ["curl", "-sS", url, "-H", f"x-api-key: {API_KEY}"],
            text=True,
        )
        data = json.loads(out)
        if isinstance(data, dict) and data.get("message"):
            raise RuntimeError(f"{ep} error: {data.get('message')}")
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected {ep} response: {type(data)} {str(data)[:200]}")
        return data

    cur = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    orders: list[dict] = []
    manual: list[dict] = []
    while cur <= end_d:
        chunk_end = min(cur + timedelta(days=6), end_d)
        s, e = cur.isoformat(), chunk_end.isoformat()
        for ep, target in [("/order", orders), ("/manual-order", manual)]:
            target.extend(fetch(f"{ep}?start_date={s}&end_date={e}"))
        cur = chunk_end + timedelta(days=1)

    for row in orders:
        row["_is_manual"] = False
        if row.get("orderId"):
            by_id[row["orderId"]] = row
    for row in manual:
        row["_is_manual"] = True
        if row.get("orderId"):
            by_id[row["orderId"]] = row
    return by_id


def load_api_by_id(snapshot_path: Path | None, start: str, end: str) -> dict[str, dict]:
    if snapshot_path:
        data = json.loads(snapshot_path.read_text())
        by_id = {}
        for row in data.get("orders", []):
            row["_is_manual"] = False
            if row.get("orderId"):
                by_id[row["orderId"]] = row
        for row in data.get("manual_orders", []):
            row["_is_manual"] = True
            if row.get("orderId"):
                by_id[row["orderId"]] = row
        return by_id
    return fetch_api_snapshot(start, end)


def reconcile_row(csv_row: dict, api_row: dict | None) -> dict:
    oid = csv_row["_order_id"]
    out: dict[str, Any] = {
        "order_id": oid,
        "partner": csv_row.get("Partner"),
        "title": csv_row.get("Tuote / Toimitus PVM /  Auto / Toimipiste / Asiakkaan nimi"),
    }
    if not oid:
        out["status"] = "missing_order_id"
        return out
    if not api_row:
        out["status"] = "missing_in_api"
        return out

    is_manual = api_row.get("_is_manual", False)
    out["is_manual"] = is_manual

    csv_platform_gross = eur(csv_row.get("Alustamaksu (sis alv 25,5%)"))
    csv_service_gross = eur(csv_row.get("Palvelumaksu (sis alv 25,5%)"))
    csv_total_gross = eur(csv_row.get("Apukuskille maksu (sis alv 25,5%)"))
    csv_share = eur(csv_row.get("Apukuskin tuotto (alv 0)"))
    csv_commission = num(csv_row.get("Komissiokanta"))

    api_platform_net = cents_to_eur(api_row.get("platformFee"))
    api_service_net = cents_to_eur(api_row.get("serviceFee"))
    api_commission = (
        float(api_row["commissionRate"])
        if api_row.get("commissionRate") not in (None, "")
        else None
    )
    manual_eur, manual_cents, manual_raw = parse_manual_total(api_row.get("charge") or {})

    diffs = []
    if is_manual:
        if csv_total_gross is not None and manual_eur is not None:
            d = abs(csv_total_gross - manual_eur)
            if d > 0.02:
                diffs.append(
                    {
                        "field": "manual_total_incl_vat",
                        "csv": csv_total_gross,
                        "api": manual_eur,
                        "delta": d,
                    }
                )
        out["api_manual_total_incl_vat"] = manual_eur
        out["api_manual_raw_charge"] = manual_raw
    else:
        if csv_platform_gross is not None and api_platform_net is not None:
            implied_gross = api_platform_net * VAT_MULT
            d = abs(csv_platform_gross - implied_gross)
            if d > 0.02:
                diffs.append(
                    {
                        "field": "platform_fee_gross",
                        "csv": csv_platform_gross,
                        "api_net": api_platform_net,
                        "api_gross_implied": implied_gross,
                        "delta": d,
                    }
                )
        if csv_service_gross is not None and api_service_net is not None:
            implied_gross = api_service_net * VAT_MULT
            d = abs(csv_service_gross - implied_gross)
            if d > 0.02:
                diffs.append(
                    {
                        "field": "service_fee_gross",
                        "csv": csv_service_gross,
                        "api_net": api_service_net,
                        "api_gross_implied": implied_gross,
                        "delta": d,
                    }
                )

    if csv_commission is not None and api_commission is not None:
        if abs(csv_commission - api_commission) > 0.0001:
            diffs.append(
                {
                    "field": "commission_rate",
                    "csv": csv_commission,
                    "api": api_commission,
                    "delta": abs(csv_commission - api_commission),
                }
            )

    out["diffs"] = diffs
    out["status"] = "ok" if not diffs else "mismatch"
    out["csv_share_alv0"] = csv_share
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", type=Path, required=True)
    p.add_argument("--month", default="toukokuu 2026")
    p.add_argument("--api-snapshot", type=Path, default=None)
    p.add_argument("--api-start", default="2025-11-01")
    p.add_argument("--api-end", default="2026-05-31")
    p.add_argument("--output", type=Path, default=None)
    args = p.parse_args()

    csv_rows = load_csv_rows(args.csv, args.month)
    api_by_id = load_api_by_id(args.api_snapshot, args.api_start, args.api_end)

    results = []
    for row in csv_rows:
        oid = row["_order_id"]
        api_row = api_by_id.get(oid) if oid else None
        results.append(reconcile_row(row, api_row))

    status_counts = Counter(r["status"] for r in results)
    mismatch_fields = Counter()
    for r in results:
        for d in r.get("diffs", []):
            mismatch_fields[d["field"]] += 1

    manual = [r for r in results if r.get("is_manual")]
    platform = [r for r in results if r.get("is_manual") is False]

    summary = {
        "month": args.month,
        "csv_rows": len(csv_rows),
        "with_order_id": sum(1 for r in csv_rows if r["_order_id"]),
        "matched_api": sum(1 for r in results if r["status"] not in ("missing_order_id", "missing_in_api")),
        "status_counts": dict(status_counts),
        "mismatch_fields": dict(mismatch_fields),
        "platform_ok": sum(1 for r in platform if r["status"] == "ok"),
        "platform_total": len(platform),
        "manual_ok": sum(1 for r in manual if r["status"] == "ok"),
        "manual_total": len(manual),
        "notes": [
            "Platform fee compare: CSV gross vs API net * 1.255",
            "Manual compare: CSV total incl VAT vs charge.charge",
            "Airtable share/margin not auto-compared (manual settlement logic)",
        ],
    }

    report = {"summary": summary, "results": results}
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(text)
        print(f"Wrote {args.output}")
    else:
        print(text)

    print("\n--- SUMMARY ---", file=sys.stderr)
    print(json.dumps(summary, indent=2), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
