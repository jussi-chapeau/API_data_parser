#!/usr/bin/env python3
"""Add Webhook Trigger nodes to sync workflows and push to live N8N."""

from __future__ import annotations

import json
import uuid
import urllib.error
import urllib.request
from copy import deepcopy
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SETTINGS = REPO / ".claude" / "settings.json"
N8N_API_URL = "https://apukuski.app.n8n.cloud/api/v1"
N8N_API_KEY = json.loads(SETTINGS.read_text())["mcpServers"]["n8n"]["env"]["N8N_API_KEY"]

# workflow_id -> (repo json file, webhook path, first node after trigger)
WORKFLOWS = {
    "9hWlvNyCs8HmfZly": ("n8n-workflows/orders-hot.json", "orders-hot-sync", "Set Date Range"),
    "CcOBd7IELOnbonYL": ("n8n-workflows/orders-warm.json", "orders-warm-sync", "Set Date Range"),
    "QKbM3UvkJ8Yjkhb1": ("n8n-workflows/orders-cool.json", "orders-cool-sync", "Set Date Range"),
    "cgEcgz89U6Rp7UJH": ("n8n-workflows/routes-sync.json", "routes-sync", "Set Date Range"),
    "D62F3xpZ443ZFUwa": ("n8n-workflows/reference-sync.json", "reference-sync", "Fetch Hubs"),
    "JH2On4vSuJidzbyU": ("n8n-workflows/backfill.json", "backfill-sync", "Set Date Range"),
}


def api(method: str, path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{N8N_API_URL}{path}",
        data=data,
        method=method,
        headers={
            "X-N8N-API-KEY": N8N_API_KEY,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def make_webhook_node(path: str, schedule_node: dict) -> dict:
    x, y = schedule_node.get("position", [240, 300])
    webhook_id = str(uuid.uuid4())
    return {
        "parameters": {
            "httpMethod": "POST",
            "path": path,
            "responseMode": "onReceived",
            "options": {},
        },
        "id": webhook_id,
        "name": "Webhook Trigger",
        "type": "n8n-nodes-base.webhook",
        "typeVersion": 2,
        "position": [x, y + 180],
        "webhookId": webhook_id,
    }


def add_webhook_to_workflow(data: dict, webhook_path: str, target_node: str) -> dict:
    wf = deepcopy(data)
    nodes = wf.get("nodes", [])
    connections = wf.setdefault("connections", {})

    if any(n.get("name") == "Webhook Trigger" for n in nodes):
        for n in nodes:
            if n.get("name") == "Webhook Trigger":
                n["parameters"]["path"] = webhook_path
        return wf

    schedule = next(
        (n for n in nodes if n.get("type") == "n8n-nodes-base.scheduleTrigger"),
        None,
    )
    manual = next(
        (n for n in nodes if n.get("type") == "n8n-nodes-base.manualTrigger"),
        None,
    )
    anchor = schedule or manual
    if not anchor:
        raise ValueError("No schedule/manual trigger found")

    webhook = make_webhook_node(webhook_path, anchor)
    nodes.append(webhook)
    connections["Webhook Trigger"] = {
        "main": [[{"node": target_node, "type": "main", "index": 0}]]
    }
    wf["nodes"] = nodes
    return wf


def merge_live_credentials(local: dict, live: dict) -> dict:
    """Preserve Supabase keys and other live credential values on HTTP nodes."""
    live_by_name = {n["name"]: n for n in live.get("nodes", [])}
    merged = deepcopy(local)
    for node in merged["nodes"]:
        live_node = live_by_name.get(node["name"])
        if not live_node:
            continue
        if node.get("type") == "n8n-nodes-base.httpRequest":
            live_headers = (
                live_node.get("parameters", {})
                .get("headerParameters", {})
                .get("parameters", [])
            )
            if live_headers:
                node.setdefault("parameters", {}).setdefault(
                    "headerParameters", {}
                )["parameters"] = live_headers
    return merged


def main() -> None:
    results = []
    for wf_id, (rel_path, webhook_path, target) in WORKFLOWS.items():
        local_path = REPO / rel_path
        local = json.loads(local_path.read_text())
        updated_local = add_webhook_to_workflow(local, webhook_path, target)
        local_path.write_text(json.dumps(updated_local, indent=2, ensure_ascii=False) + "\n")

        live = api("GET", f"/workflows/{wf_id}")
        payload = merge_live_credentials(updated_local, live)
        put_body = {
            "name": live["name"],
            "nodes": payload["nodes"],
            "connections": payload["connections"],
            "settings": live.get("settings") or {},
            "staticData": live.get("staticData"),
        }
        api("PUT", f"/workflows/{wf_id}", put_body)

        verify = api("GET", f"/workflows/{wf_id}")
        has_webhook = any(n.get("name") == "Webhook Trigger" for n in verify["nodes"])
        path_ok = False
        for n in verify["nodes"]:
            if n.get("name") == "Webhook Trigger":
                path_ok = n.get("parameters", {}).get("path") == webhook_path
        results.append(
            {
                "id": wf_id,
                "file": rel_path,
                "webhook_path": webhook_path,
                "webhook_url": f"https://apukuski.app.n8n.cloud/webhook/{webhook_path}",
                "has_webhook": has_webhook,
                "path_ok": path_ok,
            }
        )

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
