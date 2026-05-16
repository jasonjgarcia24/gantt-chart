"""Pure-logic content selection for deck slides — no Sheets I/O.

Each function takes program data + (where relevant) a date or baseline rows,
and returns structured rows ready for template rendering. Selection rules
are documented per slide in docs/specs/deck-generation.md.

Tactical functions operate on a single Program with cascade'd start/end.
Strategic functions take a list of Programs and roll up across the portfolio.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Optional

from ..baseline import (
    BaselineRow,
    active_baselines,
    cp_duration_days,
    reconstruct_baseline_program,
    slip_days,
)
from ..critical_path import critical_path
from ..dsl import parse_predecessors
from ..model import Program, Status, Task, wbs_sort_key


# ============================================================================
# Tactical (per-program) selectors
# ============================================================================

def this_week_and_next(program: Program, today: date) -> list[Task]:
    """T1 — tasks visible in the next-14-day window that aren't Done.

    Filter: start ≤ today + 14d AND end ≥ today AND status != Done.
    Sort: end asc, then WBS asc.
    """
    horizon = today + timedelta(days=14)
    out = [
        t for t in program.tasks
        if t.start is not None and t.end is not None
        and t.start <= horizon and t.end >= today
        and t.status != Status.DONE
    ]
    out.sort(key=lambda t: (t.end, wbs_sort_key(t.id)))
    return out


def blockers(program: Program) -> list[tuple[Task, list[str]]]:
    """T2 — Blocked + At Risk tasks paired with their open predecessor IDs.

    For each at-risk/blocked task, computes which predecessor WBS IDs aren't
    yet Done — those are the "blocked by" list. Done predecessors are
    filtered out (no longer blocking).

    Sort: Blocked first, then At Risk; WBS asc within each bucket.
    """
    by_id = {t.id: t for t in program.tasks}
    out: list[tuple[Task, list[str]]] = []
    for t in program.tasks:
        if t.status not in (Status.BLOCKED, Status.AT_RISK):
            continue
        preds = parse_predecessors(t.predecessors)
        open_preds = [
            p.id for p in preds
            if p.id in by_id and by_id[p.id].status != Status.DONE
        ]
        out.append((t, open_preds))
    status_order = {Status.BLOCKED: 0, Status.AT_RISK: 1}
    out.sort(key=lambda pair: (
        status_order.get(pair[0].status, 2),
        wbs_sort_key(pair[0].id),
    ))
    return out


def critical_path_due_soon(program: Program, today: date) -> list[Task]:
    """T3 — critical-path tasks ending in the next 14 days.

    Filter: id ∈ critical_path(program) AND end ∈ [today, today + 14d].
    Sort: end asc.
    """
    horizon = today + timedelta(days=14)
    cp_ids = set(critical_path(program))
    if not cp_ids:
        return []
    out = [
        t for t in program.tasks
        if t.id in cp_ids and t.end is not None
        and today <= t.end <= horizon
    ]
    out.sort(key=lambda t: (t.end, wbs_sort_key(t.id)))
    return out


def recently_completed(program: Program, today: date) -> list[Task]:
    """T4 — Done tasks where end ∈ [today - 7d, today].

    `end` is a proxy for completion date (we don't snapshot the actual
    completion timestamp). Sort: end desc.
    """
    window_start = today - timedelta(days=7)
    out = [
        t for t in program.tasks
        if t.status == Status.DONE and t.end is not None
        and window_start <= t.end <= today
    ]
    out.sort(key=lambda t: (t.end, wbs_sort_key(t.id)), reverse=True)
    return out


def gantt_zoom_window(
    program: Program, today: date, days: int = 30,
) -> list[Task]:
    """T5 — task subset visible in the gantt-zoom chart window.

    Filter: start ≤ today + days AND end ≥ today.
    Sort: start asc, then WBS asc (stable y-axis ordering).
    """
    horizon = today + timedelta(days=days)
    out = [
        t for t in program.tasks
        if t.start is not None and t.end is not None
        and t.start <= horizon and t.end >= today
    ]
    out.sort(key=lambda t: (t.start, wbs_sort_key(t.id)))
    return out


# ============================================================================
# Strategic (portfolio) selectors
# ============================================================================

def _worst_status(tasks: list[Task]) -> str:
    """Roll task statuses up to a single program-level worst-of value.

    Order of severity: Blocked > At Risk > In Progress > Not Started > Done.
    All-Done programs report "Done"; otherwise the highest-severity bucket
    wins.
    """
    statuses = {t.status or Status.NOT_STARTED for t in tasks}
    if Status.BLOCKED in statuses:
        return Status.BLOCKED
    if Status.AT_RISK in statuses:
        return Status.AT_RISK
    if Status.IN_PROGRESS in statuses:
        return Status.IN_PROGRESS
    if statuses and statuses.issubset({Status.DONE}):
        return Status.DONE
    return Status.NOT_STARTED


def _percent_complete_weighted(tasks: list[Task]) -> int:
    """Duration-weighted % complete across a task set. 0 when total duration is 0."""
    total_dur = sum(t.duration for t in tasks)
    if total_dur == 0:
        return 0
    weighted = sum(t.percent_complete * t.duration for t in tasks)
    return weighted // total_dur


@dataclass(frozen=True)
class PortfolioRow:
    program: str
    status: str
    percent_complete: int
    current_cp_days: Optional[int]
    last_baseline_date: Optional[date]
    last_baseline_actor: str


def portfolio_status(
    programs: list[Program],
    baseline_rows: Iterable[BaselineRow],
) -> list[PortfolioRow]:
    """S1 — one row per program with rolled-up status, %complete, CP, baseline metadata.

    Sort: program name asc.
    """
    rows = list(baseline_rows)
    out: list[PortfolioRow] = []
    for p in programs:
        program_baselines = [r for r in rows if r.program == p.name]
        last = (
            max(program_baselines, key=lambda r: r.snapshot_date)
            if program_baselines else None
        )
        out.append(PortfolioRow(
            program=p.name,
            status=_worst_status(p.tasks),
            percent_complete=_percent_complete_weighted(p.tasks),
            current_cp_days=cp_duration_days(p),
            last_baseline_date=last.snapshot_date if last else None,
            last_baseline_actor=last.snapshot_actor if last else "",
        ))
    out.sort(key=lambda r: r.program)
    return out


@dataclass(frozen=True)
class MilestoneSlipRow:
    program: str
    wbs: str
    name: str
    baseline_end: Optional[date]
    current_end: Optional[date]
    slip: Optional[int]


def milestone_slip_summary(
    programs: list[Program],
    baseline_rows: Iterable[BaselineRow],
) -> list[MilestoneSlipRow]:
    """S2 — milestones across all programs with slip vs active baseline.

    Sort: current_end asc; tasks with no current_end go to the end.
    """
    rows = list(baseline_rows)
    out: list[MilestoneSlipRow] = []
    for p in programs:
        actives = active_baselines(rows, p.name)
        for t in p.tasks:
            if not t.milestone:
                continue
            b = actives.get(t.id)
            out.append(MilestoneSlipRow(
                program=p.name,
                wbs=t.id,
                name=t.name,
                baseline_end=b.baseline_end if b else None,
                current_end=t.end,
                slip=slip_days(t.end, b.baseline_end) if b else None,
            ))
    out.sort(key=lambda r: (r.current_end is None, r.current_end or date.max))
    return out


@dataclass(frozen=True)
class CPRow:
    program: str
    baseline_cp_days: Optional[int]
    current_cp_days: Optional[int]
    delta: Optional[int]
    num_tasks_on_cp: int


def critical_path_by_program(
    programs: list[Program],
    baseline_rows: Iterable[BaselineRow],
) -> list[CPRow]:
    """S3 — one row per program with current CP length + delta from baseline.

    Sort: delta desc (worst slip first); programs with no baseline last.
    """
    rows = list(baseline_rows)
    out: list[CPRow] = []
    for p in programs:
        cp = critical_path(p)
        current = cp_duration_days(p)
        actives = active_baselines(rows, p.name)
        baseline: Optional[int] = None
        if actives:
            baseline_program = reconstruct_baseline_program(
                p.name, actives.values(), holidays=p.holidays,
            )
            try:
                baseline = cp_duration_days(baseline_program)
            except Exception:
                baseline = None
        delta = (
            current - baseline
            if (current is not None and baseline is not None)
            else None
        )
        out.append(CPRow(
            program=p.name,
            baseline_cp_days=baseline,
            current_cp_days=current,
            delta=delta,
            num_tasks_on_cp=len(cp),
        ))
    out.sort(key=lambda r: (r.delta is None, -(r.delta or 0)))
    return out


@dataclass(frozen=True)
class RiskRow:
    program: str
    wbs: str
    name: str
    status: str
    slip_days: Optional[int]
    on_critical_path: bool
    impact_score: float


def top_risks(
    programs: list[Program],
    baseline_rows: Iterable[BaselineRow],
    n: int = 5,
) -> list[RiskRow]:
    """S4 — top N risk tasks across the portfolio ranked by impact heuristic.

    Filter: status ∈ {Blocked, At Risk}.
    Score: end_slip_days × (2 if on critical path else 1). Tasks with no slip
    data score 0 and sort to the bottom.
    """
    rows = list(baseline_rows)
    candidates: list[RiskRow] = []
    for p in programs:
        cp_ids = set(critical_path(p))
        actives = active_baselines(rows, p.name)
        for t in p.tasks:
            if t.status not in (Status.BLOCKED, Status.AT_RISK):
                continue
            b = actives.get(t.id)
            slip = slip_days(t.end, b.baseline_end) if b else None
            on_cp = t.id in cp_ids
            score = (slip or 0) * (2 if on_cp else 1)
            candidates.append(RiskRow(
                program=p.name,
                wbs=t.id,
                name=t.name,
                status=t.status,
                slip_days=slip,
                on_critical_path=on_cp,
                impact_score=float(score),
            ))
    candidates.sort(key=lambda r: r.impact_score, reverse=True)
    return candidates[:n]


@dataclass(frozen=True)
class ForwardMilestone:
    program: str
    wbs: str
    name: str
    end: date
    days_from_today: int


def forward_look_30d(
    programs: list[Program], today: date, days: int = 30,
) -> list[ForwardMilestone]:
    """S5 — milestones across all programs due in the next N days.

    Sort: end asc.
    """
    horizon = today + timedelta(days=days)
    out: list[ForwardMilestone] = []
    for p in programs:
        for t in p.tasks:
            if not t.milestone or t.end is None:
                continue
            if today <= t.end <= horizon:
                out.append(ForwardMilestone(
                    program=p.name,
                    wbs=t.id,
                    name=t.name,
                    end=t.end,
                    days_from_today=(t.end - today).days,
                ))
    out.sort(key=lambda r: r.end)
    return out
