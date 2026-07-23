#!/usr/bin/env python3
"""Update Transform Orders jsCode for manual pricing and push to live N8N."""

from __future__ import annotations

import json
import urllib.request
from copy import deepcopy
from pathlib import Path

N8N_API_URL = "https://apukuski.app.n8n.cloud/api/v1"
SETTINGS = Path(__file__).resolve().parents[1] / ".claude" / "settings.json"
N8N_API_KEY = json.loads(SETTINGS.read_text())["mcpServers"]["n8n"]["env"]["N8N_API_KEY"]

REPO = Path(__file__).resolve().parents[1]

WORKFLOWS = {
    "9hWlvNyCs8HmfZly": "n8n-workflows/orders-hot.json",
    "CcOBd7IELOnbonYL": "n8n-workflows/orders-warm.json",
    "QKbM3UvkJ8Yjkhb1": "n8n-workflows/orders-cool.json",
    "JH2On4vSuJidzbyU": "n8n-workflows/backfill.json",
}

TRANSFORM_JS = r"""function tsToIso(val) {
  if (val === null || val === undefined || val === '') return null;
  if (typeof val === 'string' && (val.includes('T') || /^\d{4}-/.test(val))) return val;
  const n = parseFloat(val);
  if (isNaN(n)) return null;
  return new Date(n * 1000).toISOString();
}

function parseManualTotal(charge) {
  if (!charge || charge.charge === null || charge.charge === undefined || charge.charge === '') {
    return { totalInclVatEur: null, totalInclVatCents: null, rawChargeTotal: null };
  }
  const rawChargeTotal = String(charge.charge).trim();
  const cleaned = rawChargeTotal
    .replace(/\u00a0/g, '')
    .replace(/€/g, '')
    .replace(/EUR/gi, '')
    .replace(/\s/g, '')
    .replace(',', '.');
  if (!/^[-+]?\d+(\.\d+)?$/.test(cleaned)) {
    return { totalInclVatEur: null, totalInclVatCents: null, rawChargeTotal };
  }
  const totalInclVatEur = parseFloat(cleaned);
  return {
    totalInclVatEur,
    totalInclVatCents: Math.round(totalInclVatEur * 100),
    rawChargeTotal,
  };
}

const results = [];

for (const item of $input.all()) {
  const d = item.json;

  const isManual =
    d.isManual === true ||
    d.isManual === 'true' ||
    (d.customer !== undefined && d.additionalInfo !== undefined);

  const manualPricing = isManual ? parseManualTotal(d.charge || null) : null;

  const transformed = {
    order_id: d.orderId,
    is_manual: isManual,
    created_at: tsToIso(d.created || d.createdAt),
    organization_name: d.organizationName || null,
    org_id: d.orgId || null,
    hub_id: d.hubId || null,
    order_state: d.orderState || null,
    order_type: d.orderType || null,
    first_schedule: tsToIso(d.firstSchedule),
    schedule: d.schedule || null,
    content: d.content || null,
    stops: d.stops || null,
    charge: d.charge || null,
    platform_fee: isManual
      ? null
      : d.platformFee !== undefined && d.platformFee !== null
        ? Math.round(parseFloat(d.platformFee))
        : null,
    service_fee: isManual
      ? null
      : d.serviceFee !== undefined && d.serviceFee !== null
        ? Math.round(parseFloat(d.serviceFee))
        : null,
    route_id: d.routeId || null,
    commission_rate: d.commissionRate !== undefined ? parseFloat(d.commissionRate) : null,
    underway_at: tsToIso(d.underwayAt),
    in_transit_at: tsToIso(d.inTransitAt),
    delivered_at: tsToIso(d.deliveredAt),
    review: d.review || null,
    manual_data: isManual
      ? {
          customer: d.customer || null,
          additionalInfo: d.additionalInfo || null,
          serviceFeeApplied: d.serviceFeeApplied || null,
          total_incl_vat_eur: manualPricing.totalInclVatEur,
          total_incl_vat_cents: manualPricing.totalInclVatCents,
          price_basis: 'gross_incl_vat',
          raw_charge_total: manualPricing.rawChargeTotal,
        }
      : null,
    synced_at: new Date().toISOString(),
  };

  if (transformed.order_id) {
    results.push({ json: transformed });
  }
}

return results;
"""


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


def merge_live_credentials(local: dict, live: dict) -> dict:
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


def update_transform(workflow: dict) -> bool:
    changed = False
    for node in workflow.get("nodes", []):
        if node.get("name") == "Transform Orders":
            if node["parameters"].get("jsCode") != TRANSFORM_JS:
                node["parameters"]["jsCode"] = TRANSFORM_JS
                changed = True
    return changed


def main() -> None:
    results = []
    for wf_id, rel_path in WORKFLOWS.items():
        path = REPO / rel_path
        local = json.loads(path.read_text())
        changed = update_transform(local)
        path.write_text(json.dumps(local, indent=2, ensure_ascii=False) + "\n")

        live = api("GET", f"/workflows/{wf_id}")
        payload = merge_live_credentials(local, live)
        put_body = {
            "name": live["name"],
            "nodes": payload["nodes"],
            "connections": payload["connections"],
            "settings": live.get("settings") or {},
            "staticData": live.get("staticData"),
        }
        api("PUT", f"/workflows/{wf_id}", put_body)

        verify = api("GET", f"/workflows/{wf_id}")
        code = next(
            n["parameters"]["jsCode"]
            for n in verify["nodes"]
            if n.get("name") == "Transform Orders"
        )
        results.append(
            {
                "id": wf_id,
                "file": rel_path,
                "repo_updated": changed,
                "live_has_manual_pricing": "total_incl_vat_cents" in code,
            }
        )

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
