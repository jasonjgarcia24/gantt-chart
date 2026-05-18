"""Task / Program data model for the gantt CLI.

Pure-Python — no Sheets dependency. The Sheets adapter layer (in the `gantt`
script) calls Task.from_row / Task.to_row to round-trip a tab's data region.

Column order, A-M, matches the v1-proposal schema:
    A id | B level | C name | D owner | E team | F start | G end | H duration
    I percent_complete | J status | K predecessors | L milestone | M notes
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Optional

NUM_COLUMNS = 13


class Status:
    """Status enum values written to / read from column J.

    Cancelled was added for round-trip with Linear's canceled-type
    states. Without it, a task pulled from Linear in Canceled state
    would get its status silently rewritten by auto_status on next
    recalc — which would then push the wrong state back to Linear on
    sync (un-archiving the issue). See `compute_status` for the
    preservation carve-out.
    """
    NOT_STARTED = "Not Started"
    IN_PROGRESS = "In Progress"
    BLOCKED = "Blocked"
    AT_RISK = "At Risk"
    DONE = "Done"
    CANCELLED = "Cancelled"

    @classmethod
    def all(cls) -> list[str]:
        return [
            cls.NOT_STARTED, cls.IN_PROGRESS, cls.BLOCKED, cls.AT_RISK,
            cls.DONE, cls.CANCELLED,
        ]


def _pad_row(row: list[str], n: int = NUM_COLUMNS) -> list[str]:
    if len(row) >= n:
        return row
    return list(row) + [""] * (n - len(row))


def _parse_date(s: str) -> Optional[date]:
    s = s.strip()
    if not s:
        return None
    return date.fromisoformat(s)


def _format_date(d: Optional[date]) -> str:
    return d.isoformat() if d else ""


def _parse_int(s: str, default: int = 0) -> int:
    s = s.strip()
    if not s:
        return default
    return int(s)


def _parse_bool(s: str) -> bool:
    return s.strip().lower() == "true"


def _format_bool(b: bool) -> str:
    return "TRUE" if b else "FALSE"


@dataclass
class Task:
    id: str = ""
    level: int = 1
    name: str = ""
    owner: str = ""
    team: str = ""
    start: Optional[date] = None
    end: Optional[date] = None
    duration: int = 0
    percent_complete: int = 0
    status: str = ""
    predecessors: str = ""
    milestone: bool = False
    notes: str = ""
    # Non-column metadata used only at write-time by the Linear-pull path
    # to wrap the name column as a Sheets HYPERLINK formula pointing at
    # the Linear issue. Not read from the sheet (gspread returns the
    # rendered display text on read, so from_row never sees this).
    # Excluded from equality and repr so existing tests that build Tasks
    # without it continue to compare correctly.
    linear_url: Optional[str] = field(default=None, compare=False, repr=False)

    @classmethod
    def from_row(cls, row: Iterable[str]) -> "Task":
        r = _pad_row(list(row))
        return cls(
            id=r[0],
            level=_parse_int(r[1], default=1),
            # Strip leading indent characters — spaces and dashes from the
            # sheets layer's level-derived prefix (currently "-- " per level,
            # historically "  "). Tradeoff: a task name intentionally starting
            # with " " or "-" loses its leading characters.
            name=r[2].lstrip(" -"),
            owner=r[3],
            team=r[4],
            start=_parse_date(r[5]),
            end=_parse_date(r[6]),
            duration=_parse_int(r[7]),
            percent_complete=_parse_int(r[8]),
            status=r[9],
            predecessors=r[10],
            milestone=_parse_bool(r[11]),
            notes=r[12],
        )

    def to_row(self) -> list[str]:
        return [
            self.id,
            str(self.level),
            self.name,
            self.owner,
            self.team,
            _format_date(self.start),
            _format_date(self.end),
            str(self.duration),
            str(self.percent_complete),
            self.status,
            self.predecessors,
            _format_bool(self.milestone),
            self.notes,
        ]


@dataclass
class Program:
    name: str
    tasks: list[Task] = field(default_factory=list)
    holidays: set[date] = field(default_factory=set)

    def find(self, task_id: str) -> Optional[Task]:
        for t in self.tasks:
            if t.id == task_id:
                return t
        return None


def wbs_sort_key(task_id: str) -> tuple:
    """Sort key for WBS ids that orders children right after their parents.

    Examples (sorted ascending):  '1' < '1.1' < '1.1.1' < '1.2' < '2' < '10'.
    Tuple comparison gives the desired tree order — shorter prefixes sort
    before longer ones with the same prefix (Python tuple semantics), and
    integer comparison handles '10' > '2' correctly.
    """
    return tuple(int(p) for p in task_id.split(".") if p)


def next_wbs_id(tasks: list[Task], parent: Optional[str] = None) -> str:
    """Compute the next sibling WBS id under the given parent (or top-level if None).

    Top-level: ids with no dot. Children of `parent`: ids of the form
    `{parent}.{n}` with no further dots. Grandchildren are ignored. Always
    returns max(existing siblings) + 1, so gaps left by deletes are NOT reused.
    """
    if parent is None:
        siblings: list[int] = []
        for t in tasks:
            if t.id and "." not in t.id:
                try:
                    siblings.append(int(t.id))
                except ValueError:
                    continue
        return str(max(siblings) + 1) if siblings else "1"

    prefix = f"{parent}."
    siblings = []
    for t in tasks:
        if not t.id.startswith(prefix):
            continue
        rest = t.id[len(prefix):]
        if "." in rest:
            continue  # grandchild
        try:
            siblings.append(int(rest))
        except ValueError:
            continue
    next_n = max(siblings) + 1 if siblings else 1
    return f"{parent}.{next_n}"
