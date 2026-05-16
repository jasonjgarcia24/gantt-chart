"""Critical-path computation via CPM forward/backward pass.

Forward pass — earliest start (ES) and earliest end (EE) — is what `cascade()`
already does and stores on each `Task`. Backward pass computes latest start
(LS) and latest end (LE); slack = LS − ES (in working days). Tasks with zero
slack form the critical path.

Pure-Python; no Sheets dependency.
"""
from __future__ import annotations

from datetime import date

from .cascade import _topo_order
from .dates import add_working_days, working_days_between
from .dsl import Predecessor, parse_predecessors
from .model import Program, Task


def _build_successors(program: Program) -> dict[str, list[tuple[Task, str, int]]]:
    """For each task id, the list of (successor_task, relation, lag) tuples."""
    out: dict[str, list[tuple[Task, str, int]]] = {t.id: [] for t in program.tasks}
    for t in program.tasks:
        for p in parse_predecessors(t.predecessors):
            if p.id in out:
                out[p.id].append((t, p.rel, p.lag))
    return out


def _backward_pass(
    program: Program,
    successors: dict[str, list[tuple[Task, str, int]]],
    project_end: date,
) -> tuple[dict[str, date], dict[str, date]]:
    """Compute LE and LS for every task, returning (le_by_id, ls_by_id).

    Symmetric to the cascade forward pass: each successor relation is inverted
    so a constraint flows backwards from successor.LS/LE to predecessor.LE/LS.
    """
    le: dict[str, date] = {}
    ls: dict[str, date] = {}
    holidays = program.holidays
    ordered = _topo_order(program)

    for t in reversed(ordered):
        succs = successors[t.id]
        if not succs:
            # Sink: latest end is the project end.
            le[t.id] = project_end
        else:
            constraints: list[date] = []
            for succ, rel, lag in succs:
                if rel == "FS":
                    # succ.ES = pred.EE + 1 + lag → pred.LE = succ.LS − 1 − lag
                    c = add_working_days(ls[succ.id], -(1 + lag), holidays)
                elif rel == "SS":
                    # succ.ES = pred.ES + lag → pred.LS = succ.LS − lag → derive LE
                    c_ls = add_working_days(ls[succ.id], -lag, holidays)
                    c = c_ls if t.duration <= 0 else add_working_days(
                        c_ls, t.duration - 1, holidays,
                    )
                elif rel == "FF":
                    # succ.EE = pred.EE + lag → pred.LE = succ.LE − lag
                    c = add_working_days(le[succ.id], -lag, holidays)
                elif rel == "SF":
                    # succ.EE = pred.ES + lag → pred.LS = succ.LE − lag → derive LE
                    c_ls = add_working_days(le[succ.id], -lag, holidays)
                    c = c_ls if t.duration <= 0 else add_working_days(
                        c_ls, t.duration - 1, holidays,
                    )
                else:
                    continue
                constraints.append(c)
            le[t.id] = min(constraints)
        # LS from LE.
        if t.duration <= 0:
            ls[t.id] = le[t.id]
        else:
            ls[t.id] = add_working_days(le[t.id], -(t.duration - 1), holidays)
    return le, ls


def compute_slack(program: Program) -> dict[str, int]:
    """Return {task_id: slack_in_working_days}.

    Assumes `cascade(program)` has already populated start/end on every task.
    For tasks without dates (shouldn't happen post-cascade), they're omitted.
    """
    if not program.tasks:
        return {}
    project_end = max((t.end for t in program.tasks if t.end is not None), default=None)
    if project_end is None:
        return {}

    successors = _build_successors(program)
    _le, ls = _backward_pass(program, successors, project_end)

    slack: dict[str, int] = {}
    for t in program.tasks:
        if t.start is None or t.id not in ls:
            continue
        slack[t.id] = working_days_between(t.start, ls[t.id], program.holidays)
    return slack


def critical_path(program: Program) -> list[str]:
    """Return ordered list of task ids on the critical path (slack == 0).

    Order is topological (predecessors before dependents). When multiple
    branches share zero slack, all of them are returned — the caller decides
    how to render. For v0.5 this is a flat list, not a single linear path.
    """
    slack = compute_slack(program)
    if not slack:
        return []
    critical_ids = {tid for tid, s in slack.items() if s == 0}
    ordered = _topo_order(program)
    return [t.id for t in ordered if t.id in critical_ids]
