#!/usr/bin/env python3
"""Smoke test tools without OpenRouter (DATABASE_URL + Backoffice)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# load .env
env = ROOT / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


async def main() -> int:
    from app.tools import backoffice_get_hubs, bi_orders_report, db_read

    print("=== db_read: order count ===")
    r = await db_read("SELECT COUNT(*) AS n FROM orders")
    print(r[:500])

    print("\n=== bi_orders_report: last 7 days ===")
    from datetime import date, timedelta
    end = date.today()
    start = end - timedelta(days=7)
    r2 = await bi_orders_report(start.isoformat(), end.isoformat(), group_by="day")
    print(r2[:800])

    print("\n=== backoffice_get_hubs (sample) ===")
    r3 = await backoffice_get_hubs()
    data = json.loads(r3)
    if "hubs" in data:
        print("hubs", data["count"], "first", data["hubs"][:2])
    else:
        print(r3[:300])

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
