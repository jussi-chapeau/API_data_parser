"""
Shared Backoffice-order -> Supabase-row transform.

This is a faithful Python mirror of the `Transform Orders` Code node in
n8n-workflows/orders-{hot,warm,cool}.json and backfill.json. The N8N node is the primary
implementation (it runs on every scheduled sync); this exists so repair/backfill paths write
*identical* rows rather than a subtly different shape that then looks like drift.

IF YOU CHANGE ONE, CHANGE BOTH. tests/test_order_transform.py pins the behaviours that have
actually bitten us before, so a divergence should fail there rather than silently produce
wrong money columns.

Behaviours that are load-bearing and non-obvious:
  * `created` is an epoch STRING ("1779599528.739927"), never ISO -- verified across all
    2,196 May-Aug orders. Normal for this API; don't "fix" it upstream.
  * charge amounts are integer cents for most orders but decimal euros for a real cohort
    (~20% of recent platform orders) with no distinguishing flag -- BACKOFFICE_API_ISSUES.md
    #2. A non-integer value can only be euros, since cents are whole numbers.
  * `total_excl_vat_cents` is välitetty myynti excl. VAT, NOT liikevaihto. See
    docs/REVENUE_DEFINITIONS.md.
  * Asuntosäätiö B2B moves are recorded as EUR 0 upstream and overridden to the real flat
    rate; manual orders only.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

ASUNTOSAATIO_EXCL_VAT_EUR = 400
ASUNTOSAATIO_INCL_VAT_EUR = 502

_PLAIN_NUMBER = re.compile(r"^[-+]?\d+(\.\d+)?$")


def ts_to_iso(val):
    """Epoch-seconds string/number -> ISO 8601. Passes through values already ISO.

    The `-` check on the first 10 chars is deliberate: a bare epoch like "1779599528" has
    leading digits that look like a year to a naive `[:4].isdigit()` test, which silently
    produced an invalid timestamp and a Postgres 400 the first time this was written.
    """
    if val is None or val == "" or val == 0:
        return None
    if isinstance(val, str) and ("T" in val or "-" in val[:10]):
        return val
    try:
        n = float(val)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(n, tz=timezone.utc).isoformat()


def parse_charge_amount_cents(value):
    """Charge amount -> integer cents, branching on the decimal-euro cohort (issue #2)."""
    if value is None or value == "":
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return round(n) if float(n).is_integer() else round(n * 100)


def parse_manual_total(charge):
    """`/manual-order` gives only a VAT-inclusive free-text euro string. Never coerce
    non-numeric values (e.g. "119e/h") into a total -- keep the raw string instead."""
    if not charge or charge.get("charge") in (None, ""):
        return {"total_incl_vat_eur": None, "total_incl_vat_cents": None, "raw_charge_total": None}
    raw = str(charge.get("charge")).strip()
    cleaned = (raw.replace("\xa0", "").replace("€", "").replace("EUR", "")
                  .replace("eur", "").replace(" ", "").replace(",", "."))
    if not _PLAIN_NUMBER.match(cleaned):
        return {"total_incl_vat_eur": None, "total_incl_vat_cents": None, "raw_charge_total": raw}
    eur = float(cleaned)
    return {"total_incl_vat_eur": eur, "total_incl_vat_cents": round(eur * 100),
            "raw_charge_total": raw}


def _mentions_asuntosaatio(d) -> bool:
    try:
        return "asuntosäätiö" in json.dumps(d, ensure_ascii=False).lower()
    except (TypeError, ValueError):
        return False


def transform_order(d: dict) -> dict | None:
    """Backoffice order (either endpoint) -> a Supabase `orders` row. None if unusable."""
    if not d.get("orderId"):
        return None

    is_manual = (
        d.get("isManual") is True
        or d.get("isManual") == "true"
        or (d.get("customer") is not None and d.get("additionalInfo") is not None)
    )

    charge = d.get("charge") or {}
    manual_data = None
    is_asuntosaatio = "No"

    if is_manual:
        pricing = parse_manual_total(charge)
        override = _mentions_asuntosaatio(d) and pricing["total_incl_vat_eur"] == 0
        is_asuntosaatio = "Yes" if override else "No"
        manual_data = {
            "customer": d.get("customer") or None,
            "additionalInfo": d.get("additionalInfo") or None,
            "serviceFeeApplied": d.get("serviceFeeApplied") or None,
            "total_incl_vat_eur": ASUNTOSAATIO_INCL_VAT_EUR if override else pricing["total_incl_vat_eur"],
            "total_incl_vat_cents": ASUNTOSAATIO_INCL_VAT_EUR * 100 if override else pricing["total_incl_vat_cents"],
            "total_excl_vat_eur": ASUNTOSAATIO_EXCL_VAT_EUR if override else None,
            "total_excl_vat_cents": ASUNTOSAATIO_EXCL_VAT_EUR * 100 if override else None,
            "price_basis": "gross_incl_vat",
            "raw_charge_total": pricing["raw_charge_total"],
        }
        if override:
            manual_data["original_total_incl_vat_eur"] = pricing["total_incl_vat_eur"]
            manual_data["value_override_reason"] = "asuntosaatio_b2b_flat_rate"

    base = None if is_manual else parse_charge_amount_cents(charge.get("basePrice"))
    services = None if is_manual else parse_charge_amount_cents(charge.get("servicesPrice"))
    recycling = None if is_manual else parse_charge_amount_cents(charge.get("recyclingSurcharge"))
    platform_fee = None if is_manual else parse_charge_amount_cents(charge.get("platformFee"))
    service_fee = None if is_manual else parse_charge_amount_cents(charge.get("serviceFee"))
    total_excl_vat = (
        base + platform_fee + (services or 0) + (recycling or 0)
        if (not is_manual and base is not None and platform_fee is not None) else None
    )

    return {
        "order_id": d["orderId"],
        "is_manual": is_manual,
        "created_at": ts_to_iso(d.get("created") or d.get("createdAt")),
        "organization_name": d.get("organizationName") or None,
        "org_id": d.get("orgId") or None,
        "hub_id": d.get("hubId") or None,
        "order_state": d.get("orderState") or None,
        "order_type": d.get("orderType") or None,
        "origin": None if is_manual else (d.get("origin") or None),
        "first_schedule": ts_to_iso(d.get("firstSchedule")),
        "schedule": d.get("schedule") or None,
        "content": d.get("content") or None,
        "stops": d.get("stops") or None,
        "charge": d.get("charge") or None,
        "platform_fee": platform_fee,
        "service_fee": service_fee,
        "base_price_cents": base,
        "services_price_cents": services,
        "recycling_surcharge_cents": recycling,
        "total_excl_vat_cents": total_excl_vat,
        "route_id": d.get("routeId") or None,
        "commission_rate": float(d["commissionRate"]) if d.get("commissionRate") is not None else None,
        "underway_at": ts_to_iso(d.get("underwayAt")),
        "in_transit_at": ts_to_iso(d.get("inTransitAt")),
        "delivered_at": ts_to_iso(d.get("deliveredAt")),
        "review": d.get("review") or None,
        "manual_data": manual_data,
        "is_asuntosaatio_gig": is_asuntosaatio,
        "synced_at": datetime.now(tz=timezone.utc).isoformat(),
    }


def dedupe_by_order_id(rows: list[dict]) -> list[dict]:
    """Last occurrence wins. A repeated id in one batch raises
    'ON CONFLICT DO UPDATE command cannot affect row a second time', failing the WHOLE
    batch rather than just that row."""
    by_id = {}
    for r in rows:
        by_id[r["order_id"]] = r
    return list(by_id.values())
