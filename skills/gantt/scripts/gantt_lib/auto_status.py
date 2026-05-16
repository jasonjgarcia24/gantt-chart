"""Auto-derive task status from %complete + start date + predecessor completion.

Called from `gantt recalc` after the date cascade so the Status column (J)
reflects the truth of each task. Pure-Python — no Sheets dependency.
"""
from __future__ import annotations

from datetime import date

from .dsl import parse_predecessors
from .model import Status, Task


def compute_status(task: Task, tasks_by_id: dict[str, Task], today: date) -> str:
    """Return the auto-derived Status for `task`.

    Rules (in order; first match wins):
      1. %complete == 100               → Done
      2. %complete == 0                 → Not Started
      3. start has passed AND any predecessor < 100% complete → Blocked
      4. otherwise                      → In Progress

    "At Risk" is not auto-derived. If `task.status` was manually set to At Risk
    and no rule above triggers a different status, this function will return
    "In Progress" — overwriting the manual flag. Add a carve-out if that's
    the wrong default.
    """
    pct = task.percent_complete
    if pct >= 100:
        return Status.DONE
    if pct <= 0:
        return Status.NOT_STARTED

    # In progress range (0 < pct < 100). Check for blocker.
    if task.start is not None and task.start <= today:
        for p in parse_predecessors(task.predecessors):
            pred = tasks_by_id.get(p.id)
            if pred is not None and pred.percent_complete < 100:
                return Status.BLOCKED

    return Status.IN_PROGRESS
