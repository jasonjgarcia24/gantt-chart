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
      0a. status == Cancelled           → Cancelled (terminal; preserved)
      0b. status == Planned AND pct==0  → Planned (preserve user intent)
      1. %complete == 100               → Done
      2. %complete == 0                 → Not Started
      3. start has passed AND any predecessor < 100% complete → Blocked
      4. otherwise                      → In Progress

    Cancelled is a terminal state for tasks that came in from Linear as
    canceled-type (or were manually marked Cancelled in the workbook).
    Without the rule-0a carve-out, auto_status would rewrite Cancelled
    to Not Started whenever %complete is 0, which on the next Linear
    sync would push that "Not Started" back to Linear and un-archive
    the issue.

    Planned is a user-set flag meaning "queued / next-up, not just
    backlog." Without the rule-0b carve-out, rule 2 would silently
    overwrite Planned with Not Started whenever pct=0 (which is most
    of Planned's lifetime — once work starts, pct goes above 0 and the
    cascade naturally moves to In Progress).

    "At Risk" is not auto-derived. If `task.status` was manually set to
    At Risk and no rule above triggers a different status, this function
    returns "In Progress" — overwriting the manual flag. Same carve-out
    pattern would apply if/when At Risk needs to be preserved.
    """
    if task.status == Status.CANCELLED:
        return Status.CANCELLED
    if task.status == Status.PLANNED and task.percent_complete <= 0:
        return Status.PLANNED

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
