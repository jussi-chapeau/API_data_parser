"""
tools.py — apukuski-bi-chatbot tools.

Primary data: Supabase Postgres (orders, routes, hubs, sync_log).
Optional live fallback: Backoffice AWS API.

Register new tools in TOOLS_SCHEMA + TOOL_IMPLEMENTATIONS.
"""

import json
import logging
import re
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

import httpx

from app.config import (
    BACKOFFICE_API_KEY,
    BACKOFFICE_API_URL,
    BACKOFFICE_TIMEOUT,
    CHIEF_NAME,
    DATABASE_URL,
    SLACK_BRIDGE_TIMEOUT,
    SLACK_BRIDGE_URL,
    TRAINING_SERVICE_URL,
    TRAINING_TIMEOUT,
)

log = logging.getLogger("apukuski_bi.tools")

_FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|grant|revoke|create|truncate|copy|vacuum)\b",
    re.IGNORECASE,
)

DB_READ_SCHEMA = {
    "type": "function",
    "function": {
        "name": "db_read",
        "description": (
            "Run read-only SQL (SELECT/WITH) against Supabase Postgres. "
            "Tables:\n"
            "- orders (~9500+ rows): order_id, is_manual, created_at, organization_name, "
            "org_id, hub_id, order_state, order_type, origin, first_schedule, schedule, "
            "content, stops, charge (jsonb), platform_fee, service_fee (cents, excl VAT), "
            "route_id, commission_rate, delivered_at, manual_data (jsonb), synced_at\n"
            "- routes: route_id, date, hub_id, partner, internal_cost, order_ids, total_sales\n"
            "- hubs: hub_id, hub_name, hub_location, opening_hours, service_info, tz\n"
            "- sync_log: workflow, started_at, completed_at, status, rows_upserted\n\n"
            "Use aggregates (COUNT, SUM, date_trunc). Max 200 rows returned. "
            "See vault/BI.md for platform vs manual pricing rules."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "SELECT or WITH query"},
            },
            "required": ["sql"],
        },
    },
}


async def db_read(sql: str) -> str:
    if not DATABASE_URL:
        return json.dumps({"error": "DATABASE_URL not set."}, ensure_ascii=False)
    cleaned = re.sub(r"--.*?$|/\*.*?\*/", "", sql, flags=re.MULTILINE | re.DOTALL).strip()
    if not re.match(r"^(select|with)\b", cleaned, re.IGNORECASE):
        return json.dumps({"error": "Only SELECT/WITH allowed."}, ensure_ascii=False)
    if ";" in cleaned.rstrip(";"):
        return json.dumps({"error": "Single statement only."}, ensure_ascii=False)
    if _FORBIDDEN_SQL.search(cleaned):
        return json.dumps({"error": "Forbidden keyword in query."}, ensure_ascii=False)

    try:
        import asyncpg
    except ImportError:
        return json.dumps({"error": "asyncpg not installed."}, ensure_ascii=False)

    try:
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            rows = await conn.fetch(cleaned)
        finally:
            await conn.close()
    except Exception as e:
        return json.dumps({"error": f"SQL error: {type(e).__name__}: {e}"}, ensure_ascii=False)

    out = [dict(r) for r in rows[:200]]
    return json.dumps(
        {"rows": out, "count": len(out), "truncated": len(rows) > 200},
        ensure_ascii=False,
        default=str,
    )


async def _backoffice_get(path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    if not BACKOFFICE_API_URL or not BACKOFFICE_API_KEY:
        return {"error": "Backoffice API not configured."}
    url = BACKOFFICE_API_URL.rstrip("/") + "/" + path.lstrip("/")
    try:
        async with httpx.AsyncClient(timeout=BACKOFFICE_TIMEOUT) as client:
            resp = await client.get(
                url,
                params=params or {},
                headers={"x-api-key": BACKOFFICE_API_KEY},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        return {
            "error": f"Backoffice HTTP {e.response.status_code}",
            "detail": e.response.text[:200],
        }
    except Exception as e:
        return {"error": f"Backoffice call failed: {type(e).__name__}: {e}"}


BACKOFFICE_GET_HUBS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "backoffice_get_hubs",
        "description": (
            "List hubs from live Backoffice API (hubId, hubName, address). "
            "Prefer db_read on hubs table unless you need freshest live data."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


async def backoffice_get_hubs() -> str:
    data = await _backoffice_get("hub")
    if isinstance(data, list):
        slim = [
            {
                "hubId": h.get("hubId"),
                "hubName": h.get("hubName"),
                "address": (h.get("hubLocation") or {}).get("address"),
            }
            for h in data
        ]
        return json.dumps({"hubs": slim, "count": len(slim)}, ensure_ascii=False)
    return json.dumps(data, ensure_ascii=False)


BI_ORDERS_REPORT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "bi_orders_report",
        "description": (
            "Order volume and revenue report from Supabase orders table. "
            "Counts by order_state; revenue excludes CANCELLED. "
            "Platform orders: customer payment ≈ charge.vatPrice + charge.serviceFee (cents). "
            "Manual orders: manual_data.total_incl_vat_cents (gross incl VAT). "
            "date_basis='created' filters on created_at; 'schedule' on first_schedule. "
            "Always state which basis was used."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "YYYY-MM-DD"},
                "group_by": {
                    "type": "string",
                    "enum": ["none", "day", "hub", "order_type"],
                    "description": "Grouping (default none)",
                },
                "date_basis": {
                    "type": "string",
                    "enum": ["created", "schedule"],
                    "description": "Filter basis (default created)",
                },
                "include_manual": {
                    "type": "boolean",
                    "description": "Include manual orders (default true)",
                },
            },
            "required": ["start_date", "end_date"],
        },
    },
}


def _cents(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return 0


def _order_revenue_cents_backoffice(order: Dict[str, Any]) -> int:
    charge = order.get("charge") or {}
    vat = charge.get("vatPrice", order.get("vatPrice"))
    fee = charge.get("serviceFee", order.get("serviceFee"))
    return _cents(vat) + _cents(fee)


def _order_date_backoffice(order: Dict[str, Any], basis: str) -> Optional[str]:
    if basis == "schedule":
        sched = order.get("firstSchedule") or ""
        if sched:
            return str(sched)[:10]
        arr = order.get("schedule") or []
        if arr:
            return str(arr[0])[:10]
        return None
    created = order.get("created") or order.get("createdAt") or ""
    return str(created)[:10] if created else None


async def bi_orders_report(
    start_date: str,
    end_date: str,
    group_by: str = "none",
    date_basis: str = "created",
    include_manual: bool = True,
) -> str:
    if not DATABASE_URL:
        return json.dumps({"error": "DATABASE_URL not set — cannot query Supabase."}, ensure_ascii=False)

    try:
        datetime.strptime(start_date, "%Y-%m-%d")
        datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        return json.dumps({"error": "Dates must be YYYY-MM-DD."}, ensure_ascii=False)

    date_col = "first_schedule" if date_basis == "schedule" else "created_at"
    manual_filter = "" if include_manual else "AND is_manual = false"

    if group_by == "day":
        group_expr = f"date_trunc('day', {date_col})::date"
        group_label = "day"
    elif group_by == "hub":
        group_expr = "COALESCE(hub_id, organization_name, 'unknown')"
        group_label = "hub"
    elif group_by == "order_type":
        group_expr = "COALESCE(order_type, 'unknown')"
        group_label = "order_type"
    else:
        group_expr = "'total'"
        group_label = "group"

    sql = f"""
    SELECT
      {group_expr} AS {group_label},
      COUNT(*) AS orders_total,
      COUNT(*) FILTER (WHERE UPPER(order_state) = 'DELIVERED') AS delivered,
      COUNT(*) FILTER (WHERE UPPER(order_state) = 'CANCELLED') AS cancelled,
      COUNT(*) FILTER (
        WHERE UPPER(order_state) NOT IN ('DELIVERED', 'CANCELLED')
      ) AS other_state,
      COALESCE(SUM(
        CASE WHEN UPPER(order_state) = 'CANCELLED' THEN 0
        WHEN is_manual THEN COALESCE((manual_data->>'total_incl_vat_cents')::bigint, 0)
        ELSE
          COALESCE(
            NULLIF(regexp_replace(charge->>'vatPrice', '[^0-9-]', '', 'g'), '')::bigint,
            0
          ) + COALESCE(
            NULLIF(regexp_replace(charge->>'serviceFee', '[^0-9-]', '', 'g'), '')::bigint,
            0
          )
        END
      ), 0) AS revenue_cents
    FROM orders
    WHERE {date_col}::date >= '{start_date}'::date
      AND {date_col}::date <= '{end_date}'::date
      {manual_filter}
    GROUP BY 1
    ORDER BY 1
    """

    raw = await db_read(sql)
    data = json.loads(raw)
    if "error" in data:
        return raw

    groups = []
    for row in data.get("rows", []):
        active = row["orders_total"] - row["cancelled"]
        rev_cents = row["revenue_cents"] or 0
        groups.append({
            "group": str(row.get(group_label, row.get("group", "?"))),
            "orders_total": row["orders_total"],
            "delivered": row["delivered"],
            "cancelled": row["cancelled"],
            "in_progress_or_other": row["other_state"],
            "revenue_eur": round(rev_cents / 100, 2),
            "avg_order_eur": round(rev_cents / 100 / active, 2) if active else 0,
        })

    return json.dumps({
        "source": "supabase",
        "period": f"{start_date} – {end_date}",
        "date_basis": date_basis,
        "groups": groups,
        "note": (
            "Supabase synced data. Platform revenue = charge.vatPrice + charge.serviceFee; "
            "manual = manual_data.total_incl_vat_cents. CANCELLED excluded from revenue. "
            "Fees in platform_fee/service_fee columns are excl VAT — do not use as customer total."
        ),
    }, ensure_ascii=False, default=str)


BI_REVENUE_REPORT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "bi_revenue_report",
        "description": (
            "Live Backoffice API order report (AWS Lambda). Use when Supabase sync lag "
            "matters or for dates outside sync window. Same date_basis semantics as "
            "bi_orders_report. Prefer bi_orders_report for historical analytics."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
                "group_by": {"type": "string", "enum": ["none", "day", "hub"]},
                "date_basis": {"type": "string", "enum": ["created", "schedule"]},
                "include_manual": {"type": "boolean"},
            },
            "required": ["start_date", "end_date"],
        },
    },
}


async def bi_revenue_report(
    start_date: str,
    end_date: str,
    group_by: str = "none",
    date_basis: str = "created",
    include_manual: bool = True,
) -> str:
    try:
        d_start = datetime.strptime(start_date, "%Y-%m-%d").date()
        d_end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        return json.dumps({"error": "Dates must be YYYY-MM-DD."}, ensure_ascii=False)

    if date_basis == "schedule":
        fetch_start = (d_start - timedelta(days=60)).isoformat()
        fetch_end = (d_end + timedelta(days=1)).isoformat()
    else:
        fetch_start, fetch_end = start_date, end_date

    orders: List[Dict[str, Any]] = []
    paths = ["order"] + (["manual-order"] if include_manual else [])
    for path in paths:
        data = await _backoffice_get(path, {"start_date": fetch_start, "end_date": fetch_end})
        if isinstance(data, list):
            orders.extend(data)
        elif isinstance(data, dict) and "error" in data:
            return json.dumps(data, ensure_ascii=False)

    in_range = []
    for o in orders:
        d = _order_date_backoffice(o, date_basis)
        if d and start_date <= d <= end_date:
            in_range.append(o)

    def _bucket(o: Dict[str, Any]) -> str:
        if group_by == "day":
            return _order_date_backoffice(o, date_basis) or "?"
        if group_by == "hub":
            return o.get("organizationName") or o.get("orgId") or o.get("hubId") or "oma tuotanto"
        return "yhteensä"

    groups: Dict[str, Dict[str, Any]] = {}
    for o in in_range:
        b = groups.setdefault(_bucket(o), {
            "orders": 0, "delivered": 0, "cancelled": 0, "other_state": 0,
            "revenue_cents": 0,
        })
        b["orders"] += 1
        state = (o.get("orderState") or "").upper()
        if state == "CANCELLED":
            b["cancelled"] += 1
            continue
        if state == "DELIVERED":
            b["delivered"] += 1
        else:
            b["other_state"] += 1
        b["revenue_cents"] += _order_revenue_cents_backoffice(o)

    result = []
    for name in sorted(groups):
        g = groups[name]
        active = g["orders"] - g["cancelled"]
        result.append({
            "group": name,
            "orders_total": g["orders"],
            "delivered": g["delivered"],
            "cancelled": g["cancelled"],
            "in_progress_or_other": g["other_state"],
            "revenue_eur": round(g["revenue_cents"] / 100, 2),
            "avg_order_eur": round(g["revenue_cents"] / 100 / active, 2) if active else 0,
        })

    return json.dumps({
        "source": "backoffice",
        "period": f"{start_date} – {end_date}",
        "date_basis": date_basis,
        "groups": result,
        "total_orders": len(in_range),
    }, ensure_ascii=False)


SEARCH_KNOWLEDGE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_knowledge",
        "description": "Search operator-taught BI knowledge base (/opeta bi in Slack).",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
}


async def search_knowledge(query: str) -> str:
    if not TRAINING_SERVICE_URL:
        return json.dumps({"error": "TRAINING_SERVICE_URL not set."}, ensure_ascii=False)
    try:
        async with httpx.AsyncClient(timeout=TRAINING_TIMEOUT) as client:
            resp = await client.get(
                f"{TRAINING_SERVICE_URL.rstrip('/')}/search/{CHIEF_NAME}",
                params={"q": query},
            )
            resp.raise_for_status()
            return json.dumps(resp.json(), ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"Knowledge search failed: {e}"}, ensure_ascii=False)


FLAG_UNCERTAINTY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "flag_uncertainty",
        "description": "Escalate uncertain data interpretation to operators via Slack bridge.",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "context": {"type": "string"},
            },
            "required": ["question"],
        },
    },
}


async def flag_uncertainty(question: str, context: str = "") -> str:
    if not SLACK_BRIDGE_URL:
        return json.dumps({
            "status": "not_configured",
            "note": "State uncertainty directly in your reply.",
        }, ensure_ascii=False)
    try:
        async with httpx.AsyncClient(timeout=SLACK_BRIDGE_TIMEOUT) as client:
            await client.post(
                f"{SLACK_BRIDGE_URL.rstrip('/')}/flag-low-confidence",
                json={"chief": CHIEF_NAME, "question": question, "context": context},
            )
        return json.dumps({"status": "flagged"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "failed", "error": str(e)}, ensure_ascii=False)


TOOLS_SCHEMA: List[Dict[str, Any]] = [
    DB_READ_SCHEMA,
    BI_ORDERS_REPORT_SCHEMA,
    BI_REVENUE_REPORT_SCHEMA,
    BACKOFFICE_GET_HUBS_SCHEMA,
    SEARCH_KNOWLEDGE_SCHEMA,
    FLAG_UNCERTAINTY_SCHEMA,
]

TOOL_IMPLEMENTATIONS = {
    "db_read": db_read,
    "bi_orders_report": bi_orders_report,
    "bi_revenue_report": bi_revenue_report,
    "backoffice_get_hubs": backoffice_get_hubs,
    "search_knowledge": search_knowledge,
    "flag_uncertainty": flag_uncertainty,
}
