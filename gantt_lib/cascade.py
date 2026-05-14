"""Topological sort + dependency cascade engine.

Given a Program, recompute every task's start/end from its predecessors
according to the FS / SS / FF / SF semantics with optional working-day lag.

Semantics (per MS Project conventions, in working days):

    FS+lag : succ.start = pred.end + (1 + lag) working days
    SS+lag : succ.start = pred.start + lag working days
    FF+lag : succ.end   = pred.end + lag working days
    SF+lag : succ.end   = pred.start + lag working days

For FF/SF (which derive end), succ.start is back-computed:
    succ.start = succ.end - (succ.duration - 1) working days

Multiple predecessors → succ.start is the max of all derived constraints.
A task with no predecessors must have a manual `start` set; otherwise
UnanchoredError is raised. Cycles in the dependency graph raise CycleError.
"""
from __future__ import annotations

from datetime import date

from .dates import add_working_days
from .dsl import Predecessor, parse_predecessors
from .model import Program, Task


class CycleError(ValueError):
    """Raised when the predecessor graph contains a cycle."""


class UnanchoredError(ValueError):
    """Raised when a task has no predecessors and no manual start."""


class MissingPredecessorError(ValueError):
    """Raised when a predecessor id refers to a task that doesn't exist."""


def _topo_order(program: Program) -> list[Task]:
    """Return tasks in dependency order (predecessors before dependents).

    Raises CycleError naming the offending IDs, or MissingPredecessorError if
    a predecessor id refers to a task that doesn't exist in the program.
    """
    tasks_by_id = {t.id: t for t in program.tasks}
    deps_by_id: dict[str, list[Predecessor]] = {
        t.id: parse_predecessors(t.predecessors) for t in program.tasks
    }

    # Validate predecessor references up front.
    for tid, preds in deps_by_id.items():
        for p in preds:
            if p.id not in tasks_by_id:
                raise MissingPredecessorError(
                    f"task {tid!r} references unknown predecessor {p.id!r}"
                )

    state: dict[str, str] = {}  # id → "visiting" | "done"
    order: list[str] = []

    def visit(tid: str, path: list[str]) -> None:
        if state.get(tid) == "done":
            return
        if state.get(tid) == "visiting":
            cycle_start = path.index(tid)
            cycle = path[cycle_start:] + [tid]
            raise CycleError(f"cycle: {' -> '.join(cycle)}")
        state[tid] = "visiting"
        for pred in deps_by_id[tid]:
            visit(pred.id, path + [tid])
        state[tid] = "done"
        order.append(tid)

    for t in program.tasks:
        visit(t.id, [])

    return [tasks_by_id[tid] for tid in order]


def _start_constraint_from_predecessor(
    pred: Predecessor,
    pred_task: Task,
    succ: Task,
    holidays: set[date],
) -> date:
    """Translate one predecessor relation into a start-date constraint on succ."""
    if pred_task.start is None or pred_task.end is None:
        # Should not happen post-topo: predecessors are visited first.
        raise RuntimeError(
            f"predecessor {pred.id} dates not yet computed when needed by {succ.id}"
        )

    if pred.rel == "FS":
        # Start the working day after pred ends, plus lag.
        return add_working_days(pred_task.end, 1 + pred.lag, holidays)
    if pred.rel == "SS":
        return add_working_days(pred_task.start, pred.lag, holidays)

    # FF and SF derive an END constraint; back-compute START from duration.
    if pred.rel == "FF":
        end = add_working_days(pred_task.end, pred.lag, holidays)
    elif pred.rel == "SF":
        end = add_working_days(pred_task.start, pred.lag, holidays)
    else:
        raise RuntimeError(f"unknown relation {pred.rel}")

    if succ.duration <= 0:
        return end  # milestone: start == end
    return add_working_days(end, -(succ.duration - 1), holidays)


def cascade(program: Program) -> None:
    """Recompute start/end for every task in `program`, in place.

    Idempotent: running twice on the same program yields the same dates.
    """
    ordered = _topo_order(program)
    tasks_by_id = {t.id: t for t in program.tasks}
    holidays = program.holidays

    for task in ordered:
        preds = parse_predecessors(task.predecessors)

        if not preds:
            if task.start is None:
                raise UnanchoredError(
                    f"task {task.id!r} has no predecessors and no manual start"
                )
            # task.start stays as-is (manual anchor).
        else:
            constraints = [
                _start_constraint_from_predecessor(p, tasks_by_id[p.id], task, holidays)
                for p in preds
            ]
            task.start = max(constraints)

        if task.duration <= 0:
            task.end = task.start
        else:
            task.end = add_working_days(task.start, task.duration - 1, holidays)
