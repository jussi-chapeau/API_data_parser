#!/usr/bin/env python3
"""Sync Backoffice /route into Supabase `public.routes`.

Spec: docs/api/BACKOFFICE_ROUTE_ENDPOINT.md.

WHY THIS EXISTS AT ALL. `docs/GOTCHAS.md` and `CLAUDE.md` both record routes sync as blocked on
an upstream Lambda timeout. Measured 2026-09-23: 1 day 1.1 s, 8 days 0.2 s, 30 days 0.2 s, no
failures. The endpoint is fine. That gotcha is stale.

THE UNIT TRAP THIS SCRIPT EXISTS TO CLOSE. The live API sends `internalCost` in EUROS while
`totalSales` is in CENTS. Proven two ways: the values carry decimals (3985.97, 786.3, 359.25)
so they cannot be integer cents, and read as euros the cost is 78% of sales across 25 routes --
plausible -- while read as cents it is 0.8%, which is not. The existing `routes.internal_cost`
is an `integer` column with no unit in its name sitting next to cents, so subtracting the two
is wrong by 100x.

The backend is renaming it to `internalCostCents` (cents, string). That has NOT shipped. So this
script reads BOTH shapes and always stores cents:

    internalCostCents present -> take as cents          contract_version='internalCostCents'
    internalCost present      -> euros, multiply by 100 contract_version='internalCost_eur'

When the rename ships, nothing here needs changing -- the first branch simply starts winning,
and `contract_version` makes the cutover observable in the data instead of a thing someone has
to remember to check.

include=stops,orders IS NOT LIVE either -- it 400s with "Unknown query parameters". The script
probes once per run and uses the breakdown if it appears, so the day the PR lands this starts
capturing per-order revenue without an edit. Until then it records nothing extra rather than
guessing.

WHAT IS DELIBERATELY NOT COMPUTED. No margin. `totalSales` is VAT-inclusive customer revenue;
internal cost is free text staff copy from a partner invoice and its VAT status is unverified by
the backend. Subtracting them is not a margin. No per-order cost allocation either -- the
backend declined to invent a rule and so does this.

Usage:
  export BACKOFFICE_API_URL=... BACKOFFICE_API_KEY=... SUPABASE_URL=... SUPABASE_SERVICE_KEY=...
  python3 scripts/sync_routes.py --dry-run
  python3 scripts/sync_routes.py --start 2026-01-01
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
from decimal import Decimal, InvalidOperation

CHUNK_DAYS = 30
MAX_TRIES = 4


def api(base: str, key: str, params: dict, timeout: int = 120):
    url = f"{base.rstrip('/')}/route?{urllib.parse.urlencode(params)}"
    for attempt in range(1, MAX_TRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"x-api-key": key})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8")), None
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")[:200]
            if e.code == 400:
                return None, body          # contract error: do not retry
            if attempt == MAX_TRIES:
                return None, f"{e.code} {body}"
        except Exception as e:
            if attempt == MAX_TRIES:
                return None, str(e)
        time.sleep(1.5 * attempt)
    return None, "exhausted"


def supports_include(base: str, key: str, day: str) -> bool:
    """One probe per run. 400 means the breakdown PR has not landed yet."""
    _, err = api(base, key, {"start_date": day, "end_date": day, "include": "orders"})
    return err is None


def cost_cents(route: dict) -> tuple[int | None, str]:
    """Return (cents, contract_version). Handles both sides of the pending rename."""
    if route.get("internalCostCents") is not None:
        try:
            return int(Decimal(str(route["internalCostCents"]))), "internalCostCents"
        except (InvalidOperation, ValueError):
            return None, "internalCostCents"
    raw = route.get("internalCost")
    if raw is None:
        return None, "internalCost_eur"
    try:
        # euros, possibly decimal -- never int() the string, that truncates 786.3 to 786
        return int((Decimal(str(raw)) * 100).to_integral_value()), "internalCost_eur"
    except (InvalidOperation, ValueError):
        return None, "internalCost_eur"


def to_cents(v) -> int | None:
    if v is None:
        return None
    try:
        return int(Decimal(str(v)))
    except (InvalidOperation, ValueError):
        return None


def supa(url: str, key: str, path: str, body, prefer: str):
    headers = {"apikey": key, "Authorization": f"Bearer {key}",
               "Content-Type": "application/json", "Prefer": prefer}
    req = urllib.request.Request(f"{url.rstrip('/')}/rest/v1/{path}",
                                 data=json.dumps(body).encode("utf-8"),
                                 method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    base, key = os.environ.get("BACKOFFICE_API_URL"), os.environ.get("BACKOFFICE_API_KEY")
    su, sk = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not (base and key):
        print("ERROR: BACKOFFICE_API_URL and BACKOFFICE_API_KEY required.", file=sys.stderr)
        return 2
    if not args.dry_run and not (su and sk):
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY required.", file=sys.stderr)
        return 2

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else date.today()

    inc = supports_include(base, key, end.isoformat())
    print(f"include=orders supported by the API: {inc}"
          + ("" if inc else "  (expected today -- breakdown PR still pending)"))

    routes, failures = [], []
    cur = start
    while cur <= end:
        stop = min(cur + timedelta(days=CHUNK_DAYS - 1), end)
        p = {"start_date": cur.isoformat(), "end_date": stop.isoformat()}
        if inc:
            p["include"] = "stops,orders"
        data, err = api(base, key, p)
        if err:
            failures.append((cur.isoformat(), stop.isoformat(), err))
        else:
            routes.extend(data or [])
        cur = stop + timedelta(days=1)

    print(f"routes fetched: {len(routes)}   failed windows: {len(failures)}")
    for f in failures:
        print(f"   FAILED {f[0]}..{f[1]}: {f[2]}", file=sys.stderr)

    now = datetime.now(timezone.utc).isoformat()
    rows, versions, invariant_breaks = [], {}, []
    for r in routes:
        cc, ver = cost_cents(r)
        versions[ver] = versions.get(ver, 0) + 1
        order_ids = r.get("orderIds") or []
        wh_ids = r.get("warehouseOrderIds") or []
        total = to_cents(r.get("totalSales"))

        # The spec promises sum(attributedRevenue) == totalSales. Assert it when the breakdown
        # is there -- it is free, and a silent drift here would be invisible downstream.
        if inc and isinstance(r.get("orders"), list):
            attributed = [to_cents(o.get("attributedRevenue")) for o in r["orders"]]
            known = [a for a in attributed if a is not None]
            if total is not None and known and abs(sum(known) - total) > 1:
                invariant_breaks.append((r.get("routeId"), sum(known), total))

        rows.append({
            "route_id": r.get("routeId"),
            "date": r.get("date"),
            "hub_id": r.get("hubId"),
            "partner": r.get("partner"),
            "total_sales_cents": total,
            "internal_cost_cents": cc,
            "order_ids": order_ids,
            "warehouse_order_ids": wh_ids,
            "order_count": len(order_ids),
            "warehouse_count": len(wh_ids),
            "contract_version": ver,
            "synced_at": now,
        })

    print(f"contract versions seen: {versions}")
    if invariant_breaks:
        print(f"WARNING: sum(attributedRevenue) != totalSales on {len(invariant_breaks)} routes",
              file=sys.stderr)
        for rid, got, want in invariant_breaks[:5]:
            print(f"   {rid}: attributed {got} vs totalSales {want}", file=sys.stderr)

    priced = [r for r in rows if r["internal_cost_cents"] and r["total_sales_cents"]]
    if priced:
        c = sum(r["internal_cost_cents"] for r in priced)
        s = sum(r["total_sales_cents"] for r in priced)
        print(f"sanity: cost {c/100:,.0f} EUR vs sales {s/100:,.0f} EUR = {100*c/s:.0f}%"
              "   (a cents/euros mix-up would show ~1%)")

    if args.dry_run:
        print("DRY RUN -- nothing written.")
        return 0

    for i in range(0, len(rows), 200):
        supa(su, sk, "routes?on_conflict=route_id", rows[i:i + 200],
             "resolution=merge-duplicates,return=minimal")
    print(f"upserted {len(rows)} routes.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
