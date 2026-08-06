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
# Cloudflare WAF blocks the default urllib User-Agent with error 1010 on this endpoint --
# same issue already documented and worked around in scripts/backfill_missing_months.py.
MGMT_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def run_query(token: str, sql: str) -> dict:
    body = json.dumps({"query": sql}).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.supabase.com/v1/projects/{PROJECT_REF}/database/query",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": MGMT_UA,
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
    # Strip full-line comments BEFORE splitting on semicolons. Filtering whole statements
    # that merely *start* with "--" silently drops real SQL whenever a comment block sits
    # directly in front of a statement (e.g. a multi-line header comment before the first
    # CREATE TABLE) -- caught this the hard way on 003_create_marketing_ads_tables.sql,
    # where it silently dropped the first table entirely. Doesn't handle inline trailing
    # comments or "--" inside string literals; none of this repo's migrations use those.
    lines = [line for line in sql.split("\n") if not line.strip().startswith("--")]
    statements = [s.strip() for s in "\n".join(lines).split(";") if s.strip()]
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
