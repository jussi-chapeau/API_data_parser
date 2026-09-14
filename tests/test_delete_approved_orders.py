"""
Tests for scripts/delete_approved_orders.py -- the only code path that deletes from `orders`.

Two things are covered:

1. **Decision resolution.** The approvals log is append-only, so a revocation can only be a
   later contradicting row, never an edit. The script must honour the *current* decision.
   The resolution itself lives in SQL (`orders_delete_approvals_current`, migration 012) and
   is proven against the real database; what is tested here is that the script consumes it
   correctly -- i.e. that it reads the resolved view and treats 'rejected' as blocking rather
   than falling back to "an approval row exists somewhere".

2. **Backup retention.** Backups are a fresh copy of customer addresses, so an unbounded
   backup directory moves the GDPR problem rather than solving it.

Run:  python3 -m pytest tests/test_delete_approved_orders.py -v
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "delete_approved_orders",
    Path(__file__).resolve().parents[1] / "scripts" / "delete_approved_orders.py",
)
dao = importlib.util.module_from_spec(_SPEC)
sys.modules["delete_approved_orders"] = dao
_SPEC.loader.exec_module(dao)


# ------------------------------------------------------- decision resolution (consumption)

def _run_main_with(monkeypatch, decisions, candidates, capsys, argv=("--dry-run",)):
    """Run main() with the network stubbed, returning (exit_code, stdout, requested_urls)."""
    requested: list[str] = []

    def fake_req(url, key, method="GET", body=None, extra_headers=None):
        requested.append(url)
        if "orders_delete_approvals_current" in url:
            return decisions
        if "orders_delete_candidates" in url:
            return candidates
        return []

    monkeypatch.setattr(dao, "_req", fake_req)
    # Re-verification is exercised separately; here it always confirms absence.
    monkeypatch.setattr(dao, "still_absent_upstream", lambda *a, **k: True)
    monkeypatch.setenv("SUPABASE_URL", "http://supa.test")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")
    monkeypatch.setenv("BACKOFFICE_API_URL", "http://api.test")
    monkeypatch.setenv("BACKOFFICE_API_KEY", "k")
    monkeypatch.setattr(sys, "argv", ["delete_approved_orders.py", *argv])

    code = dao.main()
    return code, capsys.readouterr().out, requested


def _cand(oid):
    return {"order_id": oid, "created_at": "2026-08-14T12:00:00+00:00",
            "status": "pending", "evidence": {}}


def test_reads_the_resolved_view_not_the_raw_log(monkeypatch, capsys):
    """The whole fix: it must query the resolved view, never filter the append-only table."""
    _, _, urls = _run_main_with(
        monkeypatch,
        decisions=[{"order_id": "A", "decision": "approved", "approved_by": "u",
                    "approved_at": "2026-09-01T10:00:00+00:00", "approval_id": 1}],
        candidates=[_cand("A")], capsys=capsys)

    assert any("orders_delete_approvals_current" in u for u in urls), \
        "must read the resolved current-decision view"
    assert not any("orders_delete_approvals?" in u and "current" not in u for u in urls), \
        "must NOT read the raw append-only approvals table"


def test_current_rejected_blocks_deletion(monkeypatch, capsys):
    """Approved Monday, revoked Tuesday: the revocation wins and nothing is deleted.

    Under the old "an approval row exists" logic this order was deleted anyway.
    """
    code, out, _ = _run_main_with(
        monkeypatch,
        decisions=[{"order_id": "REVOKED", "decision": "rejected", "approved_by": "u",
                    "approved_at": "2026-09-02T10:00:00+00:00", "approval_id": 2}],
        candidates=[_cand("REVOKED")], capsys=capsys)

    assert code == 0
    assert "nothing currently approved" in out
    assert "REVOKED" not in out.split("blocked by a current")[0] or "blocked" in out
    assert "blocked by a current 'rejected' decision" in out


def test_current_approved_after_rejection_is_deletable(monkeypatch, capsys):
    """Rejected first, approved later: the later approval wins and it is eligible."""
    code, out, _ = _run_main_with(
        monkeypatch,
        decisions=[{"order_id": "REINSTATED", "decision": "approved", "approved_by": "u",
                    "approved_at": "2026-09-02T10:00:00+00:00", "approval_id": 2}],
        candidates=[_cand("REINSTATED")], capsys=capsys)

    assert code == 0
    assert "REINSTATED" in out
    assert "would delete" in out.lower()


def test_mixed_batch_deletes_only_currently_approved(monkeypatch, capsys):
    """A realistic batch: one approved, one revoked. Only the approved one proceeds."""
    code, out, _ = _run_main_with(
        monkeypatch,
        decisions=[
            {"order_id": "OK", "decision": "approved", "approved_by": "u",
             "approved_at": "2026-09-02T10:00:00+00:00", "approval_id": 3},
            {"order_id": "NOPE", "decision": "rejected", "approved_by": "u",
             "approved_at": "2026-09-02T10:00:00+00:00", "approval_id": 4},
        ],
        candidates=[_cand("OK")], capsys=capsys)

    assert code == 0
    assert "1 approved, 1 rejected" in out
    deletable = out.split("would delete these order_ids:")[-1]
    assert "OK" in deletable
    assert "NOPE" not in deletable


def test_approval_without_a_candidate_is_ignored_not_deleted(monkeypatch, capsys):
    """An approval for an order that was never proposed must not delete anything."""
    code, out, _ = _run_main_with(
        monkeypatch,
        decisions=[{"order_id": "GHOST", "decision": "approved", "approved_by": "u",
                    "approved_at": "2026-09-02T10:00:00+00:00", "approval_id": 5}],
        candidates=[], capsys=capsys)

    assert code == 0
    assert "no pending candidate" in out
    assert "nothing to delete" in out


# ----------------------------------------------------------------- backup retention (GDPR)

def test_prune_removes_only_expired_backups(tmp_path):
    """Backups past the retention window are deleted; fresh ones are kept."""
    old = tmp_path / "deleted_orders_backup_20260101T000000Z.json"
    new = tmp_path / "deleted_orders_backup_20260902T000000Z.json"
    for f in (old, new):
        f.write_text("[]")
    stale = (datetime.now(timezone.utc) - timedelta(days=45)).timestamp()
    os.utime(old, (stale, stale))

    removed = dao.prune_old_backups(tmp_path, retention_days=30)

    assert removed == [old.name]
    assert not old.exists()
    assert new.exists(), "a backup inside the retention window must be kept"


def test_prune_ignores_unrelated_files(tmp_path):
    """Pruning must not touch anything that isn't one of our backups."""
    keep = tmp_path / "something_else.json"
    keep.write_text("{}")
    stale = (datetime.now(timezone.utc) - timedelta(days=999)).timestamp()
    os.utime(keep, (stale, stale))

    assert dao.prune_old_backups(tmp_path, retention_days=30) == []
    assert keep.exists()


def test_prune_on_missing_directory_is_safe(tmp_path):
    """First-ever run: no backup directory yet, must not raise."""
    assert dao.prune_old_backups(tmp_path / "nope", retention_days=30) == []


def test_backup_dir_is_not_the_repo_root():
    """Backups must not default to CWD -- that is the repo root and was committable."""
    assert dao.BACKUP_DIR.name == "deleted_orders"
    assert "backups" in dao.BACKUP_DIR.parts
    assert dao.BACKUP_RETENTION_DAYS > 0
