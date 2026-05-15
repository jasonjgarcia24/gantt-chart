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

from .critical_path import critical_path
from .dsl import format_predecessors, parse_predecessors
from .model import Program, Status, Task

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


@dataclass(frozen=True)
class Summary:
    """Program-level baseline rollup, structured for direct rendering by `show`.

    Slip stats are computed only over tasks that have BOTH a current end date AND
    an active baseline end date. Tasks missing either are counted in
    `tasks_baselined` / `tasks_not_baselined` but excluded from the
    slipping/ahead/on_baseline triplet — the three add up to ≤ tasks_baselined,
    not necessarily equal.
    """
    program: str

    # Last-snapshot metadata (None if program has no baseline rows at all).
    last_baseline_date: Optional[date]
    last_baseline_actor: str
    days_since_baseline: Optional[int]

    # Status counts (over the current task set).
    total_tasks: int
    tasks_done: int
    tasks_in_progress: int
    tasks_not_started: int
    tasks_blocked: int
    tasks_at_risk: int

    # Baseline coverage (current tasks with vs. without an active baseline).
    tasks_baselined: int
    tasks_not_baselined: int

    # End-slip stats, all in calendar days. None when no comparable tasks.
    max_end_slip: Optional[int]
    mean_end_slip: Optional[float]
    slipping_count: int
    ahead_count: int
    on_baseline_count: int

    # Critical-path duration (calendar days, inclusive). None when no CP exists.
    baseline_cp_days: Optional[int]
    current_cp_days: Optional[int]
    cp_delta: Optional[int]


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


def cp_duration_days(program: Program) -> Optional[int]:
    """Calendar-day length of the critical path: max(end) − min(start) + 1 over CP tasks.

    +1 makes the duration inclusive of both endpoints (a 1-day task starting and
    ending on the same date returns 1, not 0). Returns None if the program has
    no critical path (empty, or every task missing dates).
    """
    cp_ids = critical_path(program)
    if not cp_ids:
        return None
    cp_id_set = set(cp_ids)
    cp_tasks = [t for t in program.tasks if t.id in cp_id_set]
    starts = [t.start for t in cp_tasks if t.start is not None]
    ends = [t.end for t in cp_tasks if t.end is not None]
    if not starts or not ends:
        return None
    return (max(ends) - min(starts)).days + 1


def reconstruct_baseline_program(
    program_name: str, baseline_rows: Iterable[BaselineRow]
) -> Program:
    """Build a synthetic Program from a set of active baseline rows.

    Each BaselineRow becomes a Task with the snapshotted start/end/duration and
    a *filtered* predecessor string — references to WBS ids not present in
    `baseline_rows` are dropped. This handles the case where a predecessor was
    deleted between snapshots: rather than raising MissingPredecessorError when
    we later run critical_path() on the reconstructed program, we silently drop
    the dead reference. The baseline plan is a historical artifact; we read it
    as it was, not as it might have been.

    Holidays are reconstructed as the empty set. The current program's holidays
    are used for the *current* CP computation; this means a baseline CP that
    spanned a holiday added since baseline will read very slightly longer than
    it "really" was. Acceptable for Phase 1; documented limitation.
    """
    rows = list(baseline_rows)
    valid_wbs = {r.wbs for r in rows}
    tasks = []
    for r in rows:
        preds_in = parse_predecessors(r.baseline_predecessors)
        preds_kept = [p for p in preds_in if p.id in valid_wbs]
        tasks.append(Task(
            id=r.wbs,
            level=1,
            name=r.task_name,
            start=r.baseline_start,
            end=r.baseline_end,
            duration=r.baseline_duration,
            predecessors=format_predecessors(preds_kept),
        ))
    return Program(name=program_name, tasks=tasks, holidays=set())


def _count_status(tasks: list[Task], status_value: str) -> int:
    """Count tasks whose status field matches. Empty status counts as Not Started."""
    if status_value == Status.NOT_STARTED:
        return sum(1 for t in tasks if t.status in ("", Status.NOT_STARTED))
    return sum(1 for t in tasks if t.status == status_value)


def compute_summary(
    program_name: str,
    current_program: Program,
    all_baseline_rows: Iterable[BaselineRow],
    today: date,
) -> Summary:
    """Build the Summary for one program's baseline-vs-current state.

    `current_program` carries the current task set with cascade-populated
    start/end values. `all_baseline_rows` is the full unfiltered _Baselines tab
    contents; this function filters by program internally so callers can pass
    the same list when iterating over multiple programs.
    """
    rows_list = list(all_baseline_rows)
    program_rows = [r for r in rows_list if r.program == program_name]
    actives = active_baselines(rows_list, program_name)

    # Last-snapshot metadata
    if program_rows:
        most_recent = max(program_rows, key=lambda r: r.snapshot_date)
        last_baseline_date: Optional[date] = most_recent.snapshot_date
        last_baseline_actor = most_recent.snapshot_actor
        days_since: Optional[int] = (today - last_baseline_date).days
    else:
        last_baseline_date = None
        last_baseline_actor = ""
        days_since = None

    tasks = current_program.tasks
    total_tasks = len(tasks)
    tasks_done = _count_status(tasks, Status.DONE)
    tasks_in_progress = _count_status(tasks, Status.IN_PROGRESS)
    tasks_not_started = _count_status(tasks, Status.NOT_STARTED)
    tasks_blocked = _count_status(tasks, Status.BLOCKED)
    tasks_at_risk = _count_status(tasks, Status.AT_RISK)

    current_wbs = {t.id for t in tasks}
    tasks_baselined = sum(1 for wbs in current_wbs if wbs in actives)
    tasks_not_baselined = total_tasks - tasks_baselined

    # End-slip stats — only on tasks where both sides have a date.
    slips: list[int] = []
    for t in tasks:
        b = actives.get(t.id)
        if b is None:
            continue
        s = slip_days(t.end, b.baseline_end)
        if s is not None:
            slips.append(s)

    if slips:
        max_end_slip: Optional[int] = max(slips)
        mean_end_slip: Optional[float] = sum(slips) / len(slips)
        slipping_count = sum(1 for s in slips if s > 0)
        ahead_count = sum(1 for s in slips if s < 0)
        on_baseline_count = sum(1 for s in slips if s == 0)
    else:
        max_end_slip = None
        mean_end_slip = None
        slipping_count = 0
        ahead_count = 0
        on_baseline_count = 0

    # Critical-path delta
    current_cp = _safe_cp(current_program)
    if actives:
        baseline_program = reconstruct_baseline_program(program_name, actives.values())
        baseline_cp = _safe_cp(baseline_program)
    else:
        baseline_cp = None
    cp_delta: Optional[int]
    if current_cp is not None and baseline_cp is not None:
        cp_delta = current_cp - baseline_cp
    else:
        cp_delta = None

    return Summary(
        program=program_name,
        last_baseline_date=last_baseline_date,
        last_baseline_actor=last_baseline_actor,
        days_since_baseline=days_since,
        total_tasks=total_tasks,
        tasks_done=tasks_done,
        tasks_in_progress=tasks_in_progress,
        tasks_not_started=tasks_not_started,
        tasks_blocked=tasks_blocked,
        tasks_at_risk=tasks_at_risk,
        tasks_baselined=tasks_baselined,
        tasks_not_baselined=tasks_not_baselined,
        max_end_slip=max_end_slip,
        mean_end_slip=mean_end_slip,
        slipping_count=slipping_count,
        ahead_count=ahead_count,
        on_baseline_count=on_baseline_count,
        baseline_cp_days=baseline_cp,
        current_cp_days=current_cp,
        cp_delta=cp_delta,
    )


def _safe_cp(program: Program) -> Optional[int]:
    """cp_duration_days that swallows graph-shape errors from hand-edited data.

    A baseline reconstructed from corrupted predecessor strings could trip
    CycleError or MissingPredecessorError; treat those as "no comparable CP"
    rather than crashing the whole `show` command.
    """
    try:
        return cp_duration_days(program)
    except Exception:
        return None
