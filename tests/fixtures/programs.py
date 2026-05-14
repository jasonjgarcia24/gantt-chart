"""Reusable Program/Task factories for cascade, critical-path, recalc tests.

Keep these tiny — each fixture should illustrate ONE concept (linear chain,
diamond, etc.) so test failures point at the structure, not the data.
"""
from __future__ import annotations

from datetime import date

from gantt_lib.model import Program, Task

MON = date(2026, 5, 11)


def make_task(
    id: str,
    *,
    duration: int = 1,
    name: str = "",
    start=None,
    predecessors: str = "",
    milestone: bool = False,
) -> Task:
    return Task(
        id=id,
        level=1,
        name=name or f"Task {id}",
        duration=duration,
        start=start,
        predecessors=predecessors,
        milestone=milestone,
    )


def linear_chain() -> Program:
    """1 (manual start, dur=3) → 2 (FS, dur=2) → 3 (FS, dur=1)."""
    return Program(
        name="linear",
        tasks=[
            make_task("1", duration=3, start=MON),
            make_task("2", duration=2, predecessors="1FS"),
            make_task("3", duration=1, predecessors="2FS"),
        ],
    )


def diamond() -> Program:
    """1 → {2 (short), 3 (long)} → 4. Task 4 starts after the LATER of 2/3."""
    return Program(
        name="diamond",
        tasks=[
            make_task("1", duration=2, start=MON),
            make_task("2", duration=2, predecessors="1FS"),
            make_task("3", duration=5, predecessors="1FS"),
            make_task("4", duration=1, predecessors="2FS, 3FS"),
        ],
    )


def cycle() -> Program:
    """Two-node cycle: 1 depends on 2, 2 depends on 1."""
    return Program(
        name="cycle",
        tasks=[
            make_task("1", duration=1, predecessors="2FS"),
            make_task("2", duration=1, predecessors="1FS"),
        ],
    )


def unanchored() -> Program:
    """A task with no predecessors and no manual start."""
    return Program(
        name="unanchored",
        tasks=[make_task("1", duration=3)],
    )


def milestone_chain() -> Program:
    """1 (dur=5) → milestone M (dur=0, FF on 1) — end of M should match end of 1."""
    return Program(
        name="ms",
        tasks=[
            make_task("1", duration=5, start=MON),
            make_task("M", duration=0, predecessors="1FF", milestone=True),
        ],
    )
