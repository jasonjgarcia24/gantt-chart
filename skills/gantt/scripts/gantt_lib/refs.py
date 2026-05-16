"""Predecessor reference utilities — used by `task delete` to refuse safely."""
from __future__ import annotations

from .dsl import parse_predecessors
from .model import Task


def referencing_tasks(tasks: list[Task], target_id: str) -> list[Task]:
    """Return tasks whose `predecessors` field references `target_id`.

    Used by `gantt task delete` to refuse when removing a task would leave
    dangling predecessor references on others.
    """
    out: list[Task] = []
    for t in tasks:
        if not t.predecessors:
            continue
        try:
            preds = parse_predecessors(t.predecessors)
        except Exception:
            continue  # malformed predecessor strings shouldn't block delete checks
        if any(p.id == target_id for p in preds):
            out.append(t)
    return out
