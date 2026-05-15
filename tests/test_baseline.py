"""Tests for gantt_lib.baseline — pure logic for baseline tracking."""
from __future__ import annotations

from datetime import date

from gantt_lib.baseline import (
    BASELINE_HEADERS,
    BASELINE_TAB,
    BaselineRow,
    active_baselines,
    slip_days,
)
from tests.fixtures.baselines import make_baseline_row


# ---------- module constants ----------

def test_baseline_tab_name():
    assert BASELINE_TAB == "_Baselines"


def test_baseline_headers_match_spec_order():
    """Header order is the column-write contract — must match the spec exactly."""
    assert BASELINE_HEADERS == [
        "program", "wbs", "task_name", "snapshot_date",
        "baseline_start", "baseline_end", "baseline_duration",
        "baseline_predecessors", "snapshot_label", "snapshot_actor",
    ]


def test_baseline_row_optional_fields_default_to_empty_string():
    r = BaselineRow(
        program="TPM90",
        wbs="1",
        task_name="Concept",
        snapshot_date=date(2026, 4, 1),
        baseline_start=date(2026, 4, 1),
        baseline_end=date(2026, 4, 8),
        baseline_duration=6,
    )
    assert r.baseline_predecessors == ""
    assert r.snapshot_label == ""
    assert r.snapshot_actor == ""


def test_baseline_row_is_frozen():
    """Immutability prevents accidental mutation of historical snapshots."""
    import dataclasses
    r = make_baseline_row()
    try:
        r.baseline_start = date(2030, 1, 1)
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("BaselineRow should be frozen")


# ---------- active_baselines ----------

def test_active_baselines_empty_input():
    assert active_baselines([], "TPM90") == {}


def test_active_baselines_single_program_single_snapshot():
    rows = [
        make_baseline_row(wbs="1", snapshot_date=date(2026, 4, 1)),
        make_baseline_row(wbs="2", snapshot_date=date(2026, 4, 1)),
    ]
    out = active_baselines(rows, "TPM90")
    assert set(out.keys()) == {"1", "2"}


def test_active_baselines_filters_by_program_name():
    rows = [
        make_baseline_row(program="TPM90", wbs="1"),
        make_baseline_row(program="Q3Launch", wbs="1"),
    ]
    out = active_baselines(rows, "TPM90")
    assert len(out) == 1
    assert out["1"].program == "TPM90"


def test_active_baselines_returns_latest_snapshot_per_wbs():
    rows = [
        make_baseline_row(wbs="1", snapshot_date=date(2026, 4, 1), snapshot_label="old"),
        make_baseline_row(wbs="1", snapshot_date=date(2026, 5, 1), snapshot_label="new"),
        make_baseline_row(wbs="1", snapshot_date=date(2026, 4, 15), snapshot_label="middle"),
    ]
    out = active_baselines(rows, "TPM90")
    assert out["1"].snapshot_date == date(2026, 5, 1)
    assert out["1"].snapshot_label == "new"


def test_active_baselines_ties_resolved_by_iteration_order():
    """When snapshot_date is identical, the later-encountered row wins.

    This matches the workbook semantic: rows are appended to the bottom of
    `_Baselines`, so a top-down read returns chronological order. The most
    recent same-day re-snapshot is therefore the last one read.
    """
    rows = [
        make_baseline_row(wbs="1", snapshot_date=date(2026, 4, 1), snapshot_label="first"),
        make_baseline_row(wbs="1", snapshot_date=date(2026, 4, 1), snapshot_label="second"),
    ]
    out = active_baselines(rows, "TPM90")
    assert out["1"].snapshot_label == "second"


def test_active_baselines_missing_program_returns_empty():
    rows = [make_baseline_row(program="TPM90", wbs="1")]
    assert active_baselines(rows, "DoesNotExist") == {}


def test_active_baselines_distinct_wbs_within_one_program():
    rows = [
        make_baseline_row(wbs="1", snapshot_date=date(2026, 4, 1)),
        make_baseline_row(wbs="2", snapshot_date=date(2026, 4, 1)),
        make_baseline_row(wbs="2.1", snapshot_date=date(2026, 4, 1)),
    ]
    out = active_baselines(rows, "TPM90")
    assert set(out.keys()) == {"1", "2", "2.1"}


# ---------- slip_days ----------

def test_slip_days_positive_when_current_after_baseline():
    assert slip_days(date(2026, 4, 10), date(2026, 4, 1)) == 9


def test_slip_days_zero_on_baseline():
    assert slip_days(date(2026, 4, 1), date(2026, 4, 1)) == 0


def test_slip_days_negative_means_ahead_of_baseline():
    assert slip_days(date(2026, 4, 1), date(2026, 4, 10)) == -9


def test_slip_days_none_current_returns_none():
    assert slip_days(None, date(2026, 4, 1)) is None


def test_slip_days_none_baseline_returns_none():
    assert slip_days(date(2026, 4, 1), None) is None


def test_slip_days_both_none_returns_none():
    assert slip_days(None, None) is None


def test_slip_days_uses_calendar_not_working_days():
    """Friday May 1 → Wednesday May 6 = 5 calendar days (3 working).

    Slip is a human-facing metric; a 5-day slip across a weekend should read as
    +5, not +3, so the reader's intuition about elapsed time matches the number.
    """
    assert slip_days(date(2026, 5, 6), date(2026, 5, 1)) == 5
