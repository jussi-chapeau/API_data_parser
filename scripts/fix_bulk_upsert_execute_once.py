#!/usr/bin/env python3
"""
Set executeOnce on sync nodes that send the whole batch in one request body.

The Upsert/Log nodes build their body from `$input.all()`, so a single request
already carries every row. Without `executeOnce`, n8n still runs the node once
per input item, re-serialising the full array each time: N items produce N
identical requests and N x N item serialisations. At ~500 orders the cool sync
exhausted memory and crashed ("possible out-of-memory issue").

Setting executeOnce makes the node fire exactly once per run.

Usage:
  python3 scripts/fix_bulk_upsert_execute_once.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

N8N_API_URL = "https://apukuski.app.n8n.cloud/api/v1"

WORKFLOWS = {
    "9hWlvNyCs8HmfZly": "n8n-workflows/orders-hot.json",
    "CcOBd7IELOnbonYL": "n8n-workflows/orders-warm.json",
    "QKbM3UvkJ8Yjkhb1": "n8n-workflows/orders-cool.json",
    "JH2On4vSuJidzbyU": "n8n-workflows/backfill.json",
}

# Marks a node whose request body already contains the entire batch.
BULK_BODY_MARKERS = ("$input.all()", ".all()")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def n8n_key() -> str:
    settings = repo_root() / ".claude" / "settings.json"
    return json.loads(settings.read_text())["mcpServers"]["n8n"]["env"]["N8N_API_KEY"]


def api(method: str, path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{N8N_API_URL}{path}",
        data=data,
        method=method,
        headers={
            "X-N8N-API-KEY": n8n_key(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def is_bulk_body_node(node: dict) -> bool:
    if node.get("type") != "n8n-nodes-base.httpRequest":
        return False
    body = str(node.get("parameters", {}).get("jsonBody", ""))
    return any(marker in body for marker in BULK_BODY_MARKERS)


def patch_execute_once(nodes: list[dict]) -> list[str]:
    changed = []
    for node in nodes:
        if not is_bulk_body_node(node) or node.get("executeOnce") is True:
            continue
        node["executeOnce"] = True
        changed.append(node.get("name", "?"))
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    results = []
    for wf_id, rel_path in WORKFLOWS.items():
        live = api("GET", f"/workflows/{wf_id}")
        changed = patch_execute_once(live["nodes"])

        if not changed:
            results.append({"id": wf_id, "name": live["name"], "status": "already_ok"})
            continue

        verified = None
        if not args.dry_run:
            api("PUT", f"/workflows/{wf_id}", {
                "name": live["name"],
                "nodes": live["nodes"],
                "connections": live["connections"],
                "settings": live.get("settings") or {},
                "staticData": live.get("staticData"),
            })
            check = api("GET", f"/workflows/{wf_id}")
            verified = all(
                n.get("executeOnce") is True
                for n in check["nodes"]
                if is_bulk_body_node(n)
            )
            local_path = repo_root() / rel_path
            if local_path.exists():
                local = json.loads(local_path.read_text())
                if patch_execute_once(local["nodes"]):
                    local_path.write_text(
                        json.dumps(local, indent=2, ensure_ascii=False) + "\n"
                    )

        results.append({
            "id": wf_id,
            "name": live["name"],
            "patched_nodes": changed,
            "verified": verified,
            "dry_run": args.dry_run,
        })

    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()[:400]}", file=sys.stderr)
        raise SystemExit(1)
