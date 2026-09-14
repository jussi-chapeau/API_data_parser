#!/usr/bin/env python3
"""
Hard-delete phantom orders that a human has explicitly approved.

This is the ONLY code path in this repo that deletes from `orders`. It deletes exclusively
from the reviewed, stored candidate list -- never from a freshly computed set. Computing and
deleting in one pass is precisely the mistake that turns a flaky upstream API into mass data
loss, which is why detection (scripts/reconcile_phantom_orders.py) and deletion (here) are
separate programs with a human decision in between.

Flow:
  reconcile job          -> orders_delete_candidates (status='pending')
  BI bot + human in Slack -> orders_delete_approvals (decision='approved')
  this script            -> re-verifies, deletes from orders, marks candidate 'deleted'

Role separation: the BI service can SELECT candidates and INSERT approvals, and has no
privilege on `orders` at all. Only this script (service role) deletes.

Safety:
  - Eligibility requires the order's CURRENT decision to be 'approved' AND a stored candidate
    row to exist. "Current" matters: the approvals log is append-only, so a revocation is a
    later contradicting row, not an edit. Reading the raw table would find a superseded
    'approved' row and delete anyway. This reads orders_delete_approvals_current, which
    resolves latest-wins (approved_at DESC, id DESC) -- see migration 012. An approval for an
    unknown order_id is ignored and reported.
  - A current 'rejected' decision blocks deletion and leaves the candidate 'pending', so a
    later approval can still act on it.
  - Every order is RE-VERIFIED against the live Backoffice API immediately before deletion.
    Approval may be hours or days old; if the order has reappeared upstream (or was a false
    positive, or is a duplicate that the hourly sync will just re-upsert -- see
    docs/BACKOFFICE_API_ISSUES.md #17) it is skipped, not deleted.
  - Every deleted row is written to a JSON backup first, in a fixed gitignored directory
    with an enforced retention window. Those backups contain customer addresses, so they are
    themselves in scope for erasure -- see docs/GDPR_REVIEW.md.

Usage:
  export SUPABASE_URL=... SUPABASE_SERVICE_KEY=...
  export BACKOFFICE_API_URL=... BACKOFFICE_API_KEY=...
  python3 scripts/delete_approved_orders.py --dry-run    # show what would be deleted
  python3 scripts/delete_approved_orders.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Backups contain FULL order rows, including customer street addresses in `stops`. They are
# therefore a fresh copy of the same personal data the deletion is meant to erase -- erasure
# is not complete when the row merely leaves `orders`. So: a fixed, gitignored location (not
# the CWD, which is the repo root and was committable), and a retention limit enforced on
# every run. See docs/GDPR_REVIEW.md in this repo.
BACKUP_DIR = Path(os.environ.get("ORDER_DELETE_BACKUP_DIR",
                                 Path(__file__).resolve().parents[1] / "backups" / "deleted_orders"))
BACKUP_RETENTION_DAYS = int(os.environ.get("ORDER_DELETE_BACKUP_RETENTION_DAYS", "30"))


def prune_old_backups(directory: Path, retention_days: int) -> list[str]:
    """Delete backups older than the retention window. Returns what was removed.

    Runs on every invocation rather than on a separate schedule, so retention cannot silently
    lapse because a cleanup job was never wired up.
    """
    if not directory.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    removed = []
    for f in sorted(directory.glob("deleted_orders_backup_*.json")):
        mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
        if mtime < cutoff:
            f.unlink()
            removed.append(f.name)
    return removed


def _req(url: str, key: str, method: str = "GET", body=None, extra_headers=None):
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else None


def still_absent_upstream(api_base: str, api_key: str, order_id: str, created_at: str) -> bool:
    """True if the order is STILL gone from Backoffice. Any doubt returns False (= skip).

    Checks the order's own creation day on both endpoints, mirroring how the candidate was
    detected in the first place.
    """
    day = created_at[:10]
    for path, extra in (("order", {"includeUnscheduled": "true"}), ("manual-order", {})):
        params = {"start_date": day, "end_date": day, **extra}
        url = f"{api_base.rstrip('/')}/{path}?{urllib.parse.urlencode(params)}"
        try:
            req = urllib.request.Request(url, headers={"x-api-key": api_key})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            # Cannot confirm absence => do not delete. Fail closed, always.
            print(f"    ! re-verify failed for {order_id} ({e}) -- skipping, not deleting")
            return False
        if any(o.get("orderId") == order_id for o in (data or [])):
            return False
    return True


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-reverify", action="store_true",
                   help="DANGEROUS: skip the live-API re-verification before each delete")
    args = p.parse_args()

    supa_url = os.environ.get("SUPABASE_URL")
    supa_key = os.environ.get("SUPABASE_SERVICE_KEY")
    api_base = os.environ.get("BACKOFFICE_API_URL")
    api_key = os.environ.get("BACKOFFICE_API_KEY")
    if not all([supa_url, supa_key]):
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY required.", file=sys.stderr)
        return 2
    if not args.skip_reverify and not all([api_base, api_key]):
        print("ERROR: BACKOFFICE_API_URL/KEY required for re-verification "
              "(or pass --skip-reverify, which is discouraged).", file=sys.stderr)
        return 2

    base = supa_url.rstrip("/")

    # Read the RESOLVED current decision per order, never the raw append-only log.
    # The log can legitimately hold contradicting rows for one order: the BI bot cannot
    # UPDATE, so revoking an approval means inserting a later 'rejected' row. Asking "does an
    # approval exist?" would find the superseded row and delete anyway -- the revocation would
    # be silently ignored. orders_delete_approvals_current resolves latest-wins
    # (approved_at DESC, id DESC) in SQL; see migration 012.
    decisions = _req(
        f"{base}/rest/v1/orders_delete_approvals_current"
        f"?select=order_id,decision,approved_by,approved_at,approval_id", supa_key) or []
    approved_ids = {d["order_id"] for d in decisions if d["decision"] == "approved"}
    rejected_ids = {d["order_id"] for d in decisions if d["decision"] == "rejected"}
    print(f"current decisions: {len(decisions)} "
          f"({len(approved_ids)} approved, {len(rejected_ids)} rejected)")
    if rejected_ids:
        # Surfaced explicitly: these WOULD have been deleted under the old "any approval row
        # exists" logic. Their candidates deliberately stay 'pending' so a later approval can
        # still act on them.
        print(f"  blocked by a current 'rejected' decision (candidates stay pending): "
              f"{sorted(rejected_ids)[:5]}{'...' if len(rejected_ids) > 5 else ''}")
    if not approved_ids:
        print("nothing currently approved -- nothing to do.")
        return 0

    id_filter = ",".join(sorted(approved_ids))
    candidates = _req(
        f"{base}/rest/v1/orders_delete_candidates"
        f"?order_id=in.({id_filter})&status=in.(pending,approved)"
        f"&select=order_id,created_at,status,evidence", supa_key) or []
    print(f"matching stored candidates (pending/approved): {len(candidates)}")

    orphan = approved_ids - {c["order_id"] for c in candidates}
    if orphan:
        print(f"WARNING: {len(orphan)} approval(s) with no pending candidate -- ignored "
              f"(already deleted, or approved for an order never proposed): "
              f"{sorted(orphan)[:5]}")

    eligible = []
    for c in candidates:
        if args.skip_reverify:
            eligible.append(c)
            continue
        if still_absent_upstream(api_base, api_key, c["order_id"], c["created_at"] or ""):
            eligible.append(c)
        else:
            print(f"    - {c['order_id']} is back upstream (or unverifiable) -- skipping")

    print(f"eligible after re-verification: {len(eligible)}")
    if not eligible:
        print("nothing to delete.")
        return 0

    eligible_ids = [c["order_id"] for c in eligible]

    if args.dry_run:
        print("\n--dry-run: would delete these order_ids:")
        for oid in eligible_ids:
            print(" ", oid)
        return 0

    # Back up full rows before deleting anything.
    rows = _req(f"{base}/rest/v1/orders?order_id=in.({','.join(eligible_ids)})&select=*",
                supa_key) or []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.chmod(0o700)  # directory itself is owner-only, not just the files in it
    backup = BACKUP_DIR / f"deleted_orders_backup_{stamp}.json"
    with open(backup, "w") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)
    backup.chmod(0o600)  # contains customer addresses -- owner-only
    print(f"backed up {len(rows)} full rows -> {backup}")
    pruned = prune_old_backups(BACKUP_DIR, BACKUP_RETENTION_DAYS)
    if pruned:
        print(f"pruned {len(pruned)} backup(s) older than {BACKUP_RETENTION_DAYS}d: {pruned}")

    _req(f"{base}/rest/v1/orders?order_id=in.({','.join(eligible_ids)})",
         supa_key, method="DELETE", extra_headers={"Prefer": "return=minimal"})

    remaining = _req(
        f"{base}/rest/v1/orders?order_id=in.({','.join(eligible_ids)})&select=order_id",
        supa_key) or []
    if remaining:
        print(f"ERROR: {len(remaining)} rows still present after DELETE.", file=sys.stderr)
        return 1
    print(f"deleted {len(eligible_ids)} orders, verified gone.")

    _req(f"{base}/rest/v1/orders_delete_candidates?order_id=in.({','.join(eligible_ids)})",
         supa_key, method="PATCH", body={"status": "deleted"},
         extra_headers={"Prefer": "return=minimal"})
    print("marked candidates status='deleted'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
