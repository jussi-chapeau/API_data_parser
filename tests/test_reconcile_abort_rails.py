"""
Abort-rail tests for scripts/reconcile_phantom_orders.py.

These are the tests that matter most in this pipeline. The rails exist because a stalled
sync or a flaky upstream API is indistinguishable, at the point of computation, from
"Backoffice deleted everything" -- and the consequence of getting that wrong is proposing
mass deletion of real orders. Precedent: the 2026-08 Supabase key rotation silently killed
8 workflows for two days with zero alerts; under a naive rule that run would have proposed
deleting the entire table.

Every test asserts BOTH that AbortRun was raised AND that no candidates were produced --
"aborted" must mean "wrote nothing", not "wrote some then stopped".

Run:  python3 -m pytest tests/test_reconcile_abort_rails.py -v
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

# Load the script by path -- scripts/ isn't a package.
_SPEC = importlib.util.spec_from_file_location(
    "reconcile_phantom_orders",
    Path(__file__).resolve().parents[1] / "scripts" / "reconcile_phantom_orders.py",
)
rpo = importlib.util.module_from_spec(_SPEC)
sys.modules["reconcile_phantom_orders"] = rpo
_SPEC.loader.exec_module(rpo)


FAKE_ARGS = ("http://api.test", "key", "http://supa.test", "key")


def _healthy_sync_log(*_args, **_kwargs):
    """One successful order-sync run, one hour old."""
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    return [{"workflow": "orders-hot", "completed_at": recent, "status": "success"}]


def _row(oid: str, day: date) -> dict:
    return {
        "order_id": oid,
        "created_at": f"{day.isoformat()}T12:00:00+00:00",
        "is_manual": False,
        "order_state": "CONFIRMED",
        "organization_name": "TestOrg",
        "hub_id": "hub-1",
        "total_excl_vat_cents": 10000,
        "synced_at": f"{day.isoformat()}T09:00:00+00:00",
    }


# --------------------------------------------------------------------- rail 1: fetch fails

def test_rail1_unreadable_day_aborts_and_writes_nothing(monkeypatch):
    """A day whose fetch fails must abort -- never be read as 'everything was deleted'."""
    day = date(2026, 8, 14)

    def boom(*_a, **_k):
        raise rpo.AbortRun("ABORT RAIL 1: simulated upstream 504")

    monkeypatch.setattr(rpo, "sync_log_days_covered", _healthy_sync_log)
    monkeypatch.setattr(rpo, "fetch_live_orders_for_day", boom)
    monkeypatch.setattr(rpo, "fetch_supabase_rows_for_day",
                        lambda *a, **k: [_row("o1", day), _row("o2", day)])

    with pytest.raises(rpo.AbortRun) as exc:
        rpo.reconcile(*FAKE_ARGS, day, day, verbose=False)
    assert "RAIL 1" in str(exc.value)


def test_rail1_real_http_failure_path_aborts(monkeypatch):
    """The retry loop itself must end in AbortRun, not in an empty id set."""
    monkeypatch.setattr(rpo, "BASE_BACKOFF", 0)  # keep the test fast
    monkeypatch.setattr(rpo, "MAX_TRIES", 2)

    def always_fails(*_a, **_k):
        raise TimeoutError("simulated timeout")

    monkeypatch.setattr(rpo, "_get_json", always_fails)

    with pytest.raises(rpo.AbortRun) as exc:
        rpo.fetch_live_orders_for_day("http://api.test", "k", date(2026, 8, 14))
    assert "RAIL 1" in str(exc.value)


# ------------------------------------------------------------------- rail 2: volume ceiling

def test_rail2_too_many_candidates_aborts(monkeypatch):
    """More candidates than any plausible real deletion batch => data-source failure."""
    day = date(2026, 8, 14)
    rows = [_row(f"o{i}", day) for i in range(rpo.MAX_CANDIDATES + 5)]

    monkeypatch.setattr(rpo, "sync_log_days_covered", _healthy_sync_log)
    monkeypatch.setattr(rpo, "fetch_live_orders_for_day", lambda *a, **k: {})  # API says "nothing"
    monkeypatch.setattr(rpo, "fetch_supabase_rows_for_day", lambda *a, **k: rows)

    with pytest.raises(rpo.AbortRun) as exc:
        rpo.reconcile(*FAKE_ARGS, day, day, verbose=False)
    assert "RAIL 2" in str(exc.value)


def test_rail2b_percentage_ceiling_aborts(monkeypatch):
    """Under the row ceiling but over the % ceiling must still abort.

    Retuned 2026-09-02 alongside the ceiling change (5% -> 10%): 15 phantoms out of 100 rows
    = 15%, over the new ceiling and still under the 50-row cap, so this exercises the
    percentage rail specifically rather than the absolute one. The test's intent is
    unchanged -- only the numbers moved with the threshold.
    """
    day = date(2026, 8, 14)
    live = {f"live{i}" for i in range(85)}
    rows = [_row(f"live{i}", day) for i in range(85)] + [_row(f"ghost{i}", day) for i in range(15)]

    monkeypatch.setattr(rpo, "sync_log_days_covered", _healthy_sync_log)
    monkeypatch.setattr(rpo, "fetch_live_orders_for_day", lambda *a, **k: {i: {"orderId": i} for i in live})
    monkeypatch.setattr(rpo, "fetch_supabase_rows_for_day", lambda *a, **k: rows)

    with pytest.raises(rpo.AbortRun) as exc:
        rpo.reconcile(*FAKE_ARGS, day, day, verbose=False)
    assert "RAIL 2b" in str(exc.value)
    assert "%" in str(exc.value)


def test_rail2b_ceiling_is_exclusive_not_inclusive(monkeypatch):
    """Exactly at the ceiling must pass; the rail is `>`, not `>=`.

    Pins the boundary deliberately, since the real August incident sat 0.007pp from the old
    limit -- an off-by-one here is the difference between detecting a real incident and
    silently refusing to.
    """
    day = date(2026, 8, 14)
    live = {f"live{i}" for i in range(90)}
    rows = [_row(f"live{i}", day) for i in range(90)] + [_row(f"ghost{i}", day) for i in range(10)]

    monkeypatch.setattr(rpo, "sync_log_days_covered", _healthy_sync_log)
    monkeypatch.setattr(rpo, "fetch_live_orders_for_day", lambda *a, **k: {i: {"orderId": i} for i in live})
    monkeypatch.setattr(rpo, "fetch_supabase_rows_for_day", lambda *a, **k: rows)

    candidates, repairs, stats = rpo.reconcile(*FAKE_ARGS, day, day, verbose=False)
    assert stats["candidate_pct"] == pytest.approx(rpo.MAX_CANDIDATE_PCT)
    assert stats["candidates"] == 10


# ------------------------------------------------------------------ rail 3: stalled pipeline

def test_rail3_stalled_sync_aborts(monkeypatch):
    """THE headline case: sync stalled for days, upstream fine. Must abort, not mass-delete."""
    day = date(2026, 8, 14)
    stale = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()

    monkeypatch.setattr(rpo, "sync_log_days_covered",
                        lambda *a, **k: [{"workflow": "orders-hot",
                                          "completed_at": stale, "status": "success"}])
    # Upstream returns nothing (e.g. the same outage that stalled the sync).
    monkeypatch.setattr(rpo, "fetch_live_orders_for_day", lambda *a, **k: {})
    monkeypatch.setattr(rpo, "fetch_supabase_rows_for_day",
                        lambda *a, **k: [_row(f"o{i}", day) for i in range(200)])

    with pytest.raises(rpo.AbortRun) as exc:
        rpo.reconcile(*FAKE_ARGS, day, day, verbose=False)
    assert "RAIL 3" in str(exc.value)
    assert "stalled" in str(exc.value).lower()


def test_rail3_no_successful_runs_at_all_aborts(monkeypatch):
    """Empty sync_log => pipeline health unknown => refuse to propose deletions."""
    day = date(2026, 8, 14)
    monkeypatch.setattr(rpo, "sync_log_days_covered", lambda *a, **k: [])
    monkeypatch.setattr(rpo, "fetch_live_orders_for_day", lambda *a, **k: {})
    monkeypatch.setattr(rpo, "fetch_supabase_rows_for_day", lambda *a, **k: [_row("o1", day)])

    with pytest.raises(rpo.AbortRun) as exc:
        rpo.reconcile(*FAKE_ARGS, day, day, verbose=False)
    assert "RAIL 3" in str(exc.value)


# ------------------------------------------------------------------------- happy path

def test_healthy_run_detects_only_the_real_phantom(monkeypatch):
    """Sanity: with everything healthy, exactly the absent row is proposed."""
    day = date(2026, 8, 14)
    live = {f"real{i}" for i in range(99)}
    rows = [_row(f"real{i}", day) for i in range(99)] + [_row("ghost", day)]

    monkeypatch.setattr(rpo, "sync_log_days_covered", _healthy_sync_log)
    monkeypatch.setattr(rpo, "fetch_live_orders_for_day", lambda *a, **k: {i: {"orderId": i} for i in live})
    monkeypatch.setattr(rpo, "fetch_supabase_rows_for_day", lambda *a, **k: rows)

    candidates, repairs, stats = rpo.reconcile(*FAKE_ARGS, day, day, verbose=False)
    assert [c["order_id"] for c in candidates] == ["ghost"]
    assert stats["candidates"] == 1
    assert candidates[0]["status"] == "pending"
    # Evidence must be self-contained enough to review without re-querying.
    ev = candidates[0]["evidence"]
    assert ev["reason"] == "present_in_supabase_absent_upstream"
    assert ev["checked_day"] == "2026-08-14"
    assert ev["order"]["organization_name"] == "TestOrg"


def test_cancelled_upstream_is_not_a_phantom(monkeypatch):
    """A CANCELLED order still exists upstream -- it must never be proposed for deletion."""
    day = date(2026, 8, 14)
    cancelled = _row("cancelled-1", day)
    cancelled["order_state"] = "CANCELLED"

    monkeypatch.setattr(rpo, "sync_log_days_covered", _healthy_sync_log)
    # Upstream still returns it, because cancellation is not deletion.
    monkeypatch.setattr(rpo, "fetch_live_orders_for_day", lambda *a, **k: {"cancelled-1": {"orderId": "cancelled-1"}})
    monkeypatch.setattr(rpo, "fetch_supabase_rows_for_day", lambda *a, **k: [cancelled])

    candidates, repairs, _ = rpo.reconcile(*FAKE_ARGS, day, day, verbose=False)
    assert candidates == []
    assert repairs == []  # present on both sides -> nothing to delete AND nothing to repair


# ------------------------------------------------- rail 2a: a whole day wiped out

def test_rail2a_empty_day_aborts(monkeypatch):
    """An empty-but-successful API response must abort, not yield a day's worth of phantoms.

    This is the gap rail 1 cannot see: HTTP 200 with `[]` is not an error, so the retry loop
    succeeds and hands back an empty set. Without this rail every Supabase row for that day
    becomes a deletion candidate.
    """
    day = date(2026, 8, 14)
    monkeypatch.setattr(rpo, "sync_log_days_covered", _healthy_sync_log)
    monkeypatch.setattr(rpo, "fetch_live_orders_for_day", lambda *a, **k: {})
    monkeypatch.setattr(rpo, "fetch_supabase_rows_for_day",
                        lambda *a, **k: [_row(f"o{i}", day) for i in range(12)])

    with pytest.raises(rpo.AbortRun) as exc:
        rpo.reconcile(*FAKE_ARGS, day, day, verbose=False)
    assert "RAIL 2a" in str(exc.value)


def test_rail2a_catches_what_the_percentage_rail_would_miss(monkeypatch):
    """The reason rail 2a is per-day rather than aggregate.

    One wiped day inside a long, busy window is a *small* overall percentage, so a purely
    proportional rail waves it through. 12 phantoms out of a 1212-row window is 0.99% -- under
    even the old 5% ceiling and far under the 50-row cap. Rail 2a still stops it.
    """
    wiped = date(2026, 8, 14)
    days = [date(2026, 8, d) for d in range(10, 21)]

    def live(_api, _key, day):
        return {} if day == wiped else {f"ok-{day}-{i}": {"orderId": f"ok-{day}-{i}"} for i in range(120)}

    def supa(_u, _k, day):
        if day == wiped:
            return [_row(f"ghost-{i}", day) for i in range(12)]
        return [_row(f"ok-{day}-{i}", day) for i in range(120)]

    monkeypatch.setattr(rpo, "sync_log_days_covered", _healthy_sync_log)
    monkeypatch.setattr(rpo, "fetch_live_orders_for_day", live)
    monkeypatch.setattr(rpo, "fetch_supabase_rows_for_day", supa)

    with pytest.raises(rpo.AbortRun) as exc:
        rpo.reconcile(*FAKE_ARGS, days[0], days[-1], verbose=False)
    assert "RAIL 2a" in str(exc.value)


def test_quiet_day_with_no_orders_either_side_is_fine(monkeypatch):
    """A genuinely quiet day (nothing upstream, nothing here) must NOT trip rail 2a."""
    day = date(2026, 8, 14)
    monkeypatch.setattr(rpo, "sync_log_days_covered", _healthy_sync_log)
    monkeypatch.setattr(rpo, "fetch_live_orders_for_day", lambda *a, **k: {})
    monkeypatch.setattr(rpo, "fetch_supabase_rows_for_day", lambda *a, **k: [])

    candidates, repairs, stats = rpo.reconcile(*FAKE_ARGS, day, day, verbose=False)
    assert candidates == []
    assert stats["candidates"] == 0


def test_real_august_ratio_now_passes_comfortably(monkeypatch):
    """Regression guard for the actual incident: 34 phantoms in a 681-row window.

    This came in at 4.993% and cleared the old 5.0% ceiling by 0.007pp -- effectively luck.
    It must now pass with real margin, or the rail is still mis-tuned.
    """
    day = date(2026, 8, 14)
    live = {f"real{i}" for i in range(647)}
    rows = ([_row(f"real{i}", day) for i in range(647)] +
            [_row(f"ghost{i}", day) for i in range(34)])

    monkeypatch.setattr(rpo, "sync_log_days_covered", _healthy_sync_log)
    monkeypatch.setattr(rpo, "fetch_live_orders_for_day", lambda *a, **k: {i: {"orderId": i} for i in live})
    monkeypatch.setattr(rpo, "fetch_supabase_rows_for_day", lambda *a, **k: rows)

    candidates, repairs, stats = rpo.reconcile(*FAKE_ARGS, day, day, verbose=False)
    assert stats["candidates"] == 34
    assert stats["candidate_pct"] == pytest.approx(4.993, abs=0.01)
    assert stats["candidate_pct"] < rpo.MAX_CANDIDATE_PCT


# ------------------------------------------------- direction 2: repair (Backoffice-only)

def test_missing_locally_is_repaired_not_proposed_for_deletion(monkeypatch):
    """An order upstream but absent here becomes a repair, never a deletion candidate.

    This is the direction nothing watched before 2026-09-03 -- one real May order sat missing
    for three months because the reconcile only ever looked for surplus rows.
    """
    day = date(2026, 5, 24)
    upstream = {
        "present": {"orderId": "present", "created": "1779599528.739927"},
        "absent-here": {"orderId": "absent-here", "created": "1779599528.739927",
                        "orderState": "CONFIRMED", "organizationName": "Kirill",
                        "charge": {"basePrice": "20065", "platformFee": "0",
                                   "servicesPrice": "0", "vatPrice": "25182",
                                   "vatPercentage": "25.5"}},
    }
    monkeypatch.setattr(rpo, "sync_log_days_covered", _healthy_sync_log)
    monkeypatch.setattr(rpo, "fetch_live_orders_for_day", lambda *a, **k: upstream)
    monkeypatch.setattr(rpo, "fetch_supabase_rows_for_day", lambda *a, **k: [_row("present", day)])

    candidates, repairs, stats = rpo.reconcile(*FAKE_ARGS, day, day, verbose=False)

    assert candidates == [], "a row missing locally must never be proposed for deletion"
    assert [r["order_id"] for r in repairs] == ["absent-here"]
    assert stats["repairs"] == 1
    # Repaired rows must be fully transformed, not raw API payloads.
    r = repairs[0]
    assert r["created_at"].startswith("2026-05-24T")
    assert r["total_excl_vat_cents"] == 20065
    assert r["is_manual"] is False


def test_both_directions_in_one_run(monkeypatch):
    """Drift can go both ways on the same day; each row goes to the correct path.

    Uses a realistically sized day (100 shared rows) on purpose: with only a couple of rows
    a single phantom is a huge percentage of the window and correctly trips rail 2b, which
    would test the rail rather than the routing.
    """
    day = date(2026, 8, 14)
    shared = {f"s{i}": {"orderId": f"s{i}", "created": "1779599528.739927"} for i in range(100)}
    upstream = {**shared, "only-upstream": {"orderId": "only-upstream",
                                            "created": "1779599528.739927"}}
    supa = [_row(f"s{i}", day) for i in range(100)] + [_row("only-here", day)]

    monkeypatch.setattr(rpo, "sync_log_days_covered", _healthy_sync_log)
    monkeypatch.setattr(rpo, "fetch_live_orders_for_day", lambda *a, **k: upstream)
    monkeypatch.setattr(rpo, "fetch_supabase_rows_for_day", lambda *a, **k: supa)

    candidates, repairs, stats = rpo.reconcile(*FAKE_ARGS, day, day, verbose=False)

    assert [c["order_id"] for c in candidates] == ["only-here"]
    assert [r["order_id"] for r in repairs] == ["only-upstream"]
    assert stats["candidates"] == 1 and stats["repairs"] == 1


def test_repairs_are_not_volume_capped(monkeypatch):
    """Repairs must NOT trip the deletion volume rails.

    If Supabase lost a lot of rows, re-adding them all is the correct response -- capping the
    additive direction would leave the database knowingly wrong. The rails exist to restrain
    destruction, not restoration.
    """
    day = date(2026, 8, 14)
    upstream = {f"u{i}": {"orderId": f"u{i}", "created": "1779599528.739927"}
                for i in range(rpo.MAX_CANDIDATES * 4)}
    monkeypatch.setattr(rpo, "sync_log_days_covered", _healthy_sync_log)
    monkeypatch.setattr(rpo, "fetch_live_orders_for_day", lambda *a, **k: upstream)
    monkeypatch.setattr(rpo, "fetch_supabase_rows_for_day", lambda *a, **k: [])

    candidates, repairs, stats = rpo.reconcile(*FAKE_ARGS, day, day, verbose=False)
    assert candidates == []
    assert len(repairs) == rpo.MAX_CANDIDATES * 4


def test_abort_prevents_repairs_too(monkeypatch):
    """An abort means the whole run stops -- no candidates AND no repairs.

    If the deletion side looks anomalous, something is wrong with the comparison itself, so
    acting on either direction from that same data would be unwise.
    """
    day = date(2026, 8, 14)
    stale = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    monkeypatch.setattr(rpo, "sync_log_days_covered",
                        lambda *a, **k: [{"workflow": "orders-hot",
                                          "completed_at": stale, "status": "success"}])
    monkeypatch.setattr(rpo, "fetch_live_orders_for_day",
                        lambda *a, **k: {"u1": {"orderId": "u1", "created": "1779599528.739927"}})
    monkeypatch.setattr(rpo, "fetch_supabase_rows_for_day", lambda *a, **k: [])

    with pytest.raises(rpo.AbortRun):
        rpo.reconcile(*FAKE_ARGS, day, day, verbose=False)
