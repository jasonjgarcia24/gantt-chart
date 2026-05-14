"""Tests for gantt_lib.critical_path — CPM forward/backward pass + slack."""
from __future__ import annotations

from datetime import date

import pytest

from gantt_lib.cascade import cascade
from gantt_lib.critical_path import compute_slack, critical_path
from gantt_lib.model import Program

from tests.fixtures.programs import MON, diamond, linear_chain, make_task, milestone_chain


def _slack(p: Program) -> dict[str, int]:
    cascade(p)
    return compute_slack(p)


def _path(p: Program) -> list[str]:
    cascade(p)
    return critical_path(p)


# ---------- linear chain: every task is critical ----------

def test_linear_chain_all_tasks_have_zero_slack():
    p = linear_chain()  # 1 (3d) → 2 (2d, FS) → 3 (1d, FS)
    s = _slack(p)
    assert s == {"1": 0, "2": 0, "3": 0}


def test_linear_chain_critical_path_is_full_chain_in_topo_order():
    assert _path(linear_chain()) == ["1", "2", "3"]


# ---------- diamond: shorter branch has slack ----------

def test_diamond_long_branch_is_critical_short_branch_has_slack():
    p = diamond()  # 1 → {2 (2d), 3 (5d)} → 4 (1d, [2FS, 3FS])
    s = _slack(p)
    assert s["1"] == 0
    assert s["3"] == 0
    assert s["4"] == 0
    # Task 2 has slack (5 - 2 = 3 working days slack between branches).
    assert s["2"] == 3


def test_diamond_critical_path_takes_the_long_branch():
    cp = _path(diamond())
    assert cp == ["1", "3", "4"]
    assert "2" not in cp


# ---------- milestone on the path ----------

def test_milestone_with_ff_predecessor_is_critical():
    # 1 (5d, manual MON) → M (milestone, 0d, 1FF). Both should be critical.
    p = milestone_chain()
    s = _slack(p)
    assert s["1"] == 0
    assert s["M"] == 0
    assert _path(p) == ["1", "M"]


# ---------- multiple sinks: only the latest-ending sink is critical ----------

def test_multiple_sinks_only_latest_ending_is_critical():
    p = Program(
        name="parallel",
        tasks=[
            make_task("1", duration=2, start=MON),  # ends Tue
            make_task("2", duration=5, start=MON),  # ends Fri (later)
        ],
    )
    s = _slack(p)
    # Both are sinks; project end = task 2's end (Fri).
    # Task 1 has 3 working days slack (Tue → could finish Fri).
    assert s["2"] == 0
    assert s["1"] == 3


# ---------- empty / edge ----------

def test_empty_program_yields_empty_slack_and_path():
    p = Program(name="empty")
    assert compute_slack(p) == {}
    assert critical_path(p) == []


def test_single_task_is_its_own_critical_path():
    p = Program(name="solo", tasks=[make_task("1", duration=3, start=MON)])
    cascade(p)
    assert compute_slack(p) == {"1": 0}
    assert critical_path(p) == ["1"]


# ---------- ss / ff / sf relations ----------

def test_ss_dependency_predecessor_is_anchor():
    # 1 SS-binds 2 (they start together). Shifting 1's start shifts 2's start,
    # so 1 has zero slack — it anchors the only path to the project end.
    p = Program(
        name="ss",
        tasks=[
            make_task("1", duration=2, start=MON),
            make_task("2", duration=5, predecessors="1SS"),
        ],
    )
    cascade(p)
    s = compute_slack(p)
    assert s["1"] == 0  # anchor for the chain
    assert s["2"] == 0  # sink + critical


def test_ff_dependency_critical_when_predecessor_is_long():
    p = Program(
        name="ff",
        tasks=[
            make_task("1", duration=5, start=MON),  # MON-FRI
            make_task("2", duration=2, predecessors="1FF"),  # ends FRI; starts THU
        ],
    )
    cascade(p)
    s = compute_slack(p)
    assert s["1"] == 0
    assert s["2"] == 0  # both end FRI; both critical


# ---------- with holidays ----------

def test_holidays_extend_critical_path_length():
    # 1 (5d, MON start), holiday WED. Task 1 end shifts from FRI to next MON.
    p = Program(
        name="holiday",
        tasks=[make_task("1", duration=5, start=MON)],
        holidays={date(2026, 5, 13)},  # Wed of MON's week
    )
    cascade(p)
    assert p.tasks[0].end == date(2026, 5, 18)  # Mon following Fri
    s = compute_slack(p)
    assert s["1"] == 0
