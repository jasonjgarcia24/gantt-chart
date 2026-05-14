"""Tests for gantt_lib.cascade — topological sort + dependency engine.

Semantics tested:
- FS (default): successor starts the working day after predecessor ends, plus lag.
- SS: successor starts when predecessor starts, plus lag.
- FF: successor ENDS when predecessor ends, plus lag (succ.start derived from end).
- SF: successor ENDS when predecessor starts, plus lag (rare).
- Multiple predecessors → succ.start is the max of all derived constraints.
- Cycle → CycleError mentioning IDs in the cycle.
- Task with no predecessors AND no manual start → UnanchoredError.
- Milestone (duration=0) → end == start.
- Idempotence: cascade twice yields the same result.
"""
from __future__ import annotations

from datetime import date

import pytest

from gantt_lib.cascade import (
    CycleError,
    MissingPredecessorError,
    UnanchoredError,
    cascade,
)
from gantt_lib.model import Program, Task

from tests.fixtures.programs import (
    MON,
    cycle,
    diamond,
    linear_chain,
    make_task,
    milestone_chain,
    unanchored,
)

# Calendar anchors (Mon = 2026-05-11).
TUE = date(2026, 5, 12)
WED = date(2026, 5, 13)
THU = date(2026, 5, 14)
FRI = date(2026, 5, 15)
NEXT_MON = date(2026, 5, 18)
NEXT_TUE = date(2026, 5, 19)
NEXT_WED = date(2026, 5, 20)
NEXT_THU = date(2026, 5, 21)
NEXT_FRI = date(2026, 5, 22)
WEEK3_MON = date(2026, 5, 25)
WEEK3_TUE = date(2026, 5, 26)


# ---------- single-task baseline ----------

def test_single_anchored_task_computes_end_from_duration():
    p = Program(name="x", tasks=[make_task("1", duration=3, start=MON)])
    cascade(p)
    t = p.find("1")
    assert t.start == MON
    assert t.end == WED  # MON + 2 working days = WED (3-day duration: MON, TUE, WED)


def test_single_milestone_end_equals_start():
    p = Program(name="x", tasks=[make_task("M", duration=0, start=MON, milestone=True)])
    cascade(p)
    assert p.find("M").end == MON


# ---------- linear chain ----------

def test_linear_chain_cascades_dates():
    p = linear_chain()
    cascade(p)
    t1, t2, t3 = p.find("1"), p.find("2"), p.find("3")
    # 1: MON .. WED (3 working days)
    assert t1.start == MON and t1.end == WED
    # 2: starts day after 1 ends → THU; dur=2 → ends FRI
    assert t2.start == THU and t2.end == FRI
    # 3: starts day after 2 ends → NEXT_MON; dur=1 → ends NEXT_MON
    assert t3.start == NEXT_MON and t3.end == NEXT_MON


# ---------- diamond ----------

def test_diamond_takes_max_of_predecessor_constraints():
    p = diamond()
    cascade(p)
    t1, t2, t3, t4 = (p.find(x) for x in ("1", "2", "3", "4"))
    # 1: MON .. TUE
    assert t1.start == MON and t1.end == TUE
    # 2: WED .. THU (2 working days starting day after t1 ends)
    assert t2.start == WED and t2.end == THU
    # 3: WED .. NEXT_TUE (5 working days starting WED)
    assert t3.start == WED and t3.end == NEXT_TUE
    # 4: starts day after the LATER of t2/t3 ends → day after NEXT_TUE = NEXT_WED
    assert t4.start == NEXT_WED


# ---------- relation types ----------

def test_ss_relation_starts_with_predecessor():
    p = Program(
        name="ss",
        tasks=[
            make_task("1", duration=5, start=MON),
            make_task("2", duration=2, predecessors="1SS"),
        ],
    )
    cascade(p)
    assert p.find("2").start == MON  # SS+0 → starts same day as predecessor


def test_ss_relation_with_lag():
    p = Program(
        name="ss-lag",
        tasks=[
            make_task("1", duration=5, start=MON),
            make_task("2", duration=2, predecessors="1SS+2"),
        ],
    )
    cascade(p)
    assert p.find("2").start == WED  # MON + 2 working days


def test_ff_relation_ends_with_predecessor():
    p = Program(
        name="ff",
        tasks=[
            make_task("1", duration=5, start=MON),  # ends FRI
            make_task("2", duration=2, predecessors="1FF"),
        ],
    )
    cascade(p)
    t2 = p.find("2")
    assert t2.end == FRI
    assert t2.start == THU  # FRI - (2-1) working days = THU


def test_sf_relation_ends_when_predecessor_starts():
    p = Program(
        name="sf",
        tasks=[
            make_task("1", duration=3, start=NEXT_MON),  # starts NEXT_MON
            make_task("2", duration=2, predecessors="1SF"),
        ],
    )
    cascade(p)
    t2 = p.find("2")
    assert t2.end == NEXT_MON
    assert t2.start == FRI  # NEXT_MON - (2-1) = FRI


def test_fs_with_positive_lag_inserts_gap():
    p = Program(
        name="fs-lag",
        tasks=[
            make_task("1", duration=2, start=MON),  # MON .. TUE
            make_task("2", duration=2, predecessors="1FS+2"),
        ],
    )
    cascade(p)
    # Standard FS+0 would be WED. FS+2 adds 2 more working days → FRI.
    assert p.find("2").start == FRI


def test_fs_with_negative_lag_overlaps():
    p = Program(
        name="fs-neg",
        tasks=[
            make_task("1", duration=3, start=MON),  # MON .. WED
            make_task("2", duration=2, predecessors="1FS-1"),
        ],
    )
    cascade(p)
    # Standard FS+0 starts THU; -1 backs up to WED.
    assert p.find("2").start == WED


# ---------- holiday handling ----------

def test_cascade_skips_holidays():
    p = Program(
        name="holiday",
        tasks=[
            make_task("1", duration=2, start=MON),  # MON .. TUE (working days)
            make_task("2", duration=1, predecessors="1FS"),
        ],
        holidays={WED},  # Wed is a holiday → succ starts THU instead of WED
    )
    cascade(p)
    assert p.find("2").start == THU


# ---------- milestone in cascade ----------

def test_milestone_with_ff_dependency():
    p = milestone_chain()
    cascade(p)
    t1, m = p.find("1"), p.find("M")
    assert t1.end == FRI  # 5-day task starting MON ends FRI
    assert m.start == FRI and m.end == FRI  # milestone matches predecessor end


# ---------- error cases ----------

def test_cycle_detection():
    p = cycle()
    with pytest.raises(CycleError) as exc:
        cascade(p)
    msg = str(exc.value)
    assert "1" in msg and "2" in msg


def test_unanchored_task_raises():
    with pytest.raises(UnanchoredError) as exc:
        cascade(unanchored())
    assert "1" in str(exc.value)


def test_missing_predecessor_raises():
    p = Program(
        name="missing",
        tasks=[make_task("1", duration=1, predecessors="9.9FS", start=MON)],
    )
    with pytest.raises(MissingPredecessorError) as exc:
        cascade(p)
    assert "9.9" in str(exc.value)


# ---------- idempotence ----------

def test_cascade_is_idempotent():
    p = linear_chain()
    cascade(p)
    snapshot = [(t.id, t.start, t.end) for t in p.tasks]
    cascade(p)
    after = [(t.id, t.start, t.end) for t in p.tasks]
    assert snapshot == after


def test_cascade_overrides_manual_start_on_dependent_tasks():
    # A successor's manual `start` is irrelevant once it has predecessors.
    p = Program(
        name="override",
        tasks=[
            make_task("1", duration=2, start=MON),
            make_task("2", duration=1, predecessors="1FS", start=date(2030, 1, 1)),
        ],
    )
    cascade(p)
    assert p.find("2").start == WED  # NOT 2030-01-01


# ---------- multiple holidays + diamond stress ----------

def test_diamond_with_holidays():
    p = diamond()
    p.holidays = {WED}  # WED holiday should ripple through everything
    cascade(p)
    t1 = p.find("1")  # MON, TUE — unaffected (WED comes after)
    t2 = p.find("2")  # 2 days starting day after t1 ends. THU, FRI (skipping WED).
    t3 = p.find("3")  # 5 days starting day after t1 ends.
    t4 = p.find("4")
    assert t1.end == TUE
    assert t2.start == THU and t2.end == FRI
    # t3: starts THU, 5 working days = THU, FRI, NEXT_MON, NEXT_TUE, NEXT_WED
    assert t3.start == THU and t3.end == NEXT_WED
    # t4 starts day after later of t2/t3 = day after NEXT_WED = NEXT_THU
    assert t4.start == NEXT_THU
