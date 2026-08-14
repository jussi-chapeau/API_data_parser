#!/usr/bin/env python3
"""
One-time historical backfill for payments_stripe, behind payments-stripe-daily's rolling
30-day window (see n8n-workflows/payments-stripe-daily.json). Confirmed live 2026-08-13:
real Stripe checkout session data goes back to at least early-mid 2023, comfortably past
FY2025 -- default start date below is deliberately generous to cover the account's full
real history, matching the "full 2yr" precedent set by the Ads backfill.

Mirrors the N8N workflow's Transform Stripe Payments node exactly -- same field extraction,
same discount-resolution logic, same GDPR allowlist for raw_payload (no customer PII, ever).
If you change one, change both, or they'll silently diverge.

Usage:
  export STRIPE_API_KEY="rk_live_..."
  export SUPABASE_URL="https://ybznbfezrdgzgptxkgul.supabase.co"
  export SUPABASE_SERVICE_KEY="..."
  python3 scripts/backfill_payments_stripe.py --start 2023-01-01 --dry-run
  python3 scripts/backfill_payments_stripe.py --start 2023-01-01
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

MAX_TRIES = 5
BASE_BACKOFF = 2.0
UPSERT_BATCH_SIZE = 100


def stripe_request(api_key: str, url: str) -> dict:
    last_err = None
    for attempt in range(1, MAX_TRIES + 1):
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            last_err = e
            if attempt < MAX_TRIES:
                time.sleep(BASE_BACKOFF * (2 ** (attempt - 1)))
    raise RuntimeError(f"Stripe request failed after {MAX_TRIES} tries: {last_err}")


def fetch_all_sessions(api_key: str, start: date, end: date) -> list[dict]:
    created_gte = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
    created_lte = int(datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc).timestamp())

    sessions: list[dict] = []
    starting_after = None
    page = 0
    while True:
        params = [
            ("created[gte]", str(created_gte)),
            ("created[lte]", str(created_lte)),
            ("limit", "100"),
            ("expand[]", "data.payment_intent.latest_charge.balance_transaction"),
            ("expand[]", "data.total_details.breakdown"),
        ]
        if starting_after:
            params.append(("starting_after", starting_after))
        qs = "&".join(f"{k}={urllib.parse.quote(v)}" for k, v in params)
        resp = stripe_request(api_key, f"https://api.stripe.com/v1/checkout/sessions?{qs}")
        batch = resp.get("data", [])
        sessions.extend(batch)
        page += 1
        print(f"  ...page {page}, {len(batch)} sessions ({len(sessions)} total so far)")
        if not resp.get("has_more") or not batch:
            break
        starting_after = batch[-1]["id"]
    return sessions


def extract_discounts(session: dict) -> list[dict]:
    breakdown = (session.get("total_details") or {}).get("breakdown") or {}
    entries = breakdown.get("discounts") or []
    promo_ids = [d.get("promotion_code") for d in (session.get("discounts") or []) if d.get("promotion_code")]
    result = []
    for i, entry in enumerate(entries):
        coupon = (entry.get("discount") or {}).get("coupon") or {}
        result.append({
            "coupon_id": coupon.get("id"),
            "coupon_name": coupon.get("name"),
            "percent_off": coupon.get("percent_off"),
            "amount_off_cents": coupon.get("amount_off"),
            "applied_cents": entry.get("amount"),
            "promotion_code_id": promo_ids[i] if i < len(promo_ids) else None,
        })
    return result


def clean_session(session: dict) -> dict:
    pi = session.get("payment_intent")
    pi_obj = pi if isinstance(pi, dict) else None
    charge = pi_obj.get("latest_charge") if pi_obj else None
    charge_obj = charge if isinstance(charge, dict) else None
    bt = charge_obj.get("balance_transaction") if charge_obj else None
    bt_obj = bt if isinstance(bt, dict) else None

    payment_method_brand = None
    if charge_obj and charge_obj.get("payment_method_details"):
        pmd = charge_obj["payment_method_details"]
        payment_method_brand = (pmd.get("card") or {}).get("brand") if pmd.get("card") else pmd.get("type")

    return {
        "id": session.get("id"),
        "payment_intent_id": pi_obj.get("id") if pi_obj else pi,
        "charge_id": charge_obj.get("id") if charge_obj else charge,
        "status": session.get("status"),
        "payment_status": session.get("payment_status"),
        "amount_total": session.get("amount_total"),
        "currency": session.get("currency"),
        "created": session.get("created"),
        "payment_method_types": session.get("payment_method_types"),
        "charge_created": charge_obj.get("created") if charge_obj else None,
        "charge_paid": charge_obj.get("paid") if charge_obj else None,
        "amount_refunded": charge_obj.get("amount_refunded") if charge_obj else None,
        "payment_method_brand": payment_method_brand,
        "balance_transaction_fee": bt_obj.get("fee") if bt_obj else None,
        "balance_transaction_net": bt_obj.get("net") if bt_obj else None,
        "metadata": session.get("metadata") or {},
        "discounts": extract_discounts(session),
    }


def transform(session: dict) -> dict | None:
    c = clean_session(session)
    order_id = (session.get("metadata") or {}).get("order_id") or None
    primary_discount = c["discounts"][0] if c["discounts"] else None

    if not c["id"]:
        return None

    return {
        "id": c["id"],
        "payment_intent_id": c["payment_intent_id"],
        "order_id": order_id,
        "status": session.get("payment_status") or session.get("status"),
        "amount_cents": session.get("amount_total"),
        "currency": session.get("currency"),
        "payment_method_type": c["payment_method_brand"],
        "created_at": datetime.fromtimestamp(session["created"], tz=timezone.utc).isoformat() if session.get("created") else None,
        "paid_at": datetime.fromtimestamp(c["charge_created"], tz=timezone.utc).isoformat() if c["charge_paid"] and c["charge_created"] else None,
        "fee_cents": c["balance_transaction_fee"],
        "refund_amount_cents": c["amount_refunded"],
        "discount_coupon_code": primary_discount["coupon_id"] if primary_discount else None,
        "discount_percent_off": primary_discount["percent_off"] if primary_discount else None,
        "discount_amount_off_cents": primary_discount["amount_off_cents"] if primary_discount else None,
        "discount_applied_cents": primary_discount["applied_cents"] if primary_discount else None,
        "discounts": c["discounts"],
        "raw_payload": c,
        "synced_at": datetime.now(tz=timezone.utc).isoformat(),
    }


def upsert(supabase_url: str, service_key: str, rows: list[dict]) -> None:
    for i in range(0, len(rows), UPSERT_BATCH_SIZE):
        batch = rows[i:i + UPSERT_BATCH_SIZE]
        body = json.dumps(batch).encode("utf-8")
        req = urllib.request.Request(
            f"{supabase_url.rstrip('/')}/rest/v1/payments_stripe?on_conflict=id",
            data=body, method="POST",
            headers={
                "apikey": service_key,
                "Authorization": f"Bearer {service_key}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            resp.read()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", help="YYYY-MM-DD, default today")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    api_key = os.environ.get("STRIPE_API_KEY")
    supabase_url = os.environ.get("SUPABASE_URL")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not all([api_key, supabase_url, service_key]):
        print("ERROR: STRIPE_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY must all be set.", file=sys.stderr)
        return 1

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else date.today()

    print(f"Fetching checkout sessions {start} .. {end}...")
    sessions = fetch_all_sessions(api_key, start, end)
    print(f"\nTotal sessions fetched: {len(sessions)}")

    rows = [transform(s) for s in sessions]
    rows = [r for r in rows if r]

    with_order_id = sum(1 for r in rows if r["order_id"])
    with_discount = sum(1 for r in rows if r["discount_coupon_code"])
    paid = sum(1 for r in rows if r["status"] == "paid")
    print(f"Transformed rows: {len(rows)}")
    print(f"  paid: {paid}")
    print(f"  with order_id: {with_order_id}")
    print(f"  with a discount code: {with_discount}")

    if args.dry_run:
        print("\n--dry-run: not writing to Supabase.")
        return 0

    print(f"\nUpserting {len(rows)} rows...")
    upsert(supabase_url, service_key, rows)
    print("OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
