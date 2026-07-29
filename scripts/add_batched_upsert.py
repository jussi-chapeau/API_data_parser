#!/usr/bin/env python3
"""
Route order upserts through a Loop Over Items node so Supabase receives
batches instead of one oversized request.

The sync sent every transformed order in a single POST. Once the cool window
produced ~500 rows the request stopped completing: Supabase (t4g.micro) failed
the TCP handshake and Cloudflare returned 522.

Rewires:
    Transform Orders -> Upsert Orders -> Log Sync
into:
    Transform Orders -> Loop Over Items --(loop)--> Upsert Orders -+
                                |                                  |
                                |<---------------------------------+
                                +--(done)--> Log Sync

splitInBatches v3 exposes output 0 as "done" and output 1 as "loop".

Usage:
  python3 scripts/add_batched_upsert.py --workflow cool [--batch-size 100] [--dry-run]
  python3 scripts/add_batched_upsert.py --workflow all
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

N8N_API_URL = "https://apukuski.app.n8n.cloud/api/v1"

WORKFLOWS = {
    "hot": ("9hWlvNyCs8HmfZly", "n8n-workflows/orders-hot.json"),
    "warm": ("CcOBd7IELOnbonYL", "n8n-workflows/orders-warm.json"),
    "cool": ("QKbM3UvkJ8Yjkhb1", "n8n-workflows/orders-cool.json"),
}

LOOP_NODE = "Loop Over Items"
SOURCE_NODE = "Transform Orders"
UPSERT_NODE = "Upsert Orders"
LOG_NODE = "Log Sync"


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


def node_by_name(nodes: list[dict], name: str) -> dict | None:
    return next((n for n in nodes if n.get("name") == name), None)


def rewire(workflow: dict, batch_size: int) -> str:
    nodes = workflow["nodes"]
    conns = workflow.setdefault("connections", {})

    transform = node_by_name(nodes, SOURCE_NODE)
    upsert = node_by_name(nodes, UPSERT_NODE)
    if transform is None or upsert is None:
        raise ValueError(f"missing {SOURCE_NODE} or {UPSERT_NODE}")

    loop = node_by_name(nodes, LOOP_NODE)
    if loop is None:
        tx, ty = transform.get("position", [1120, 300])
        loop = {
            "parameters": {"batchSize": batch_size, "options": {}},
            "id": str(uuid.uuid4()),
            "name": LOOP_NODE,
            "type": "n8n-nodes-base.splitInBatches",
            "typeVersion": 3,
            "position": [tx + 180, ty],
        }
        nodes.append(loop)
        action = "added"
    else:
        loop["parameters"]["batchSize"] = batch_size
        action = "updated"

    # Upsert receives one batch at a time; body already spans the batch.
    upsert["executeOnce"] = True

    conns[SOURCE_NODE] = {
        "main": [[{"node": LOOP_NODE, "type": "main", "index": 0}]]
    }
    conns[LOOP_NODE] = {
        "main": [
            [{"node": LOG_NODE, "type": "main", "index": 0}],
            [{"node": UPSERT_NODE, "type": "main", "index": 0}],
        ]
    }
    conns[UPSERT_NODE] = {
        "main": [[{"node": LOOP_NODE, "type": "main", "index": 0}]]
    }
    return action


def verify(workflow: dict) -> dict:
    conns = workflow.get("connections", {})

    def targets(name: str, out: int = 0) -> list[str]:
        branches = conns.get(name, {}).get("main") or []
        if len(branches) <= out or not branches[out]:
            return []
        return [c["node"] for c in branches[out]]

    loop = node_by_name(workflow["nodes"], LOOP_NODE)
    return {
        "loop_present": loop is not None,
        "batch_size": (loop or {}).get("parameters", {}).get("batchSize"),
        "transform_to": targets(SOURCE_NODE),
        "loop_done_to": targets(LOOP_NODE, 0),
        "loop_batch_to": targets(LOOP_NODE, 1),
        "upsert_to": targets(UPSERT_NODE),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow", default="cool", choices=[*WORKFLOWS, "all"])
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    selected = list(WORKFLOWS) if args.workflow == "all" else [args.workflow]
    results = []

    for label in selected:
        wf_id, rel_path = WORKFLOWS[label]
        live = api("GET", f"/workflows/{wf_id}")
        action = rewire(live, args.batch_size)

        if not args.dry_run:
            api("PUT", f"/workflows/{wf_id}", {
                "name": live["name"],
                "nodes": live["nodes"],
                "connections": live["connections"],
                "settings": live.get("settings") or {},
                "staticData": live.get("staticData"),
            })
            check = api("GET", f"/workflows/{wf_id}")
            state = verify(check)
            local_path = repo_root() / rel_path
            if local_path.exists():
                local = json.loads(local_path.read_text())
                try:
                    rewire(local, args.batch_size)
                    local_path.write_text(
                        json.dumps(local, indent=2, ensure_ascii=False) + "\n"
                    )
                except ValueError:
                    pass
        else:
            state = verify(live)

        results.append({
            "workflow": label,
            "name": live["name"],
            "loop_node": action,
            "state": state,
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
