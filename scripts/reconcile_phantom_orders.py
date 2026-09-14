#!/usr/bin/env python3
"""
Detect "phantom" orders: rows still in Supabase that no longer exist upstream in Backoffice.

The orders sync is upsert-only and has no delete path, so anything Backoffice deletes stays
in Supabase forever -- inflating BI counts and retaining customer addresses in `stops` past
the controller's own deletion (GDPR Art. 5(1)(e); see apukuski-bi-chatbot
docs/GDPR_REVIEW.md issue #8). August 2026 overstated by 34 gigs / EUR 16,520 this way.

Reconciles BOTH directions per day, because drift happens both ways and only one of them was
ever being watched:
  * Supabase-only (present here, gone upstream) -> proposed for deletion. DESTRUCTIVE, so it
    only ever writes a `pending` candidate; a human approves via the BI bot and
    scripts/delete_approved_orders.py does the removal.
  * Backoffice-only (present upstream, missing here) -> re-fetched and upserted immediately.
    ADDITIVE, so no approval gate: re-adding a row that provably exists upstream cannot lose
    data, and requiring a click would recreate the exact failure this catches. One May 2026
    order sat missing for three months because nothing watched this direction.

THIS SCRIPT NEVER DELETES ANYTHING.

Abort rails (the important part -- a stalled sync or a flaky API must never be mistaken for
mass upstream deletion):
  1. ANY day's fetch failing after retries aborts the whole run, writing nothing. A
     timed-out chunk is indistinguishable from "everything was deleted".
  2a. Any single day returning ZERO upstream ids while Supabase holds rows for it aborts --
      an empty-but-successful response is not a retryable error, so rail 1 never sees it.
  2b. Candidate volume > MAX_CANDIDATES rows or > MAX_CANDIDATE_PCT of the window aborts.
  3. A day with no successful order-sync run covering it aborts -- if the pipeline itself
     is unhealthy, we have no business proposing deletions from its output.
Rails are evaluated BEFORE anything is written. Abort = write nothing at all, not
write-then-rollback.

Usage:
  export SUPABASE_URL=... SUPABASE_SERVICE_KEY=...
  export BACKOFFICE_API_URL=... BACKOFFICE_API_KEY=...

  # dry run over the default trailing window (safe, read-only, writes nothing)
  python3 scripts/reconcile_phantom_orders.py --dry-run

  # dry run over an explicit range (used for the acceptance test)
  python3 scripts/reconcile_phantom_orders.py --dry-run --start 2026-08-01 --end 2026-08-31

  # real run -- writes pending candidates, still deletes nothing
  python3 scripts/reconcile_phantom_orders.py
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
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from order_transform import dedupe_by_order_id, transform_order  # noqa: E402

WINDOW_DAYS = 45
# Skip the most recent days: same-day/next-day indexing lag on /order means a real order can
# be missing from the API briefly and would look deleted. docs/BACKOFFICE_API_ISSUES.md #13.
SKIP_RECENT_DAYS = 2

MAX_TRIES = 6
BASE_BACKOFF = 1.0

# Abort thresholds. 50 is comfortably above any plausible real deletion batch we've seen
# (the largest real incident was 34) and far below "the API returned nothing".
MAX_CANDIDATES = 50
# Raised from 5.0 -> 10.0 on 2026-09-02, deliberately, and only because rail 2a now exists.
# The real August incident (34 phantoms in a 681-row month) came in at 4.993% and passed the
# old ceiling by 0.007pp -- i.e. the rail very nearly blocked the exact detection it was
# built to permit, and a genuine incident in a quieter month would have tripped it. The 5%
# figure was a blunt proxy for "the API returned nothing"; rail 2a now detects that case
# exactly and per-day, so this can be relaxed without losing the protection it stood in for.
# The 50-row absolute ceiling is unchanged and still binds first on any large window.
MAX_CANDIDATE_PCT = 10.0

# Workflows whose successful completion means "the orders table was refreshed".
ORDER_SYNC_WORKFLOWS = ("orders-hot", "orders-warm", "orders-cool", "backfill")


class AbortRun(Exception):
    """Raised when an abort rail trips. Nothing has been written when this propagates."""


# --------------------------------------------------------------------------- fetch helpers

def _get_json(url: str, headers: dict, timeout: int = 30):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:
            raise RuntimeError(f"non-200: {resp.status}")
        return json.loads(resp.read().decode("utf-8"))


def fetch_live_orders_for_day(api_base: str, api_key: str, day: date) -> dict[str, dict]:
    """Authoritative {order_id: order} for `day`, from both endpoints.

    Returns full objects, not just ids, because the repair direction needs the payload to
    upsert a missing row without a second round-trip.

    Day-by-day, never multi-day: 30-day /order windows fail ~47% of the time with 500/504
    (docs/BACKOFFICE_API_ISSUES.md #7). Raises AbortRun if either endpoint can't be read --
    an unreadable day must abort the run, never silently yield "no orders upstream".
    """
    orders: dict[str, dict] = {}
    d = day.isoformat()

    for path, extra in (
        # includeUnscheduled=true is REQUIRED on /order: without it ~96 real orders per
        # window are omitted and look deleted (docs/BACKOFFICE_API_ISSUES.md #12).
        ("order", {"includeUnscheduled": "true"}),
        # ...and must NOT be passed to /manual-order, which 400s on it (docs/GOTCHAS.md).
        ("manual-order", {}),
    ):
        params = {"start_date": d, "end_date": d, **extra}
        url = f"{api_base.rstrip('/')}/{path}?{urllib.parse.urlencode(params)}"
        last_err = None
        for attempt in range(1, MAX_TRIES + 1):
            try:
                data = _get_json(url, {"x-api-key": api_key})
                if not isinstance(data, list):
                    raise RuntimeError(f"unexpected response shape: {str(data)[:200]}")
                for o in data:
                    oid = o.get("orderId")
                    if oid:
                        orders[oid] = o
                break
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
                    RuntimeError, json.JSONDecodeError) as e:
                last_err = e
                if attempt < MAX_TRIES:
                    time.sleep(BASE_BACKOFF * (2 ** (attempt - 1)))
        else:
            raise AbortRun(
                f"ABORT RAIL 1: /{path} for {d} failed after {MAX_TRIES} tries "
                f"({last_err}). A day we cannot read is indistinguishable from a day "
                f"where everything was deleted -- refusing to propose any candidates."
            )
    return orders


def fetch_supabase_rows_for_day(base_url: str, key: str, day: date) -> list[dict]:
    """All Supabase order rows whose created_at falls on `day` (UTC).

    No order_state filter on either side of the diff: a CANCELLED order still exists
    upstream and is still returned by /order, so it is not a phantom.
    """
    nxt = day + timedelta(days=1)
    url = (
        f"{base_url.rstrip('/')}/rest/v1/orders"
        f"?created_at=gte.{day.isoformat()}T00:00:00Z"
        f"&created_at=lt.{nxt.isoformat()}T00:00:00Z"
        f"&select=order_id,created_at,is_manual,order_state,organization_name,hub_id,"
        f"total_excl_vat_cents,synced_at"
    )
    return _get_json(url, {"apikey": key, "Authorization": f"Bearer {key}"}, timeout=60)


def sync_log_days_covered(base_url: str, key: str, since: date) -> list[dict]:
    """Successful order-sync runs completing on/after `since`.

    Ordered by completed_at, NOT started_at: started_at is NULL on every order-sync row in
    this table (verified 2026-09-02 -- the Log Sync node never set it).
    """
    wf = ",".join(f'"{w}"' for w in ORDER_SYNC_WORKFLOWS)
    url = (
        f"{base_url.rstrip('/')}/rest/v1/sync_log"
        f"?workflow=in.({wf})&status=eq.success"
        f"&completed_at=gte.{since.isoformat()}T00:00:00Z"
        f"&select=workflow,completed_at,status&order=completed_at.desc"
    )
    return _get_json(url, {"apikey": key, "Authorization": f"Bearer {key}"}, timeout=60)


# --------------------------------------------------------------------------- core

def build_evidence(row: dict, day: date, live_count: int, supa_count: int) -> dict:
    """Everything a human needs to review this candidate without re-querying anything."""
    return {
        "reason": "present_in_supabase_absent_upstream",
        "checked_day": day.isoformat(),
        "live_api_ids_that_day": live_count,
        "supabase_rows_that_day": supa_count,
        "order": {
            "created_at": row.get("created_at"),
            "is_manual": row.get("is_manual"),
            "order_state": row.get("order_state"),
            "organization_name": row.get("organization_name"),
            "hub_id": row.get("hub_id"),
            "total_excl_vat_cents": row.get("total_excl_vat_cents"),
            "synced_at": row.get("synced_at"),
        },
        "endpoints_checked": ["/order?includeUnscheduled=true", "/manual-order"],
        "detected_by": "scripts/reconcile_phantom_orders.py",
    }


def reconcile(api_base, api_key, supa_url, supa_key, start: date, end: date,
              enforce_sync_log: bool = True, verbose: bool = True):
    """Returns (candidates, stats). Raises AbortRun if any rail trips."""
    days = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur += timedelta(days=1)

    # --- Rail 3 precheck: is the pipeline healthy enough to trust its output at all? ---
    if enforce_sync_log:
        runs = sync_log_days_covered(supa_url, supa_key, start)
        if not runs:
            raise AbortRun(
                f"ABORT RAIL 3: no successful order-sync run recorded in sync_log since "
                f"{start}. If the pipeline itself is stalled we have no business proposing "
                f"deletions from its output."
            )
        latest = max(r["completed_at"] for r in runs)
        latest_dt = datetime.fromisoformat(latest.replace("Z", "+00:00"))
        age_h = (datetime.now(timezone.utc) - latest_dt).total_seconds() / 3600
        # orders-hot runs hourly; >24h without any successful order sync means stalled.
        if age_h > 24:
            raise AbortRun(
                f"ABORT RAIL 3: most recent successful order-sync completed {age_h:.1f}h "
                f"ago ({latest}). Pipeline looks stalled -- refusing to propose deletions."
            )
        if verbose:
            print(f"  rail 3 ok: {len(runs)} successful runs, latest {latest} ({age_h:.1f}h ago)")

    candidates = []
    repairs = []          # Backoffice-only rows: present upstream, absent here
    total_supa_rows = 0
    for day in days:
        live = fetch_live_orders_for_day(api_base, api_key, day)     # raises -> rail 1
        live_ids = set(live)
        supa_rows = fetch_supabase_rows_for_day(supa_url, supa_key, day)
        total_supa_rows += len(supa_rows)
        supa_ids = {r["order_id"] for r in supa_rows}

        # --- Direction 2: present upstream, missing here -> repair immediately ---
        # Additive and non-destructive: re-adding a row that provably exists upstream cannot
        # lose data, so it needs no human approval. Gating it behind a click would just
        # recreate the failure this is here to fix -- the missing May order sat unnoticed for
        # three months precisely because nothing acted on this direction. Deliberately NOT
        # volume-capped: if Supabase lost a lot of rows, re-adding them all is the correct
        # response, not a reason to stop.
        for oid, obj in live.items():
            if oid not in supa_ids:
                row = transform_order(obj)
                if row:
                    repairs.append(row)

        # --- Rail 2a: a whole day wiped out ---
        # The precise signature of "the API answered, but with nothing" -- an empty 200 is
        # not a retryable error so rail 1 never sees it, yet it makes every Supabase row for
        # that day look deleted. Caught per-day rather than in aggregate, because on a long
        # window one wiped day is a small overall percentage and would slip under a purely
        # proportional rail. A genuine mass deletion of an entire day is also worth a human
        # look, so aborting is the right response either way.
        if supa_rows and not live:
            raise AbortRun(
                f"ABORT RAIL 2a: {day} returned ZERO order ids upstream while Supabase holds "
                f"{len(supa_rows)} rows for that day. An empty-but-successful API response is "
                f"indistinguishable from that day being deleted wholesale -- refusing to "
                f"propose {len(supa_rows)} deletions off it. Nothing written."
            )

        missing = [r for r in supa_rows if r["order_id"] not in live_ids]
        for r in missing:
            candidates.append({
                "order_id": r["order_id"],
                "created_at": r.get("created_at"),
                "evidence": build_evidence(r, day, len(live_ids), len(supa_rows)),
                "status": "pending",
            })
        day_repairs = [r for r in repairs if (r.get("created_at") or "")[:10] == day.isoformat()]
        if verbose and (missing or day_repairs or len(supa_rows) == 0):
            print(f"  {day}: live={len(live_ids):4d} supa={len(supa_rows):4d} "
                  f"phantom={len(missing)} missing_here={len(day_repairs)}")

    # --- Rail 2b: volume sanity, evaluated before anything is written ---
    n = len(candidates)
    pct = (n / total_supa_rows * 100.0) if total_supa_rows else 0.0
    if n > MAX_CANDIDATES:
        raise AbortRun(
            f"ABORT RAIL 2b: {n} candidates exceeds the {MAX_CANDIDATES}-row ceiling. "
            f"That is more than any plausible real deletion batch -- treating it as a "
            f"data-source failure, not a mass deletion. Nothing written."
        )
    if pct > MAX_CANDIDATE_PCT:
        raise AbortRun(
            f"ABORT RAIL 2b: candidates are {pct:.1f}% of the {total_supa_rows}-row window, "
            f"over the {MAX_CANDIDATE_PCT}% ceiling. Nothing written."
        )

    repairs = dedupe_by_order_id(repairs)
    return candidates, repairs, {
        "days_checked": len(days),
        "supabase_rows_in_window": total_supa_rows,
        "candidates": n,
        "candidate_pct": round(pct, 3),
        "repairs": len(repairs),
    }


def write_candidates(supa_url: str, supa_key: str, candidates: list[dict]) -> None:
    """Upsert proposals as status='pending'. Never touches `orders`."""
    if not candidates:
        return
    body = json.dumps(candidates).encode("utf-8")
    req = urllib.request.Request(
        f"{supa_url.rstrip('/')}/rest/v1/orders_delete_candidates?on_conflict=order_id",
        data=body, method="POST",
        headers={
            "apikey": supa_key,
            "Authorization": f"Bearer {supa_key}",
            "Content-Type": "application/json",
            # merge-duplicates so re-detecting an already-approved candidate doesn't reset
            # its status back to pending... except PostgREST would overwrite status here.
            # ignore-duplicates keeps an existing decision intact -- see the note in
            # docs/STATUS.md workstream G.
            "Prefer": "resolution=ignore-duplicates,return=minimal",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        resp.read()


def upsert_orders(supa_url: str, supa_key: str, rows: list[dict], batch: int = 100) -> None:
    """Upsert repaired orders. Additive only -- never deletes, never blanks a column.

    Explicit ?on_conflict=order_id rather than relying on PostgREST's implicit PK fallback,
    and batched so one oversized request can't fail the whole repair set.
    """
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        req = urllib.request.Request(
            f"{supa_url.rstrip('/')}/rest/v1/orders?on_conflict=order_id",
            data=json.dumps(chunk).encode("utf-8"), method="POST",
            headers={
                "apikey": supa_key,
                "Authorization": f"Bearer {supa_key}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates,return=minimal",
            },
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            resp.read()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="compute only, write nothing")
    p.add_argument("--start", help="YYYY-MM-DD (default: WINDOW_DAYS back)")
    p.add_argument("--end", help="YYYY-MM-DD (default: today - SKIP_RECENT_DAYS)")
    p.add_argument("--no-sync-log-rail", action="store_true",
                   help="skip abort rail 3 (for backfilling historical windows only)")
    p.add_argument("--json-out", help="write candidates to this path")
    args = p.parse_args()

    api_base = os.environ.get("BACKOFFICE_API_URL")
    api_key = os.environ.get("BACKOFFICE_API_KEY")
    supa_url = os.environ.get("SUPABASE_URL")
    supa_key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not all([api_base, api_key, supa_url, supa_key]):
        print("ERROR: BACKOFFICE_API_URL, BACKOFFICE_API_KEY, SUPABASE_URL, "
              "SUPABASE_SERVICE_KEY must all be set.", file=sys.stderr)
        return 2

    today = date.today()
    end = date.fromisoformat(args.end) if args.end else today - timedelta(days=SKIP_RECENT_DAYS)
    start = date.fromisoformat(args.start) if args.start else end - timedelta(days=WINDOW_DAYS - 1)

    print(f"Reconciling {start} .. {end} ({(end - start).days + 1} days), "
          f"dry_run={args.dry_run}")

    try:
        candidates, repairs, stats = reconcile(
            api_base, api_key, supa_url, supa_key, start, end,
            enforce_sync_log=not args.no_sync_log_rail,
        )
    except AbortRun as e:
        print(f"\n{e}", file=sys.stderr)
        print("RESULT: aborted, nothing written.", file=sys.stderr)
        return 3

    print(f"\nstats: {json.dumps(stats)}")
    print(f"phantom candidates (Supabase-only, need approval): {len(candidates)}")
    print(f"repairs (Backoffice-only, applied automatically):  {len(repairs)}")
    for r in repairs[:10]:
        print(f"  + {r['order_id']}  created={r['created_at']}  "
              f"org={r.get('organization_name')}  state={r.get('order_state')}")
    if len(repairs) > 10:
        print(f"  ... and {len(repairs) - 10} more")
    for c in candidates[:10]:
        ev = c["evidence"]["order"]
        print(f"  {c['order_id']}  created={c['created_at']}  "
              f"org={ev.get('organization_name')}  synced={ev.get('synced_at')}")
    if len(candidates) > 10:
        print(f"  ... and {len(candidates) - 10} more")

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(candidates, f, indent=2, ensure_ascii=False)
        print(f"wrote {args.json_out}")

    if args.dry_run:
        print("\n--dry-run: nothing written (no candidates, no repairs).")
        return 0

    if repairs:
        upsert_orders(supa_url, supa_key, repairs)
        print(f"\nrepaired {len(repairs)} order(s) missing from Supabase (additive, no "
              f"approval needed -- they provably exist upstream).")

    write_candidates(supa_url, supa_key, candidates)
    print(f"wrote {len(candidates)} pending candidates. NOTHING DELETED -- approval "
          f"happens via the BI bot, deletion via scripts/delete_approved_orders.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
