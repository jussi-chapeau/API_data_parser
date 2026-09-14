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


def split_statements(sql: str) -> list[str]:
    """Split a migration into statements on top-level semicolons only.

    Two things this has to get right, both learned from real breakage:

    1. Full-line `--` comments are stripped BEFORE splitting. Filtering whole statements
       that merely *start* with "--" silently drops real SQL whenever a comment block sits
       in front of a statement -- that silently dropped an entire CREATE TABLE in
       003_create_marketing_ads_tables.sql.
    2. Semicolons inside dollar-quoted bodies ($$ ... $$, $tag$ ... $tag$) are NOT
       statement separators. Splitting naively tears `DO $$ BEGIN ... END $$` into
       fragments and Postgres rejects them with "unterminated dollar-quoted string" --
       hit on 011_phantom_order_deletion.sql, whose RLS grants live in a DO block.
    3. Semicolons inside ordinary single-quoted strings are not separators either --
       hit on 014_ga4_windsor_cutover.sql, where a COMMENT body contained "; ". This was
       a documented limitation rather than a fixed one for two migrations running; a
       COMMENT with a semicolon in it is too ordinary to keep tripping over. '' inside a
       string is an escaped quote, not a close.

    Still not a real SQL parser -- it does not handle `--` appearing inside a string
    literal, or E'' / dollar-quoted escape exotica. Those have not come up.
    """
    lines = [ln for ln in sql.split("\n") if not ln.strip().startswith("--")]
    text = "\n".join(lines)

    statements: list[str] = []
    buf: list[str] = []
    dollar_tag: str | None = None
    in_squote = False
    i = 0
    while i < len(text):
        ch = text[i]

        # A trailing `--` comment runs to end of line and is NOT code. Skip over it without
        # interpreting anything inside: an apostrophe in prose ("-- the order's own id")
        # would otherwise open a string literal that never closes and swallow the rest of
        # the file into one malformed statement. Caught exactly that way while fixing the
        # single-quote handling below -- 011 collapsed from 10 statements to 1.
        if not in_squote and dollar_tag is None and text.startswith("--", i):
            eol = text.find("\n", i)
            if eol == -1:
                eol = len(text)
            buf.append(text[i:eol])   # kept verbatim; harmless to Postgres, keeps SQL readable
            i = eol
            continue

        # Single-quoted string: only dollar-quoting outranks it, so check it first and
        # consume everything until the closing quote.
        if in_squote:
            if ch == "'":
                if text[i + 1:i + 2] == "'":      # '' is an escaped quote, stay inside
                    buf.append("''")
                    i += 2
                    continue
                in_squote = False
            buf.append(ch)
            i += 1
            continue

        if dollar_tag is None and ch == "'":
            in_squote = True
            buf.append(ch)
            i += 1
            continue

        if dollar_tag is None and ch == "$":
            # Possible opening dollar-quote: $$ or $tag$
            end = text.find("$", i + 1)
            if end != -1 and (text[i + 1:end] == "" or text[i + 1:end].isidentifier()):
                dollar_tag = text[i:end + 1]
                buf.append(dollar_tag)
                i = end + 1
                continue
        elif dollar_tag is not None and text.startswith(dollar_tag, i):
            buf.append(dollar_tag)
            i += len(dollar_tag)
            dollar_tag = None
            continue

        if ch == ";" and dollar_tag is None:
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
        else:
            buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


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
    statements = split_statements(sql)
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
