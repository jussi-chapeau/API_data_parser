#!/usr/bin/env python3
"""
Add retryOnFail to the Fetch Orders/Fetch Manual Orders/Fetch Routes nodes on
the live N8N sync workflows.

Confirmed live state before this patch: Webhook Trigger, executeOnce, Loop Over
Items (cool), and settings.errorWorkflow were all already correctly wired.
retryOnFail was the one piece still missing everywhere -- this is what let the
Backoffice API's intermittent 500/504 (see docs/BACKOFFICE_API_ISSUES.md #7)
turn into permanent data loss instead of a retried, self-healing blip.

Patches only the named nodes in place (fetch -> modify -> PUT), same pattern as
fix_bulk_upsert_execute_once.py, so real credentials embedded in other nodes
are preserved untouched.

Usage:
  export N8N_API_URL="https://apukuski.app.n8n.cloud/api/v1"
  export N8N_API_KEY="..."
  python3 scripts/add_fetch_retry.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

WORKFLOWS = {
    "9hWlvNyCs8HmfZly": "orders-hot",
    "CcOBd7IELOnbonYL": "orders-warm",
    "QKbM3UvkJ8Yjkhb1": "orders-cool",
    "JH2On4vSuJidzbyU": "backfill",
}

FETCH_NODES = {"Fetch Orders", "Fetch Manual Orders", "Fetch Routes"}
MAX_TRIES = 5
WAIT_BETWEEN_TRIES_MS = 3000


def api(method: str, path: str, base: str, key: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{base}{path}",
        data=data,
        method=method,
        headers={
            "X-N8N-API-KEY": key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def patch_retry(nodes: list[dict]) -> list[str]:
    changed = []
    for n in nodes:
        if n.get("name") in FETCH_NODES and n.get("type") == "n8n-nodes-base.httpRequest":
            if n.get("retryOnFail") is True and n.get("maxTries") == MAX_TRIES:
                continue
            n["retryOnFail"] = True
            n["maxTries"] = MAX_TRIES
            n["waitBetweenTries"] = WAIT_BETWEEN_TRIES_MS
            changed.append(n["name"])
    return changed


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    base = os.environ.get("N8N_API_URL", "https://apukuski.app.n8n.cloud/api/v1")
    key = os.environ.get("N8N_API_KEY")
    if not key:
        print("ERROR: N8N_API_KEY is required", file=sys.stderr)
        return 1

    results = []
    for wf_id, label in WORKFLOWS.items():
        live = api("GET", f"/workflows/{wf_id}", base, key)
        changed = patch_retry(live["nodes"])

        if not changed:
            results.append({"workflow": label, "id": wf_id, "status": "already_ok"})
            print(f"{label}: already has retryOnFail, no change")
            continue

        if args.dry_run:
            results.append({"workflow": label, "id": wf_id, "status": "would_patch", "nodes": changed})
            print(f"{label}: [dry-run] would add retryOnFail to {changed}")
            continue

        api("PUT", f"/workflows/{wf_id}", base, key, {
            "name": live["name"],
            "nodes": live["nodes"],
            "connections": live["connections"],
            "settings": live.get("settings") or {},
            "staticData": live.get("staticData"),
        })
        check = api("GET", f"/workflows/{wf_id}", base, key)
        verified = all(
            n.get("retryOnFail") is True
            for n in check["nodes"]
            if n.get("name") in FETCH_NODES
        )
        results.append({"workflow": label, "id": wf_id, "status": "patched", "nodes": changed, "verified": verified})
        print(f"{label}: patched {changed}, verified={verified}")

    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()[:400]}", file=sys.stderr)
        raise SystemExit(1)
