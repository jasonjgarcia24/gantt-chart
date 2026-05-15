"""Tests for gantt_lib.baseline — pure logic for baseline tracking."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from gantt_lib.baseline import (
    BASELINE_HEADERS,
    BASELINE_TAB,
    BaselineRow,
    Summary,
    active_baselines,
    compute_summary,
    cp_duration_days,
    reconstruct_baseline_program,
    slip_days,
)
from gantt_lib.cascade import cascade
from gantt_lib.model import Program, Status, Task
from tests.fixtures.baselines import make_baseline_row
from tests.fixtures.programs import diamond, linear_chain


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


# ---------- cp_duration_days ----------

def test_cp_duration_days_linear_chain():
    """Linear 1(dur=3) → 2(dur=2) → 3(dur=1), Mon May 11 anchor.

    After cascade: 1=May11-13, 2=May14-15, 3=May18 (Mon, weekend skip).
    All three are on the CP (no parallelism). Calendar span = May 11..18 = 8 days.
    """
    p = linear_chain()
    cascade(p)
    assert cp_duration_days(p) == 8


def test_cp_duration_days_diamond_uses_longer_branch():
    """Diamond: 1 → {2(dur=2), 3(dur=5)} → 4(dur=1). Long branch is critical."""
    p = diamond()
    cascade(p)
    cp_days = cp_duration_days(p)
    # 1=May11-12, 3=May13-19 (Wed→Tue, +5wd from Wed = May 13,14,15,18,19),
    # 4=May20. Calendar span = May 11..20 = 10 days.
    assert cp_days == 10


def test_cp_duration_days_empty_program_returns_none():
    p = Program(name="empty", tasks=[])
    assert cp_duration_days(p) is None


def test_cp_duration_days_returns_none_when_no_dates():
    """A program where cascade hasn't been run (no start/end) yields no CP."""
    p = Program(name="bare", tasks=[Task(id="1", name="x", duration=1)])
    assert cp_duration_days(p) is None


# ---------- reconstruct_baseline_program ----------

def test_reconstruct_program_preserves_dates_and_predecessors():
    rows = [
        make_baseline_row(
            wbs="1", baseline_start=date(2026, 4, 1),
            baseline_end=date(2026, 4, 8), baseline_duration=6,
            baseline_predecessors="",
        ),
        make_baseline_row(
            wbs="2", baseline_start=date(2026, 4, 9),
            baseline_end=date(2026, 4, 22), baseline_duration=10,
            baseline_predecessors="1FS",
        ),
    ]
    p = reconstruct_baseline_program("TPM90", rows)
    assert p.name == "TPM90"
    assert {t.id for t in p.tasks} == {"1", "2"}
    t2 = next(t for t in p.tasks if t.id == "2")
    assert t2.start == date(2026, 4, 9)
    assert t2.end == date(2026, 4, 22)
    assert t2.predecessors == "1FS"


def test_reconstruct_program_drops_predecessors_to_missing_wbs():
    """Baselines may reference WBS ids deleted before this snapshot.

    Rather than failing topo sort, drop the dangling reference. The baseline
    plan is read as-was, with whatever structural integrity remains.
    """
    rows = [
        make_baseline_row(wbs="2", baseline_predecessors="1FS, 99FS+3"),
    ]
    p = reconstruct_baseline_program("TPM90", rows)
    t2 = next(t for t in p.tasks if t.id == "2")
    # Both 1 and 99 are missing → both dropped → empty predecessor string.
    assert t2.predecessors == ""


def test_reconstruct_program_keeps_lag_when_pred_kept():
    rows = [
        make_baseline_row(wbs="1"),
        make_baseline_row(wbs="2", baseline_predecessors="1FS+3"),
    ]
    p = reconstruct_baseline_program("TPM90", rows)
    t2 = next(t for t in p.tasks if t.id == "2")
    assert t2.predecessors == "1FS+3"


# ---------- compute_summary ----------

def _current_program_from_baselines(rows: list[BaselineRow]) -> Program:
    """Build a 'current' program identical to the baseline — zero slip case."""
    return reconstruct_baseline_program(rows[0].program if rows else "X", rows)


def test_compute_summary_no_baselines_yields_no_slip_stats():
    p = linear_chain()
    cascade(p)
    s = compute_summary("linear", p, [], today=date(2026, 5, 14))
    assert s.last_baseline_date is None
    assert s.last_baseline_actor == ""
    assert s.days_since_baseline is None
    assert s.tasks_baselined == 0
    assert s.tasks_not_baselined == 3
    assert s.max_end_slip is None
    assert s.mean_end_slip is None
    assert s.baseline_cp_days is None
    assert s.cp_delta is None
    # Current CP is still computable
    assert s.current_cp_days == 8


def test_compute_summary_zero_slip_when_current_matches_baseline():
    """Snapshot the cascade'd linear chain, then compare against itself."""
    p = linear_chain()
    cascade(p)
    rows = [
        make_baseline_row(
            program="linear", wbs=t.id, task_name=t.name,
            snapshot_date=date(2026, 4, 1),
            baseline_start=t.start, baseline_end=t.end,
            baseline_duration=t.duration,
            baseline_predecessors=t.predecessors,
        )
        for t in p.tasks
    ]
    s = compute_summary("linear", p, rows, today=date(2026, 5, 14))
    assert s.tasks_baselined == 3
    assert s.tasks_not_baselined == 0
    assert s.max_end_slip == 0
    assert s.mean_end_slip == pytest.approx(0.0)
    assert s.slipping_count == 0
    assert s.ahead_count == 0
    assert s.on_baseline_count == 3
    assert s.cp_delta == 0


def test_compute_summary_positive_slip_when_current_is_later():
    """Push the linear chain's third task end out by 5 days; max slip = +5."""
    p = linear_chain()
    cascade(p)
    rows = [
        make_baseline_row(
            program="linear", wbs=t.id, task_name=t.name,
            baseline_start=t.start, baseline_end=t.end,
            baseline_duration=t.duration,
            baseline_predecessors=t.predecessors,
        )
        for t in p.tasks
    ]
    # Slip task 3's current end by 5 calendar days.
    p.tasks[2].end = p.tasks[2].end + timedelta(days=5)
    s = compute_summary("linear", p, rows, today=date(2026, 5, 14))
    assert s.max_end_slip == 5
    assert s.slipping_count == 1
    assert s.ahead_count == 0
    assert s.on_baseline_count == 2


def test_compute_summary_ahead_when_current_is_earlier():
    """Pull a task's end IN by 3 days; ahead_count rises."""
    p = linear_chain()
    cascade(p)
    rows = [
        make_baseline_row(
            program="linear", wbs=t.id, task_name=t.name,
            baseline_start=t.start, baseline_end=t.end,
            baseline_duration=t.duration,
            baseline_predecessors=t.predecessors,
        )
        for t in p.tasks
    ]
    p.tasks[1].end = p.tasks[1].end - timedelta(days=3)
    s = compute_summary("linear", p, rows, today=date(2026, 5, 14))
    assert s.ahead_count == 1
    assert s.max_end_slip == 0  # max is over signed slips, so 0 wins over -3


def test_compute_summary_status_counts():
    p = Program(name="x", tasks=[
        Task(id="1", name="a", status=Status.DONE),
        Task(id="2", name="b", status=Status.IN_PROGRESS),
        Task(id="3", name="c", status=Status.NOT_STARTED),
        Task(id="4", name="d", status=""),  # empty counts as not started
        Task(id="5", name="e", status=Status.BLOCKED),
        Task(id="6", name="f", status=Status.AT_RISK),
    ])
    s = compute_summary("x", p, [], today=date(2026, 5, 14))
    assert s.tasks_done == 1
    assert s.tasks_in_progress == 1
    assert s.tasks_not_started == 2
    assert s.tasks_blocked == 1
    assert s.tasks_at_risk == 1


def test_compute_summary_orphan_baseline_excluded_from_slip_stats():
    """A baseline row for a task no longer in the program does not count."""
    p = Program(name="x", tasks=[
        Task(id="1", name="a", start=date(2026, 4, 1), end=date(2026, 4, 1),
             duration=1),
    ])
    rows = [
        make_baseline_row(
            program="x", wbs="1",
            baseline_start=date(2026, 4, 1), baseline_end=date(2026, 4, 1),
            baseline_duration=1,
        ),
        make_baseline_row(
            program="x", wbs="999",  # task no longer exists
            baseline_start=date(2026, 4, 1), baseline_end=date(2026, 4, 10),
            baseline_duration=8,
        ),
    ]
    s = compute_summary("x", p, rows, today=date(2026, 5, 14))
    # Only WBS 1 is in current → only 1 in slip stats; orphan ignored.
    assert s.on_baseline_count == 1
    assert s.tasks_baselined == 1
    assert s.tasks_not_baselined == 0
    assert s.total_tasks == 1


def test_compute_summary_last_baseline_picks_most_recent():
    """When multiple snapshots exist, last_baseline_date is the latest one."""
    rows = [
        make_baseline_row(wbs="1", snapshot_date=date(2026, 4, 1), snapshot_actor="alice"),
        make_baseline_row(wbs="1", snapshot_date=date(2026, 5, 1), snapshot_actor="bob"),
    ]
    p = Program(name="TPM90", tasks=[])
    s = compute_summary("TPM90", p, rows, today=date(2026, 5, 14))
    assert s.last_baseline_date == date(2026, 5, 1)
    assert s.last_baseline_actor == "bob"
    assert s.days_since_baseline == 13


def test_compute_summary_cp_delta_zero_when_holidays_present_and_match():
    """Regression: with holidays in current, baseline reconstruction must use
    the same holidays so backward-pass slack agrees and CP membership matches.

    Caught live: a TPM90 baseline taken right after rebaseline showed
    Critical: 15d → 67d (+52d) despite zero per-task slip — because the
    baseline program was reconstructed with holidays=set() while current used
    real US federal holidays, forcing different CP membership.
    """
    holidays = {date(2026, 5, 25)}  # Memorial Day mid-program
    p = Program(
        name="hol",
        tasks=[
            Task(id="1", level=1, name="A", duration=3, start=date(2026, 5, 11)),
            Task(id="2", level=1, name="B", duration=2, predecessors="1FS"),
            Task(id="3", level=1, name="C", duration=8, predecessors="2FS"),
        ],
        holidays=holidays,
    )
    cascade(p)
    rows = [
        make_baseline_row(
            program="hol", wbs=t.id, task_name=t.name,
            baseline_start=t.start, baseline_end=t.end,
            baseline_duration=t.duration,
            baseline_predecessors=t.predecessors,
        )
        for t in p.tasks
    ]
    s = compute_summary("hol", p, rows, today=date(2026, 5, 14))
    assert s.cp_delta == 0
    assert s.baseline_cp_days == s.current_cp_days


def test_compute_summary_cp_delta_when_current_extended():
    """Baseline CP = 8d (linear chain, dur=3+2+1). Current grows task 3 dur to 6 → CP delta positive.

    Use a freshly-cascaded current program (not a hand-mutated baseline copy)
    so all start/end/duration are mutually consistent — the backward pass
    needs that consistency to compute slack correctly.
    """
    rows = [
        make_baseline_row(
            program="linear", wbs="1", task_name="Task 1",
            baseline_start=date(2026, 5, 11), baseline_end=date(2026, 5, 13),
            baseline_duration=3, baseline_predecessors="",
        ),
        make_baseline_row(
            program="linear", wbs="2", task_name="Task 2",
            baseline_start=date(2026, 5, 14), baseline_end=date(2026, 5, 15),
            baseline_duration=2, baseline_predecessors="1FS",
        ),
        make_baseline_row(
            program="linear", wbs="3", task_name="Task 3",
            baseline_start=date(2026, 5, 18), baseline_end=date(2026, 5, 18),
            baseline_duration=1, baseline_predecessors="2FS",
        ),
    ]
    p = Program(name="linear", tasks=[
        Task(id="1", level=1, name="Task 1", duration=3, start=date(2026, 5, 11)),
        Task(id="2", level=1, name="Task 2", duration=2, predecessors="1FS"),
        Task(id="3", level=1, name="Task 3", duration=6, predecessors="2FS"),
    ])
    cascade(p)
    s = compute_summary("linear", p, rows, today=date(2026, 5, 14))
    assert s.baseline_cp_days == 8                # May 11..18 = 8 calendar days
    assert s.current_cp_days == 15                # May 11..25 = 15 calendar days
    assert s.cp_delta == 7

