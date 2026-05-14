"""Tests for gantt_lib.model — Task and Program data structures.

Round-trip is the central contract: from_row(to_row(t)) == t. Every test
case here flows through both directions so a one-sided bug can't hide.
"""
from __future__ import annotations

from datetime import date

import pytest

from gantt_lib.model import Program, Status, Task, next_wbs_id

# Column order from v1 schema (A–M):
# id, level, name, owner, team, start, end, duration, percent_complete,
# status, predecessors, milestone, notes


# ---------- Task.from_row / to_row round-trip ----------

def test_fully_populated_row_round_trips():
    row = [
        "1.1", "2", "Define OKRs", "Jason", "PM",
        "2026-06-01", "2026-06-05", "5", "0",
        "Not Started", "1FS+2", "FALSE", "kickoff prep",
    ]
    t = Task.from_row(row)
    assert t.id == "1.1"
    assert t.level == 2
    assert t.name == "Define OKRs"
    assert t.owner == "Jason"
    assert t.team == "PM"
    assert t.start == date(2026, 6, 1)
    assert t.end == date(2026, 6, 5)
    assert t.duration == 5
    assert t.percent_complete == 0
    assert t.status == "Not Started"
    assert t.predecessors == "1FS+2"
    assert t.milestone is False
    assert t.notes == "kickoff prep"
    assert t.to_row() == row


def test_minimal_row_with_only_required_fields_normalizes_on_write():
    # Only id, level, name, duration. Empty numeric cells normalize to "0"
    # on round-trip — the empty cell semantically means 0% complete, etc.
    row = ["1", "1", "Workstream A", "", "", "", "", "10", "", "", "", "", ""]
    t = Task.from_row(row)
    assert t.id == "1"
    assert t.level == 1
    assert t.name == "Workstream A"
    assert t.start is None
    assert t.end is None
    assert t.duration == 10
    assert t.percent_complete == 0
    assert t.status == ""  # empty when unset; recalc fills in default elsewhere
    assert t.predecessors == ""
    assert t.milestone is False
    # Empty percent_complete normalizes to "0" on write; everything else preserved.
    expected = ["1", "1", "Workstream A", "", "", "", "", "10", "0", "", "", "FALSE", ""]
    assert t.to_row() == expected


def test_milestone_row_round_trips():
    # Milestone has zero duration and renders as a diamond.
    row = [
        "2", "1", "Launch", "Jason", "PM",
        "2026-08-31", "2026-08-31", "0", "0",
        "Not Started", "1FS", "TRUE", "",
    ]
    t = Task.from_row(row)
    assert t.milestone is True
    assert t.duration == 0
    assert t.to_row() == row


def test_short_row_pads_to_13_columns():
    # gspread strips trailing empty cells; from_row must tolerate that.
    short = ["1.2", "2", "Quick task", "", "", "2026-06-15", "2026-06-19", "5"]
    t = Task.from_row(short)
    assert t.duration == 5
    assert t.percent_complete == 0
    assert t.notes == ""
    # Round-trip pads to full 13 cols.
    full = t.to_row()
    assert len(full) == 13
    assert full[0:8] == short


def test_in_progress_with_completion_round_trips():
    row = [
        "1.2", "2", "Build prototype", "Jason", "Eng",
        "2026-06-08", "2026-06-19", "10", "40",
        "In Progress", "1.1FS", "FALSE", "",
    ]
    t = Task.from_row(row)
    assert t.percent_complete == 40
    assert t.status == "In Progress"
    assert t.to_row() == row


# ---------- helpers + edge cases ----------

@pytest.mark.parametrize("flag", ["TRUE", "true", "True"])
def test_milestone_accepts_common_truthy_strings(flag):
    row = ["1", "1", "x", "", "", "", "", "0", "", "", "", flag, ""]
    assert Task.from_row(row).milestone is True


@pytest.mark.parametrize("flag", ["FALSE", "false", "False", "", "anything-else"])
def test_milestone_defaults_false_for_non_truthy(flag):
    row = ["1", "1", "x", "", "", "", "", "0", "", "", "", flag, ""]
    assert Task.from_row(row).milestone is False


def test_status_values_are_documented():
    # Sanity check on the enum so anyone changing it has to update tests.
    assert Status.NOT_STARTED == "Not Started"
    assert Status.IN_PROGRESS == "In Progress"
    assert Status.BLOCKED == "Blocked"
    assert Status.AT_RISK == "At Risk"
    assert Status.DONE == "Done"
    assert set(Status.all()) == {
        "Not Started", "In Progress", "Blocked", "At Risk", "Done",
    }


# ---------- Program container ----------

def test_program_holds_tasks_and_holidays():
    p = Program(name="TPM90", holidays={date(2026, 7, 3)})
    assert p.tasks == []
    assert date(2026, 7, 3) in p.holidays
    assert p.name == "TPM90"


def test_program_find_task_by_id():
    t1 = Task(id="1", level=1, name="A")
    t2 = Task(id="1.1", level=2, name="A.1")
    p = Program(name="X", tasks=[t1, t2])
    assert p.find("1.1") is t2
    assert p.find("does-not-exist") is None


# ---------- next_wbs_id ----------

def _tasks(*ids):
    return [Task(id=i, level=1, name=f"t{i}") for i in ids]


def test_next_wbs_top_level_first_task():
    assert next_wbs_id([], parent=None) == "1"


def test_next_wbs_top_level_appends_after_max():
    assert next_wbs_id(_tasks("1", "2", "3"), parent=None) == "4"


def test_next_wbs_top_level_ignores_children():
    # Children of "1" don't affect top-level numbering.
    assert next_wbs_id(_tasks("1", "1.1", "1.2"), parent=None) == "2"


def test_next_wbs_first_child_of_parent():
    assert next_wbs_id(_tasks("1", "2"), parent="1") == "1.1"


def test_next_wbs_appends_after_max_child():
    assert next_wbs_id(_tasks("1", "1.1", "1.2"), parent="1") == "1.3"


def test_next_wbs_skips_grandchildren():
    # parent="1": only direct children count, not 1.1.1.
    assert next_wbs_id(_tasks("1", "1.1", "1.1.1"), parent="1") == "1.2"


def test_next_wbs_under_nested_parent():
    assert next_wbs_id(_tasks("1", "1.1", "1.1.1"), parent="1.1") == "1.1.2"


def test_next_wbs_when_parent_has_no_children_yet():
    assert next_wbs_id(_tasks("1", "2"), parent="2") == "2.1"


def test_next_wbs_handles_gap_in_top_level_numbering():
    # User manually deleted "2" — next id is max+1, not the gap.
    assert next_wbs_id(_tasks("1", "3"), parent=None) == "4"
