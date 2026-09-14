"""
Tests for scripts/order_transform.py.

This transform exists in three places -- here, the `Transform Orders` node in
orders-{hot,warm,cool}/backfill.json, and the `Reconcile` node in orders-reconcile.json.
Three copies is two too many, but the repair path must write rows IDENTICAL to what the
normal sync writes; otherwise every repaired row would look like drift on the next run.

These tests pin the behaviours that have actually caused incidents, so a divergence between
the copies shows up here rather than as wrong money columns in a report.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "order_transform", Path(__file__).resolve().parents[1] / "scripts" / "order_transform.py")
ot = importlib.util.module_from_spec(_SPEC)
sys.modules["order_transform"] = ot
_SPEC.loader.exec_module(ot)


# ------------------------------------------------------------------ epoch handling

def test_epoch_string_is_not_mistaken_for_iso():
    """`created` is ALWAYS an epoch string, never ISO -- verified across 2,196 May-Aug orders.

    A naive `val[:4].isdigit()` check reads "1779..." as a year and passes the raw epoch
    through, which Postgres rejects. This regressed once already.
    """
    assert ot.ts_to_iso("1779599528.739927") == "2026-05-24T05:12:08.739927+00:00"


def test_real_iso_passes_through_untouched():
    assert ot.ts_to_iso("2026-06-02T07:00:00+00:00") == "2026-06-02T07:00:00+00:00"


def test_empty_and_zero_are_none():
    for v in (None, "", 0):
        assert ot.ts_to_iso(v) is None


# --------------------------------------------------------- decimal-cohort charge amounts

def test_integer_string_is_already_cents():
    assert ot.parse_charge_amount_cents("20065") == 20065


def test_decimal_euro_is_converted_not_truncated():
    """Issue #2: a decimal value is euros. Reading "84.07" as 84 cents understates 100x."""
    assert ot.parse_charge_amount_cents("84.07") == 8407


def test_single_decimal_place_is_the_10x_case():
    assert ot.parse_charge_amount_cents("840.7") == 84070


def test_unparseable_charge_is_none_not_zero():
    assert ot.parse_charge_amount_cents("n/a") is None
    assert ot.parse_charge_amount_cents(None) is None


# ------------------------------------------------------------------ platform orders

def _platform_order():
    return {
        "orderId": "e8578d18", "created": "1779599528.739927", "orderState": "CONFIRMED",
        "orderType": "Kierrätyspalvelu", "organizationName": "Kirill", "hubId": "hub-1",
        "firstSchedule": "2026-06-02T07:00:00+00:00", "commissionRate": 0.26,
        "charge": {"basePrice": "20065", "platformFee": "0", "servicesPrice": "0",
                   "vatPrice": "25182", "vatPercentage": "25.5"},
    }


def test_platform_order_total_matches_the_vat_formula():
    """total_excl_vat_cents must reconcile to vatPrice / (1 + vatPercentage/100)."""
    row = ot.transform_order(_platform_order())
    assert row["total_excl_vat_cents"] == 20065
    expected = float("25182") / 1.255
    assert abs(row["total_excl_vat_cents"] - expected) < 1.5
    assert row["is_manual"] is False
    assert row["is_asuntosaatio_gig"] == "No"
    assert row["manual_data"] is None


def test_missing_recycling_surcharge_does_not_break_the_total():
    """Most orders have no recyclingSurcharge; it must contribute 0, not None-poison."""
    row = ot.transform_order(_platform_order())
    assert row["recycling_surcharge_cents"] is None
    assert row["total_excl_vat_cents"] == 20065


def test_total_is_none_when_a_required_component_is_missing():
    """Better a null than a wrong partial sum."""
    o = _platform_order()
    del o["charge"]["platformFee"]
    assert ot.transform_order(o)["total_excl_vat_cents"] is None


def test_order_without_id_is_dropped():
    assert ot.transform_order({"created": "1779599528.739927"}) is None


# -------------------------------------------------------------------- manual orders

def test_manual_order_money_columns_stay_null():
    """Manual orders have no fee breakdown upstream -- never invent one."""
    row = ot.transform_order({
        "orderId": "m1", "created": "1779599528.739927",
        "customer": {"name": "X"}, "additionalInfo": "note",
        "charge": {"charge": "323.40", "paymentMethod": "invoice"}})
    assert row["is_manual"] is True
    assert row["platform_fee"] is None and row["total_excl_vat_cents"] is None
    assert row["manual_data"]["total_incl_vat_cents"] == 32340
    assert row["manual_data"]["price_basis"] == "gross_incl_vat"


def test_non_numeric_manual_charge_is_preserved_not_coerced():
    """"119e/h" is a rate, not a total. Keep the raw string, leave the total null."""
    row = ot.transform_order({
        "orderId": "m2", "created": "1779599528.739927",
        "customer": {"name": "X"}, "additionalInfo": "note",
        "charge": {"charge": "119e/h"}})
    assert row["manual_data"]["total_incl_vat_cents"] is None
    assert row["manual_data"]["raw_charge_total"] == "119e/h"


def test_asuntosaatio_zero_value_is_overridden():
    """Recorded as EUR 0 upstream; overridden to the real flat rate, original kept for audit."""
    row = ot.transform_order({
        "orderId": "m3", "created": "1779599528.739927",
        "customer": {"name": "X"}, "additionalInfo": "Asuntosäätiö sponsoroi muuton",
        "charge": {"charge": "0"}})
    assert row["is_asuntosaatio_gig"] == "Yes"
    assert row["manual_data"]["total_incl_vat_eur"] == 502
    assert row["manual_data"]["total_excl_vat_eur"] == 400
    assert row["manual_data"]["original_total_incl_vat_eur"] == 0


def test_asuntosaatio_mention_with_real_value_is_not_overridden():
    """The override requires BOTH a mention AND a zero value -- a paid job stays as-is."""
    row = ot.transform_order({
        "orderId": "m4", "created": "1779599528.739927",
        "customer": {"name": "X"}, "additionalInfo": "Asuntosäätiö",
        "charge": {"charge": "323.40"}})
    assert row["is_asuntosaatio_gig"] == "No"
    assert row["manual_data"]["total_incl_vat_cents"] == 32340


# ------------------------------------------------------------------------- dedupe

def test_dedupe_keeps_last_occurrence():
    """A repeated id in one batch fails the WHOLE upsert, not just that row."""
    rows = [{"order_id": "a", "v": 1}, {"order_id": "b", "v": 2}, {"order_id": "a", "v": 3}]
    out = ot.dedupe_by_order_id(rows)
    assert len(out) == 2
    assert next(r for r in out if r["order_id"] == "a")["v"] == 3
