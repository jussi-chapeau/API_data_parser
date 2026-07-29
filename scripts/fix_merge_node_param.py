#!/usr/bin/env python3
"""
Fix Merge node parameters on the order sync workflows.

These Merge nodes join `/order` and `/manual-order` results before the shared
Transform Orders step. The two lists must be concatenated, so the correct mode
is `append`.

Two defects are corrected:

1. `combinationMode` is a Merge v2 parameter that v3 ignores, so the node fell
   back to match-by-fields and every run failed with "You need to define at
   least one pair of fields in Fields to Match".
2. `mode: combine` is wrong regardless of parameter name — it pairs items
   positionally, truncating output to the shorter input and merging a platform
   order's fields into a manual order's record.

Patches the live workflow in place (fetch → modify only the Merge node → PUT)
so unrelated node config such as the Transform Orders code is preserved.

Usage:
  python3 scripts/fix_merge_node_param.py [--dry-run]
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

APPEND_PARAMS = {"mode": "append", "options": {}}


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


def patch_merge_nodes(nodes: list[dict]) -> list[str]:
    """Set Merge nodes to append mode. Returns names of changed nodes."""
    changed = []
    for node in nodes:
        if node.get("type") != "n8n-nodes-base.merge":
            continue
        params = node.get("parameters", {})
        if params.get("mode") == "append" and "combinationMode" not in params:
            continue
        node["parameters"] = dict(APPEND_PARAMS)
        changed.append(node.get("name", "Merge"))
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    results = []
    for wf_id, rel_path in WORKFLOWS.items():
        live = api("GET", f"/workflows/{wf_id}")
        changed = patch_merge_nodes(live["nodes"])

        if not changed:
            results.append({"id": wf_id, "name": live["name"], "status": "already_ok"})
            continue

        if not args.dry_run:
            api("PUT", f"/workflows/{wf_id}", {
                "name": live["name"],
                "nodes": live["nodes"],
                "connections": live["connections"],
                "settings": live.get("settings") or {},
                "staticData": live.get("staticData"),
            })
            verify = api("GET", f"/workflows/{wf_id}")
            merge_params = [
                n.get("parameters", {})
                for n in verify["nodes"]
                if n.get("type") == "n8n-nodes-base.merge"
            ]
            ok = all(
                p.get("mode") == "append" and "combinationMode" not in p
                for p in merge_params
            )
            # Keep the committed workflow JSON aligned with live.
            local_path = repo_root() / rel_path
            if local_path.exists():
                local = json.loads(local_path.read_text())
                if patch_merge_nodes(local["nodes"]):
                    local_path.write_text(
                        json.dumps(local, indent=2, ensure_ascii=False) + "\n"
                    )
        else:
            ok = None

        results.append({
            "id": wf_id,
            "name": live["name"],
            "patched_nodes": changed,
            "verified": ok,
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
