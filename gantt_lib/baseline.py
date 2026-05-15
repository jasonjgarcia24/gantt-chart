"""Baseline tracking — pure logic for snapshots, slip math, and active-baseline lookup.

A baseline is a frozen snapshot of (start, end, duration, predecessors) for every
task in a program at a point in time. Snapshots are append-only in the workbook's
`_Baselines` tab; the *active baseline* for any (program, wbs) pair is the row
with the most recent snapshot_date.

This module owns the data shapes and pure computation. All Sheets I/O lives in
the gantt script's `_read_baselines_tab` / `_append_baseline_rows` helpers,
which parse rows into BaselineRow instances and write them back via the
BASELINE_HEADERS column order.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

BASELINE_TAB = "_Baselines"

BASELINE_HEADERS = [
    "program", "wbs", "task_name", "snapshot_date",
    "baseline_start", "baseline_end", "baseline_duration",
    "baseline_predecessors", "snapshot_label", "snapshot_actor",
]


@dataclass(frozen=True)
class BaselineRow:
    program: str
    wbs: str
    task_name: str
    snapshot_date: date
    baseline_start: Optional[date]
    baseline_end: Optional[date]
    baseline_duration: int
    baseline_predecessors: str = ""
    snapshot_label: str = ""
    snapshot_actor: str = ""


def active_baselines(
    rows: Iterable[BaselineRow], program: str
) -> dict[str, BaselineRow]:
    """Return {wbs: latest BaselineRow} for `program`, latest by snapshot_date.

    Ties on snapshot_date are broken by iteration order — later-encountered rows
    win. This matches the sheet semantic that newer rows append to the bottom of
    `_Baselines`, so a top-down read of the tab yields rows in chronological
    order.
    """
    out: dict[str, BaselineRow] = {}
    for r in rows:
        if r.program != program:
            continue
        prev = out.get(r.wbs)
        if prev is None or r.snapshot_date >= prev.snapshot_date:
            out[r.wbs] = r
    return out


def slip_days(current: Optional[date], baseline: Optional[date]) -> Optional[int]:
    """Calendar-day slip from baseline to current. Positive = later than baseline.

    Returns None if either date is missing — caller should display "—" and
    exclude the task from slip aggregates. Calendar days (not working days) is
    deliberate: slip is a human-facing metric, and a 5-day slip across a weekend
    should read as +5, not +3.
    """
    if current is None or baseline is None:
        return None
    return (current - baseline).days
