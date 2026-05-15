"""Tests for gantt_lib.deck.data — pure-logic content selection per slide."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from gantt_lib.cascade import cascade
from gantt_lib.deck.data import (
    CPRow,
    ForwardMilestone,
    MilestoneSlipRow,
    PortfolioRow,
    RiskRow,
    blockers,
    critical_path_by_program,
    critical_path_due_soon,
    forward_look_30d,
    gantt_zoom_window,
    milestone_slip_summary,
    portfolio_status,
    recently_completed,
    this_week_and_next,
    top_risks,
    _percent_complete_weighted,
    _worst_status,
)
from gantt_lib.model import Program, Status, Task
from tests.fixtures.baselines import make_baseline_row


TODAY = date(2026, 5, 14)


# ---------- helpers ----------

def _t(
    id: str, name: str = "x", *, duration: int = 1,
    start: date | None = None, end: date | None = None,
    status: str = Status.NOT_STARTED, predecessors: str = "",
    milestone: bool = False, percent: int = 0,
) -> Task:
    return Task(
        id=id, level=1, name=name, duration=duration,
        start=start, end=end, status=status,
        predecessors=predecessors, milestone=milestone,
        percent_complete=percent,
    )


# ============================================================================
# Tactical
# ============================================================================

# ---------- this_week_and_next ----------

def test_this_week_and_next_includes_tasks_in_window():
    p = Program(name="x", tasks=[
        _t("1", start=TODAY, end=TODAY + timedelta(days=3)),
        _t("2", start=TODAY + timedelta(days=10), end=TODAY + timedelta(days=12)),
    ])
    out = this_week_and_next(p, TODAY)
    assert [t.id for t in out] == ["1", "2"]


def test_this_week_and_next_excludes_already_done():
    p = Program(name="x", tasks=[
        _t("1", start=TODAY, end=TODAY + timedelta(days=3), status=Status.DONE),
        _t("2", start=TODAY, end=TODAY + timedelta(days=3), status=Status.IN_PROGRESS),
    ])
    out = this_week_and_next(p, TODAY)
    assert [t.id for t in out] == ["2"]


def test_this_week_and_next_excludes_past_end_dates():
    """Task that already ended (end < today) is out of window."""
    p = Program(name="x", tasks=[
        _t("1", start=TODAY - timedelta(days=10), end=TODAY - timedelta(days=1)),
    ])
    assert this_week_and_next(p, TODAY) == []


def test_this_week_and_next_excludes_starts_beyond_horizon():
    """Task starting > 14 days out is excluded."""
    p = Program(name="x", tasks=[
        _t("1", start=TODAY + timedelta(days=20), end=TODAY + timedelta(days=25)),
    ])
    assert this_week_and_next(p, TODAY) == []


def test_this_week_and_next_skips_unscheduled_tasks():
    """Tasks with no start/end are filtered out (no date data to evaluate)."""
    p = Program(name="x", tasks=[_t("1")])
    assert this_week_and_next(p, TODAY) == []


def test_this_week_and_next_sorts_by_end_then_wbs():
    p = Program(name="x", tasks=[
        _t("2", start=TODAY, end=TODAY + timedelta(days=5)),
        _t("1", start=TODAY, end=TODAY + timedelta(days=5)),
        _t("3", start=TODAY, end=TODAY + timedelta(days=2)),
    ])
    out = this_week_and_next(p, TODAY)
    assert [t.id for t in out] == ["3", "1", "2"]


# ---------- blockers ----------

def test_blockers_returns_blocked_and_at_risk():
    p = Program(name="x", tasks=[
        _t("1", status=Status.DONE),
        _t("2", status=Status.BLOCKED, predecessors="1FS"),
        _t("3", status=Status.AT_RISK),
        _t("4", status=Status.IN_PROGRESS),
    ])
    out = blockers(p)
    assert [t.id for t, _open in out] == ["2", "3"]


def test_blockers_open_predecessor_list_excludes_done_predecessors():
    """Blocking predecessors that are themselves Done don't block anymore."""
    p = Program(name="x", tasks=[
        _t("1", status=Status.DONE),
        _t("2", status=Status.IN_PROGRESS),
        _t("3", status=Status.BLOCKED, predecessors="1FS, 2FS"),
    ])
    out = blockers(p)
    assert len(out) == 1
    _task, open_preds = out[0]
    assert open_preds == ["2"]  # 1 is Done, dropped


def test_blockers_sorts_blocked_before_at_risk():
    p = Program(name="x", tasks=[
        _t("1", status=Status.AT_RISK),
        _t("2", status=Status.BLOCKED),
    ])
    out = blockers(p)
    assert [t.id for t, _ in out] == ["2", "1"]


def test_blockers_handles_predecessor_referencing_unknown_id():
    """Predecessor pointing at a non-existent WBS — silently ignored, not crashed."""
    p = Program(name="x", tasks=[
        _t("2", status=Status.BLOCKED, predecessors="999FS"),
    ])
    out = blockers(p)
    assert len(out) == 1
    _task, open_preds = out[0]
    assert open_preds == []


# ---------- critical_path_due_soon ----------

def test_cp_due_soon_filters_to_cp_tasks_in_window():
    """Linear chain — all 3 tasks on CP. Only the next-14d ones make it through."""
    p = Program(name="x", tasks=[
        _t("1", duration=3, start=TODAY),
        _t("2", duration=2, predecessors="1FS"),
        _t("3", duration=1, predecessors="2FS"),
    ])
    cascade(p)
    out = critical_path_due_soon(p, TODAY)
    # All 3 finish within 14d, so all 3 appear; sorted by end asc.
    assert [t.id for t in out] == ["1", "2", "3"]


def test_cp_due_soon_empty_when_no_critical_path():
    p = Program(name="x", tasks=[])
    assert critical_path_due_soon(p, TODAY) == []


# ---------- recently_completed ----------

def test_recently_completed_in_last_7_days():
    p = Program(name="x", tasks=[
        _t("1", end=TODAY - timedelta(days=3), status=Status.DONE),
        _t("2", end=TODAY - timedelta(days=10), status=Status.DONE),  # too old
        _t("3", end=TODAY, status=Status.DONE),
        _t("4", end=TODAY - timedelta(days=2), status=Status.IN_PROGRESS),  # not done
    ])
    out = recently_completed(p, TODAY)
    assert [t.id for t in out] == ["3", "1"]  # newest first


# ---------- gantt_zoom_window ----------

def test_gantt_zoom_window_includes_overlapping_tasks():
    p = Program(name="x", tasks=[
        _t("1", start=TODAY - timedelta(days=5), end=TODAY + timedelta(days=2)),  # overlap
        _t("2", start=TODAY + timedelta(days=10), end=TODAY + timedelta(days=20)),
        _t("3", start=TODAY + timedelta(days=40), end=TODAY + timedelta(days=45)),  # past horizon
        _t("4", start=TODAY - timedelta(days=20), end=TODAY - timedelta(days=10)),  # already done
    ])
    out = gantt_zoom_window(p, TODAY)
    assert [t.id for t in out] == ["1", "2"]


def test_gantt_zoom_window_sorts_by_start_asc():
    p = Program(name="x", tasks=[
        _t("1", start=TODAY + timedelta(days=10), end=TODAY + timedelta(days=12)),
        _t("2", start=TODAY, end=TODAY + timedelta(days=2)),
    ])
    out = gantt_zoom_window(p, TODAY)
    assert [t.id for t in out] == ["2", "1"]


# ============================================================================
# Strategic
# ============================================================================

# ---------- _worst_status / _percent_complete_weighted ----------

def test_worst_status_blocked_wins():
    tasks = [_t("1", status=Status.IN_PROGRESS), _t("2", status=Status.BLOCKED)]
    assert _worst_status(tasks) == Status.BLOCKED


def test_worst_status_at_risk_beats_in_progress():
    tasks = [_t("1", status=Status.IN_PROGRESS), _t("2", status=Status.AT_RISK)]
    assert _worst_status(tasks) == Status.AT_RISK


def test_worst_status_all_done():
    tasks = [_t("1", status=Status.DONE), _t("2", status=Status.DONE)]
    assert _worst_status(tasks) == Status.DONE


def test_worst_status_empty_program_is_not_started():
    assert _worst_status([]) == Status.NOT_STARTED


def test_percent_complete_weighted_by_duration():
    tasks = [
        _t("1", duration=5, percent=100),
        _t("2", duration=5, percent=0),
    ]
    # Equal weights, average = 50
    assert _percent_complete_weighted(tasks) == 50


def test_percent_complete_weighted_handles_zero_total_duration():
    """All-zero-duration program shouldn't divide by zero."""
    tasks = [_t("1", duration=0, percent=50)]
    assert _percent_complete_weighted(tasks) == 0


# ---------- portfolio_status ----------

def test_portfolio_status_one_row_per_program():
    p1 = Program(name="A", tasks=[_t("1", status=Status.DONE, duration=5, percent=100)])
    p2 = Program(name="B", tasks=[_t("1", status=Status.BLOCKED, duration=5)])
    out = portfolio_status([p1, p2], [])
    assert [r.program for r in out] == ["A", "B"]
    assert out[0].status == Status.DONE
    assert out[1].status == Status.BLOCKED


def test_portfolio_status_picks_latest_baseline_per_program():
    p = Program(name="A", tasks=[_t("1")])
    rows = [
        make_baseline_row(program="A", snapshot_date=date(2026, 4, 1), snapshot_actor="alice"),
        make_baseline_row(program="A", snapshot_date=date(2026, 5, 1), snapshot_actor="bob"),
    ]
    out = portfolio_status([p], rows)
    assert out[0].last_baseline_date == date(2026, 5, 1)
    assert out[0].last_baseline_actor == "bob"


def test_portfolio_status_no_baseline_yields_empty_metadata():
    p = Program(name="A", tasks=[_t("1")])
    out = portfolio_status([p], [])
    assert out[0].last_baseline_date is None
    assert out[0].last_baseline_actor == ""


# ---------- milestone_slip_summary ----------

def test_milestone_slip_summary_only_milestones():
    p = Program(name="A", tasks=[
        _t("1", milestone=False, end=TODAY),
        _t("2", milestone=True, end=TODAY + timedelta(days=5)),
    ])
    out = milestone_slip_summary([p], [])
    assert len(out) == 1
    assert out[0].wbs == "2"


def test_milestone_slip_summary_computes_slip_against_baseline():
    p = Program(name="A", tasks=[
        _t("1", milestone=True, end=TODAY + timedelta(days=10)),
    ])
    rows = [make_baseline_row(
        program="A", wbs="1",
        baseline_start=TODAY, baseline_end=TODAY + timedelta(days=5),
    )]
    out = milestone_slip_summary([p], rows)
    assert out[0].slip == 5  # 10 - 5 = +5 calendar days


def test_milestone_slip_summary_sorts_by_current_end():
    p = Program(name="A", tasks=[
        _t("1", milestone=True, end=TODAY + timedelta(days=20)),
        _t("2", milestone=True, end=TODAY + timedelta(days=5)),
    ])
    out = milestone_slip_summary([p], [])
    assert [r.wbs for r in out] == ["2", "1"]


# ---------- critical_path_by_program ----------

def test_cp_by_program_no_baseline_yields_none_delta():
    p = Program(name="A", tasks=[_t("1", duration=3, start=TODAY)])
    cascade(p)
    out = critical_path_by_program([p], [])
    assert out[0].baseline_cp_days is None
    assert out[0].delta is None
    assert out[0].current_cp_days is not None  # CP exists from cascade


def test_cp_by_program_zero_delta_when_current_matches_baseline():
    p = Program(name="A", tasks=[
        _t("1", duration=3, start=TODAY),
        _t("2", duration=2, predecessors="1FS"),
    ])
    cascade(p)
    rows = [
        make_baseline_row(program="A", wbs=t.id, baseline_start=t.start,
                          baseline_end=t.end, baseline_duration=t.duration,
                          baseline_predecessors=t.predecessors)
        for t in p.tasks
    ]
    out = critical_path_by_program([p], rows)
    assert out[0].delta == 0


# ---------- top_risks ----------

def test_top_risks_filters_to_blocked_and_at_risk():
    p = Program(name="A", tasks=[
        _t("1", status=Status.BLOCKED),
        _t("2", status=Status.AT_RISK),
        _t("3", status=Status.IN_PROGRESS),
    ])
    out = top_risks([p], [])
    assert {r.wbs for r in out} == {"1", "2"}


def test_top_risks_ranks_cp_tasks_higher():
    """A blocked task on the CP outranks a blocked off-CP task at the same slip."""
    p = Program(name="A", tasks=[
        _t("1", duration=3, start=TODAY, status=Status.BLOCKED),
        _t("2", duration=2, predecessors="1FS", status=Status.BLOCKED),
    ])
    cascade(p)
    # Both on CP (linear chain). Both have slip 0 (no baseline) → tie.
    # Add baselines so 2 has slip 0 and 1 has slip 5 → 1 should outrank.
    rows = [
        make_baseline_row(program="A", wbs="1",
                          baseline_start=TODAY - timedelta(days=5),
                          baseline_end=TODAY - timedelta(days=3)),
        make_baseline_row(program="A", wbs="2",
                          baseline_start=p.tasks[1].start,
                          baseline_end=p.tasks[1].end),
    ]
    out = top_risks([p], rows)
    assert out[0].wbs == "1"
    assert out[0].impact_score > 0


def test_top_risks_returns_at_most_n():
    tasks = [_t(str(i), status=Status.BLOCKED) for i in range(1, 11)]
    p = Program(name="A", tasks=tasks)
    out = top_risks([p], [], n=3)
    assert len(out) == 3


# ---------- forward_look_30d ----------

def test_forward_look_30d_only_milestones_in_window():
    p = Program(name="A", tasks=[
        _t("1", milestone=True, end=TODAY + timedelta(days=5)),
        _t("2", milestone=True, end=TODAY + timedelta(days=45)),  # past horizon
        _t("3", milestone=False, end=TODAY + timedelta(days=10)),  # not a milestone
        _t("4", milestone=True, end=TODAY - timedelta(days=2)),  # past
    ])
    out = forward_look_30d([p], TODAY)
    assert [m.wbs for m in out] == ["1"]


def test_forward_look_30d_aggregates_across_programs():
    pa = Program(name="A", tasks=[_t("1", milestone=True, end=TODAY + timedelta(days=10))])
    pb = Program(name="B", tasks=[_t("1", milestone=True, end=TODAY + timedelta(days=5))])
    out = forward_look_30d([pa, pb], TODAY)
    assert [m.program for m in out] == ["B", "A"]  # sorted by date asc


def test_forward_look_30d_includes_days_from_today():
    p = Program(name="A", tasks=[_t("1", milestone=True, end=TODAY + timedelta(days=7))])
    out = forward_look_30d([p], TODAY)
    assert out[0].days_from_today == 7
