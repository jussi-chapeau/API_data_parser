#!/usr/bin/env python3
"""Create bi_chatbot_readonly role via Supabase Management API."""

from __future__ import annotations

import json
import os
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
    token = os.getenv("SUPABASE_ACCESS_TOKEN")
    password = os.getenv("BI_DB_PASSWORD")
    if not token:
        print("ERROR: SUPABASE_ACCESS_TOKEN required", file=sys.stderr)
        return 1
    if not password:
        print("ERROR: BI_DB_PASSWORD required (from .env)", file=sys.stderr)
        return 1

    stmts = [
        "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bi_chatbot_readonly') THEN CREATE ROLE bi_chatbot_readonly WITH LOGIN PASSWORD "
        f"'{password.replace(chr(39), chr(39)+chr(39))}'; END IF; END $$;",
        "GRANT CONNECT ON DATABASE postgres TO bi_chatbot_readonly;",
        "GRANT USAGE ON SCHEMA public TO bi_chatbot_readonly;",
        "GRANT SELECT ON ALL TABLES IN SCHEMA public TO bi_chatbot_readonly;",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO bi_chatbot_readonly;",
    ]
    import time
    for i, sql in enumerate(stmts):
        if i:
            time.sleep(1)
        try:
            run_query(token, sql)
            print("OK:", sql[:70])
        except urllib.error.HTTPError as e:
            print("FAIL:", e.read().decode()[:300], file=sys.stderr)
            return 1
    print("Done. DATABASE_URL in .env should work for bi_chatbot_readonly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
