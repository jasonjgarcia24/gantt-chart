"""Tests for gantt_lib.linear.push — SyncDiff → MCPRequest descriptors.

Coverage:
- update rows emit one save_issue per changed row with the right kwargs
- create rows emit save_issue in pass 1 without blockedBy
- archive rows emit save_issue with state=<archive_state>
- blockedBy reconciliation: pass 2 includes adds + removes; new-issue
  references substitute placeholder tokens
- No MCP requests emitted for unchanged / pull_new / orphaned_link rows
- Pass numbers correctly assigned (1 for independents, 2 for blockedBy)
"""
from __future__ import annotations

import pytest

from gantt_lib.linear.merge import (
    FieldChange,
    FieldClassification,
    SyncDiff,
    SyncRowDiff,
)
from gantt_lib.linear.push import (
    SAVE_ISSUE_TOOL,
    MCPRequest,
    _placeholder_for,
    build_push_requests,
)
from gantt_lib.model import Task


# --- helpers -----------------------------------------------------------------


def _mk_update_row(
    *,
    wbs_id: str = "1",
    linear_id: str = "JAS-5",
    title: str = "Spec optics",
    field_changes: list[FieldChange] = None,
    conflicts: list[str] = None,
) -> SyncRowDiff:
    return SyncRowDiff(
        wbs_id=wbs_id,
        linear_id=linear_id,
        title=title,
        action="update",
        field_changes=field_changes or [],
        conflicts=conflicts or [],
    )


def _fc(field, W, S, L, classification, resolved, source):
    return FieldChange(
        field=field,
        workbook_value=W,
        snapshot_value=S,
        linear_value=L,
        classification=classification,
        resolved_to_value=resolved,
        resolved_to_source=source,
    )


def _build(rows, *, tasks_by_wbs=None, team="JasonGarcia", project="Test", archive_state="Cancelled"):
    diff = SyncDiff(program="TEST", rows=rows)
    return build_push_requests(
        diff,
        workbook_tasks_by_wbs=tasks_by_wbs or {},
        linear_team=team,
        linear_project=project,
        linear_archive_state=archive_state,
    )


# --- update rows -------------------------------------------------------------


def test_update_push_title_emits_save_issue_with_title():
    row = _mk_update_row(field_changes=[
        _fc("title", "New title", "Old title", "Old title",
            FieldClassification.PUSH, "New title", "workbook"),
    ])
    reqs = _build([row])
    assert len(reqs) == 1
    assert reqs[0].tool == SAVE_ISSUE_TOOL
    assert reqs[0].kwargs == {"id": "JAS-5", "title": "New title"}
    assert reqs[0].pass_number == 1


def test_update_with_only_pull_changes_emits_no_request():
    """If every field change is PULL or CONVERGED, the workbook has nothing
    to push back — no MCP request needed."""
    row = _mk_update_row(field_changes=[
        _fc("state", "X", "X", "Y", FieldClassification.PULL, "Y", "linear"),
    ])
    reqs = _build([row])
    assert reqs == []


def test_update_conflict_workbook_wins_emits_workbook_value():
    """estimate conflict where workbook value wins → save_issue with workbook's estimate."""
    row = _mk_update_row(field_changes=[
        _fc("estimate", "7", "5", "3",
            FieldClassification.CONFLICT, "7", "workbook"),
    ])
    reqs = _build([row])
    assert len(reqs) == 1
    assert reqs[0].kwargs == {"id": "JAS-5", "estimate": 7.0}


def test_update_conflict_linear_wins_emits_nothing_for_that_field():
    """title conflict where Linear wins → no push for that field
    (Linear already has the value)."""
    row = _mk_update_row(field_changes=[
        _fc("title", "WB", "S", "LIN",
            FieldClassification.CONFLICT, "LIN", "linear"),
    ])
    reqs = _build([row])
    # No request — title is Linear's value, no push needed.
    assert reqs == []


def test_update_pushes_multiple_fields_in_one_save_issue():
    """All field updates on one issue collapse into a single save_issue call."""
    row = _mk_update_row(field_changes=[
        _fc("title", "New", "Old", "Old", FieldClassification.PUSH, "New", "workbook"),
        _fc("state", "In Progress", "Backlog", "Backlog", FieldClassification.PUSH, "In Progress", "workbook"),
        _fc("assignee", "alex@x.com", "", "", FieldClassification.PUSH, "alex@x.com", "workbook"),
    ])
    reqs = _build([row])
    assert len(reqs) == 1
    assert reqs[0].kwargs == {
        "id": "JAS-5",
        "title": "New",
        "state": "In Progress",
        "assignee": "alex@x.com",
    }


def test_update_estimate_zero_skipped():
    """Pushing estimate=0 is treated as 'leave Linear alone' — empty
    estimate clears it on the workbook side but we don't blindly clear
    it on Linear."""
    row = _mk_update_row(field_changes=[
        _fc("estimate", "0", "5", "5", FieldClassification.PUSH, "0", "workbook"),
    ])
    reqs = _build([row])
    assert reqs == []  # nothing to push (estimate was the only changed field)


def test_update_push_due_date_emits_save_issue_with_dueDate():
    """due_date is a regular mergeable field — PUSH classification produces
    a save_issue call with the dueDate kwarg."""
    row = _mk_update_row(field_changes=[
        _fc("due_date", "2026-06-01", "", "",
            FieldClassification.PUSH, "2026-06-01", "workbook"),
    ])
    reqs = _build([row])
    assert len(reqs) == 1
    assert reqs[0].kwargs == {"id": "JAS-5", "dueDate": "2026-06-01"}
    assert reqs[0].pass_number == 1


def test_update_pull_due_date_emits_no_push():
    """PULL classification means Linear changed it; the workbook applies
    the change locally and we DON'T echo it back."""
    row = _mk_update_row(field_changes=[
        _fc("due_date", "2026-06-01", "2026-06-01", "2026-06-15",
            FieldClassification.PULL, "2026-06-15", "linear"),
    ])
    reqs = _build([row])
    assert reqs == []


def test_update_due_date_conflict_workbook_wins_pushes():
    """If a CONFLICT resolves to workbook (atypical for due_date since
    LINEAR_WINS, but force it here to verify the policy branch fires),
    we push the workbook value."""
    row = _mk_update_row(field_changes=[
        _fc("due_date", "2026-06-01", "2026-05-15", "2026-06-15",
            FieldClassification.CONFLICT, "2026-06-01", "workbook"),
    ])
    reqs = _build([row])
    assert len(reqs) == 1
    assert reqs[0].kwargs == {"id": "JAS-5", "dueDate": "2026-06-01"}


def test_update_due_date_conflict_linear_wins_does_not_push():
    """Default policy: due_date is in LINEAR_WINS. Conflict resolved to
    Linear → no MCP write needed (workbook pulls Linear's value)."""
    row = _mk_update_row(field_changes=[
        _fc("due_date", "2026-06-01", "2026-05-15", "2026-06-15",
            FieldClassification.CONFLICT, "2026-06-15", "linear"),
    ])
    reqs = _build([row])
    assert reqs == []


# --- create rows -------------------------------------------------------------


def test_create_emits_save_issue_without_blockedby_in_pass_1():
    row = SyncRowDiff(
        wbs_id="3", linear_id="", title="Manual task", action="create",
    )
    task = Task(id="3", level=1, name="Manual task", duration=5, owner="alex@x.com")
    reqs = _build([row], tasks_by_wbs={"3": task})
    assert len(reqs) == 1
    assert reqs[0].pass_number == 1
    assert reqs[0].pass_1_create_for_wbs == "3"
    assert reqs[0].kwargs == {
        "team": "JasonGarcia",
        "project": "Test",
        "title": "Manual task",
        "assignee": "alex@x.com",
        "estimate": 5.0,
    }
    # No blockedBy in the create itself.
    assert "blockedBy" not in reqs[0].kwargs


def test_create_with_predecessors_emits_pass_2_blockedby_request():
    """A new workbook task that blocks-by another (also-new) workbook
    task → pass-1 create + pass-2 blockedBy with placeholder."""
    row_a = SyncRowDiff(wbs_id="3", linear_id="", title="A", action="create")
    row_b = SyncRowDiff(wbs_id="4", linear_id="", title="B", action="create")
    task_a = Task(id="3", level=1, name="A", duration=2)
    task_b = Task(id="4", level=1, name="B", duration=3, predecessors="3FS")

    reqs = _build(
        [row_a, row_b],
        tasks_by_wbs={"3": task_a, "4": task_b},
    )
    # 2 pass-1 creates + 1 pass-2 blockedBy.
    assert len(reqs) == 3
    pass1 = [r for r in reqs if r.pass_number == 1]
    pass2 = [r for r in reqs if r.pass_number == 2]
    assert len(pass1) == 2
    assert len(pass2) == 1

    b_req = pass2[0]
    # The blockedBy refers to A's create placeholder.
    placeholder_for_a = _placeholder_for("3")
    placeholder_for_b = _placeholder_for("4")
    assert b_req.kwargs["id"] == placeholder_for_b
    assert placeholder_for_a in b_req.kwargs["blockedBy"]
    # And the request advertises both placeholders.
    assert placeholder_for_a in b_req.pass_1_placeholders
    assert placeholder_for_b in b_req.pass_1_placeholders


# --- archive rows ------------------------------------------------------------


def test_archive_emits_save_issue_with_archive_state():
    row = SyncRowDiff(
        wbs_id="5", linear_id="JAS-9", title="Launch", action="archive",
    )
    reqs = _build([row])
    assert len(reqs) == 1
    assert reqs[0].kwargs == {"id": "JAS-9", "state": "Cancelled"}
    assert reqs[0].pass_number == 1


def test_archive_with_no_archive_state_skips_silently():
    """If linear_archive_state is empty (agent didn't supply), the archive
    request is omitted — caller surfaces this as a warning."""
    row = SyncRowDiff(
        wbs_id="5", linear_id="JAS-9", title="Launch", action="archive",
    )
    reqs = _build([row], archive_state="")
    assert reqs == []


# --- blockedBy reconciliation on existing-link rows -------------------------


def test_update_blockedby_add_only_emits_pass_2_with_adds():
    """Workbook added a blocker; Linear's blockedBy gets a single add."""
    fc = _fc(
        "blockedby",
        "JAS-1,JAS-2",  # workbook target
        "JAS-1",         # last-known snapshot
        "JAS-1",         # current Linear
        FieldClassification.PUSH,
        "JAS-1,JAS-2",
        "workbook",
    )
    row = _mk_update_row(field_changes=[fc])
    reqs = _build([row])
    assert len(reqs) == 1
    assert reqs[0].pass_number == 2
    assert reqs[0].kwargs == {"id": "JAS-5", "blockedBy": ["JAS-2"]}


def test_update_blockedby_remove_only_emits_pass_2_with_removes():
    """Workbook removed a blocker; Linear's blockedBy gets a single remove."""
    fc = _fc(
        "blockedby",
        "JAS-1",         # workbook target
        "JAS-1,JAS-2",  # last-known snapshot
        "JAS-1,JAS-2",  # current Linear
        FieldClassification.PUSH,
        "JAS-1",
        "workbook",
    )
    row = _mk_update_row(field_changes=[fc])
    reqs = _build([row])
    assert len(reqs) == 1
    assert reqs[0].kwargs == {"id": "JAS-5", "removeBlockedBy": ["JAS-2"]}


def test_update_blockedby_add_and_remove_in_same_request():
    fc = _fc(
        "blockedby",
        "JAS-1,JAS-3",  # workbook target
        "JAS-1,JAS-2",  # snapshot
        "JAS-1,JAS-2",  # linear
        FieldClassification.PUSH,
        "JAS-1,JAS-3",
        "workbook",
    )
    row = _mk_update_row(field_changes=[fc])
    reqs = _build([row])
    assert len(reqs) == 1
    assert reqs[0].kwargs == {
        "id": "JAS-5",
        "blockedBy": ["JAS-3"],
        "removeBlockedBy": ["JAS-2"],
    }


# --- non-write rows ----------------------------------------------------------


def test_unchanged_emits_nothing():
    row = SyncRowDiff(wbs_id="1", linear_id="JAS-5", title="X", action="unchanged")
    reqs = _build([row])
    assert reqs == []


def test_pull_new_emits_nothing():
    """pull_new is a workbook-side action (append new row); no MCP write."""
    row = SyncRowDiff(wbs_id="", linear_id="JAS-99", title="X", action="pull_new")
    reqs = _build([row])
    assert reqs == []


def test_orphaned_link_emits_nothing():
    row = SyncRowDiff(wbs_id="1", linear_id="JAS-GHOST", title="X", action="orphaned_link")
    reqs = _build([row])
    assert reqs == []


# --- ordering ----------------------------------------------------------------


def test_pass_1_requests_come_before_pass_2_in_output():
    """The returned list is ordered pass-1 first so the agent can
    iterate without re-sorting."""
    update_with_blockedby = _mk_update_row(field_changes=[
        _fc("title", "New", "Old", "Old", FieldClassification.PUSH, "New", "workbook"),
        _fc("blockedby", "JAS-2", "", "", FieldClassification.PUSH, "JAS-2", "workbook"),
    ])
    reqs = _build([update_with_blockedby])
    assert len(reqs) == 2
    assert reqs[0].pass_number == 1  # field updates
    assert reqs[1].pass_number == 2  # blockedBy

