#!/usr/bin/env python3
"""Apply a Supabase SQL migration file to the live project.

Uses Supabase Management API when SUPABASE_ACCESS_TOKEN is set:
  https://supabase.com/docs/reference/api/v1-run-a-query

Usage:
  export SUPABASE_ACCESS_TOKEN="..."
  python3 scripts/apply_supabase_migration.py supabase/migrations/002_add_origin_to_orders.sql
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_REF = "ybznbfezrdgzgptxkgul"


def run_query(token: str, sql: str) -> dict:
    body = json.dumps({"query": sql}).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{PROJECT_REF}/database/query",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <migration.sql>", file=sys.stderr)
        return 1

    token = __import__("os").environ.get("SUPABASE_ACCESS_TOKEN")
    if not token:
        print("ERROR: SUPABASE_ACCESS_TOKEN is not set.", file=sys.stderr)
        print("Create one at https://supabase.com/dashboard/account/tokens", file=sys.stderr)
        return 1

    sql = Path(sys.argv[1]).read_text()
    # Management API runs one statement at a time for some setups; split on semicolons.
    statements = [s.strip() for s in sql.split(";") if s.strip() and not s.strip().startswith("--")]
    results = []
    import time
    for i, stmt in enumerate(statements):
        if i:
            time.sleep(1)
        try:
            results.append({"sql": stmt[:80], "result": run_query(token, stmt)})
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8")
            print(f"FAILED: {stmt[:80]}\n{err}", file=sys.stderr)
            return 1

    print(json.dumps({"applied": len(statements), "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
