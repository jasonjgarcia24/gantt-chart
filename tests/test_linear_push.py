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

from gantt_lib.cp.contracts import CpInputIssue
from gantt_lib.linear.merge import (
    FieldChange,
    FieldClassification,
    SyncDiff,
    SyncRowDiff,
)
from gantt_lib.linear.push import (
    SAVE_ISSUE_TOOL,
    SAVE_MILESTONE_TOOL,
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


def _build(
    rows,
    *,
    tasks_by_wbs=None,
    team="JasonGarcia",
    project="Test",
    archive_state="Cancelled",
    issues_by_id=None,
    team_label_map=None,
):
    diff = SyncDiff(program="TEST", rows=rows)
    return build_push_requests(
        diff,
        workbook_tasks_by_wbs=tasks_by_wbs or {},
        linear_team=team,
        linear_project=project,
        linear_archive_state=archive_state,
        linear_issues_by_id=issues_by_id,
        linear_team_label_map=team_label_map,
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


# --- team → labels push ------------------------------------------------------


def test_team_push_replaces_team_label_preserves_others():
    """Workbook changed team to Manufacturing; Linear currently has
    labels ["SW", "Bug"]. Push must replace "SW" (the previous team-label)
    with "MFG" (the new one) and preserve "Bug"."""
    row = _mk_update_row(field_changes=[
        _fc("team", "Manufacturing", "Engineering", "Engineering",
            FieldClassification.PUSH, "Manufacturing", "workbook"),
    ])
    issue = CpInputIssue(linear_id="JAS-5", title="x", labels=["SW", "Bug"])
    reqs = _build(
        [row],
        issues_by_id={"JAS-5": issue},
        team_label_map={"Engineering": "SW", "Manufacturing": "MFG"},
    )
    assert len(reqs) == 1
    assert reqs[0].kwargs["id"] == "JAS-5"
    assert set(reqs[0].kwargs["labels"]) == {"Bug", "MFG"}


def test_team_push_clearing_team_drops_team_label_only():
    """Workbook team cleared to "". Push must drop the team-label but
    keep non-team labels."""
    row = _mk_update_row(field_changes=[
        _fc("team", "", "Engineering", "Engineering",
            FieldClassification.PUSH, "", "workbook"),
    ])
    issue = CpInputIssue(linear_id="JAS-5", title="x", labels=["SW", "Bug"])
    reqs = _build(
        [row],
        issues_by_id={"JAS-5": issue},
        team_label_map={"Engineering": "SW"},
    )
    assert len(reqs) == 1
    assert reqs[0].kwargs["labels"] == ["Bug"]


def test_team_push_suppressed_when_no_label_map():
    """No team_label_map configured → team push is silently dropped
    (callers that don't want team sync get no labels writes)."""
    row = _mk_update_row(field_changes=[
        _fc("team", "Engineering", "", "",
            FieldClassification.PUSH, "Engineering", "workbook"),
    ])
    issue = CpInputIssue(linear_id="JAS-5", title="x", labels=["Bug"])
    reqs = _build(
        [row],
        issues_by_id={"JAS-5": issue},
        team_label_map=None,
    )
    assert reqs == []


def test_team_push_pull_classification_emits_nothing():
    """PULL means Linear changed the team-label; workbook absorbs it
    locally without emitting a push."""
    row = _mk_update_row(field_changes=[
        _fc("team", "Engineering", "Engineering", "Manufacturing",
            FieldClassification.PULL, "Manufacturing", "linear"),
    ])
    issue = CpInputIssue(linear_id="JAS-5", title="x", labels=["MFG"])
    reqs = _build(
        [row],
        issues_by_id={"JAS-5": issue},
        team_label_map={"Engineering": "SW", "Manufacturing": "MFG"},
    )
    assert reqs == []


def test_create_includes_team_label_when_team_set_and_map_provided():
    """A new workbook row with team='Engineering' creates a Linear issue
    with labels=['SW'] when the map has Engineering→SW."""
    row = SyncRowDiff(wbs_id="3", linear_id="", title="X", action="create")
    task = Task(id="3", level=1, name="X", duration=2, team="Engineering")
    reqs = _build(
        [row],
        tasks_by_wbs={"3": task},
        team_label_map={"Engineering": "SW"},
    )
    assert len(reqs) == 1
    assert reqs[0].kwargs.get("labels") == ["SW"]


def test_create_omits_labels_when_team_unmapped():
    """If the workbook team isn't in the map, the create has no labels."""
    row = SyncRowDiff(wbs_id="3", linear_id="", title="X", action="create")
    task = Task(id="3", level=1, name="X", duration=2, team="UnmappedTeam")
    reqs = _build(
        [row],
        tasks_by_wbs={"3": task},
        team_label_map={"Engineering": "SW"},
    )
    assert "labels" not in reqs[0].kwargs


# --- milestone push ----------------------------------------------------------


def test_milestone_push_strips_ms_prefix_for_save_issue():
    """Workbook stores milestone link as 'MS-<uuid>'; Linear's save_issue
    expects the raw UUID. Push must strip the prefix."""
    row = _mk_update_row(field_changes=[
        _fc("milestone",
            "MS-0ec2ab6b-68aa-4c46-9dd1-2f59bae7921d",
            "",
            "",
            FieldClassification.PUSH,
            "MS-0ec2ab6b-68aa-4c46-9dd1-2f59bae7921d",
            "workbook"),
    ])
    reqs = _build([row])
    assert len(reqs) == 1
    assert reqs[0].kwargs == {
        "id": "JAS-5",
        "milestone": "0ec2ab6b-68aa-4c46-9dd1-2f59bae7921d",  # prefix stripped
    }


def test_milestone_push_empty_value_clears_link():
    """Workbook cleared the milestone link → push milestone=None to
    clear it in Linear."""
    row = _mk_update_row(field_changes=[
        _fc("milestone", "", "MS-abc", "MS-abc",
            FieldClassification.PUSH, "", "workbook"),
    ])
    reqs = _build([row])
    assert len(reqs) == 1
    assert reqs[0].kwargs == {"id": "JAS-5", "milestone": None}


def test_milestone_pull_emits_no_push():
    """PULL means Linear changed the milestone; workbook applies the
    change locally and we don't echo it back."""
    row = _mk_update_row(field_changes=[
        _fc("milestone", "MS-old", "MS-old", "MS-new",
            FieldClassification.PULL, "MS-new", "linear"),
    ])
    reqs = _build([row])
    assert reqs == []


# --- milestone-row membership push -------------------------------------------


def _mk_milestone_push_payload(issues: list[CpInputIssue]):
    """Bare payload for membership-push tests."""
    from datetime import date
    from gantt_lib.cp.contracts import CpInput, CpInputConfig, CpInputProject
    return CpInput(
        project=CpInputProject(name="TEST", source="linear"),
        config=CpInputConfig(default_duration_days=1, today=date(2026, 5, 18)),
        issues=issues,
        edges=[],
    )


def _mk_link(wbs_id: str, linear_id: str):
    from gantt_lib.linear.sync_tab import SyncLink
    return SyncLink(
        program="TEST", wbs_id=wbs_id, linear_id=linear_id,
        last_synced="2026-05-18T00:00:00Z",
        linear_url=f"https://x/{linear_id}",
    )


def test_milestone_membership_push_emits_save_issue_for_new_member():
    """Workbook adds `1FS` to milestone row's Predecessors. Member IBO-5
    has no milestone in Linear → emit save_issue with `milestone` set to
    the raw UUID (MS- prefix stripped)."""
    ms_task = Task(
        id="3", level=1, name="v1.0 launch",
        milestone=True, predecessors="1FS",
    )
    member = Task(id="1", level=1, name="A", duration=2)
    payload = _mk_milestone_push_payload([
        CpInputIssue(linear_id="IBO-5", title="A"),  # no milestone in Linear
        CpInputIssue(linear_id="MS-abc-uuid", title="v1.0 launch", is_milestone=True),
    ])
    links = [
        _mk_link("1", "IBO-5"),
        _mk_link("3", "MS-abc-uuid"),
    ]
    diff = SyncDiff(program="TEST", rows=[])
    reqs = build_push_requests(
        diff,
        workbook_tasks_by_wbs={"1": member, "3": ms_task},
        linear_team="Test", linear_project="P", linear_archive_state="Canceled",
        workbook_tasks=[member, ms_task],
        payload=payload,
        existing_links=links,
    )
    assert len(reqs) == 1
    assert reqs[0].kwargs == {"id": "IBO-5", "milestone": "abc-uuid"}
    assert reqs[0].pass_number == 1
    assert "milestone membership" in reqs[0].description


def test_milestone_membership_push_skips_member_already_set_in_linear():
    """Member IBO-5 already has milestone=MS-abc-uuid in Linear → no
    redundant save_issue."""
    ms_task = Task(
        id="3", level=1, name="v1.0 launch",
        milestone=True, predecessors="1FS",
    )
    member = Task(id="1", level=1, name="A", duration=2)
    payload = _mk_milestone_push_payload([
        CpInputIssue(linear_id="IBO-5", title="A", milestone_id="MS-abc-uuid"),
        CpInputIssue(linear_id="MS-abc-uuid", title="v1.0 launch", is_milestone=True),
    ])
    links = [_mk_link("1", "IBO-5"), _mk_link("3", "MS-abc-uuid")]
    reqs = build_push_requests(
        SyncDiff(program="TEST", rows=[]),
        workbook_tasks_by_wbs={"1": member, "3": ms_task},
        linear_team="Test", linear_project="P", linear_archive_state="Canceled",
        workbook_tasks=[member, ms_task], payload=payload, existing_links=links,
    )
    assert reqs == []


def test_milestone_membership_push_only_bare_fs_zero_lag_counts():
    """Predecessor `1SS` (SS relation) and `2FS+3` (lag != 0) are NOT
    membership signals — they stay workbook-local. Only `<wbs>FS` with
    zero lag pushes."""
    ms_task = Task(
        id="3", level=1, name="v1.0 launch",
        milestone=True, predecessors="1SS, 2FS+3, 4FS",
    )
    a = Task(id="1", level=1, name="A")
    b = Task(id="2", level=1, name="B")
    d = Task(id="4", level=1, name="D")
    payload = _mk_milestone_push_payload([
        CpInputIssue(linear_id="IBO-1", title="A"),
        CpInputIssue(linear_id="IBO-2", title="B"),
        CpInputIssue(linear_id="IBO-4", title="D"),
        CpInputIssue(linear_id="MS-uuid", title="v1.0 launch", is_milestone=True),
    ])
    links = [
        _mk_link("1", "IBO-1"), _mk_link("2", "IBO-2"),
        _mk_link("4", "IBO-4"), _mk_link("3", "MS-uuid"),
    ]
    reqs = build_push_requests(
        SyncDiff(program="TEST", rows=[]),
        workbook_tasks_by_wbs={"1": a, "2": b, "4": d, "3": ms_task},
        linear_team="Test", linear_project="P", linear_archive_state="Canceled",
        workbook_tasks=[a, b, d, ms_task], payload=payload, existing_links=links,
    )
    # Only wbs=4 (the lone bare-FS-zero-lag entry) becomes a membership push.
    assert len(reqs) == 1
    assert reqs[0].kwargs == {"id": "IBO-4", "milestone": "uuid"}


def test_milestone_membership_push_skips_unlinked_members():
    """Predecessor refs a workbook task that hasn't been pushed to Linear
    yet (no SyncLink). Silently skip — next sync after the member's
    create will pick it up."""
    ms_task = Task(
        id="3", level=1, name="v1.0 launch",
        milestone=True, predecessors="1FS, 2FS",
    )
    a = Task(id="1", level=1, name="A")
    b = Task(id="2", level=1, name="B")  # not linked
    payload = _mk_milestone_push_payload([
        CpInputIssue(linear_id="IBO-1", title="A"),
        CpInputIssue(linear_id="MS-uuid", title="v1.0 launch", is_milestone=True),
    ])
    links = [_mk_link("1", "IBO-1"), _mk_link("3", "MS-uuid")]
    reqs = build_push_requests(
        SyncDiff(program="TEST", rows=[]),
        workbook_tasks_by_wbs={"1": a, "2": b, "3": ms_task},
        linear_team="Test", linear_project="P", linear_archive_state="Canceled",
        workbook_tasks=[a, b, ms_task], payload=payload, existing_links=links,
    )
    # Only IBO-1 pushes; b has no link so its entry is silently skipped.
    assert len(reqs) == 1
    assert reqs[0].kwargs["id"] == "IBO-1"


def test_milestone_membership_push_skips_unlinked_milestone_row():
    """Milestone row exists in workbook but no SyncLink yet (e.g.
    workbook-side milestone marker pre-create) → no push (nowhere to
    point membership to)."""
    ms_task = Task(
        id="3", level=1, name="v1.0 launch",
        milestone=True, predecessors="1FS",
    )
    a = Task(id="1", level=1, name="A")
    payload = _mk_milestone_push_payload([
        CpInputIssue(linear_id="IBO-1", title="A"),
    ])
    links = [_mk_link("1", "IBO-1")]  # no link for ms_task
    reqs = build_push_requests(
        SyncDiff(program="TEST", rows=[]),
        workbook_tasks_by_wbs={"1": a, "3": ms_task},
        linear_team="Test", linear_project="P", linear_archive_state="Canceled",
        workbook_tasks=[a, ms_task], payload=payload, existing_links=links,
    )
    assert reqs == []


def test_milestone_membership_push_does_not_remove_existing_members():
    """Member IBO-7 IS on milestone in Linear but workbook predecessors
    don't list it. Per additive-only policy, do NOT emit a removal
    request — the user must clear in Linear UI."""
    ms_task = Task(
        id="3", level=1, name="v1.0 launch",
        milestone=True, predecessors="",  # no members listed in workbook
    )
    member = Task(id="2", level=1, name="X")
    payload = _mk_milestone_push_payload([
        CpInputIssue(linear_id="IBO-7", title="X", milestone_id="MS-uuid"),
        CpInputIssue(linear_id="MS-uuid", title="v1.0 launch", is_milestone=True),
    ])
    links = [_mk_link("2", "IBO-7"), _mk_link("3", "MS-uuid")]
    reqs = build_push_requests(
        SyncDiff(program="TEST", rows=[]),
        workbook_tasks_by_wbs={"2": member, "3": ms_task},
        linear_team="Test", linear_project="P", linear_archive_state="Canceled",
        workbook_tasks=[member, ms_task], payload=payload, existing_links=links,
    )
    # No requests at all — additive only.
    assert reqs == []


def test_milestone_membership_push_inactive_without_optional_args():
    """Backward compat: omitting workbook_tasks / payload / existing_links
    means no milestone-membership requests are generated (the regular
    per-row push path still works)."""
    reqs = _build([])  # _build doesn't pass the new optional args
    assert reqs == []


# --- MS- update routing through save_milestone -------------------------------


def test_ms_update_due_date_routes_to_save_milestone_targetDate():
    """Linear's milestone API lives at save_milestone, not save_issue —
    save_issue with MS- id always 404s. Verify due_date push on an MS-
    row produces a save_milestone call with `targetDate`, MS- prefix
    stripped from the id, and the project name attached."""
    row = _mk_update_row(
        wbs_id="6",
        linear_id="MS-abc-def-ghi",
        title="v1.0 launch",
        field_changes=[
            _fc("due_date", "2026-06-15", "2026-06-01", "2026-06-01",
                FieldClassification.PUSH, "2026-06-15", "workbook"),
        ],
    )
    reqs = _build([row], project="Gantt Skill Test")
    assert len(reqs) == 1
    assert reqs[0].tool == SAVE_MILESTONE_TOOL
    assert reqs[0].kwargs == {
        "id": "abc-def-ghi",            # MS- prefix stripped
        "project": "Gantt Skill Test",  # required by save_milestone
        "targetDate": "2026-06-15",     # due_date → targetDate
    }


def test_ms_update_title_routes_to_save_milestone_name():
    """`title` field on a milestone row maps to `name` in save_milestone."""
    row = _mk_update_row(
        wbs_id="6",
        linear_id="MS-uuid",
        title="renamed launch",
        field_changes=[
            _fc("title", "renamed launch", "v1.0 launch", "v1.0 launch",
                FieldClassification.PUSH, "renamed launch", "workbook"),
        ],
    )
    reqs = _build([row], project="P")
    assert len(reqs) == 1
    assert reqs[0].kwargs == {
        "id": "uuid",
        "project": "P",
        "name": "renamed launch",
    }


def test_ms_update_emits_save_milestone_with_both_fields():
    """Both title + due_date changing on the same milestone collapse
    into a single save_milestone call."""
    row = _mk_update_row(
        wbs_id="6", linear_id="MS-xyz", title="renamed",
        field_changes=[
            _fc("title", "renamed", "old", "old",
                FieldClassification.PUSH, "renamed", "workbook"),
            _fc("due_date", "2026-07-01", "2026-06-01", "2026-06-01",
                FieldClassification.PUSH, "2026-07-01", "workbook"),
        ],
    )
    reqs = _build([row], project="P")
    assert len(reqs) == 1
    assert reqs[0].kwargs == {
        "id": "xyz", "project": "P",
        "name": "renamed", "targetDate": "2026-07-01",
    }


def test_ms_update_clearing_due_date_emits_targetDate_null():
    """Workbook cleared the End cell on a milestone row → push
    targetDate=null so Linear clears its milestone date too."""
    row = _mk_update_row(
        wbs_id="6", linear_id="MS-xyz", title="v1",
        field_changes=[
            _fc("due_date", "", "2026-06-01", "2026-06-01",
                FieldClassification.PUSH, "", "workbook"),
        ],
    )
    reqs = _build([row], project="P")
    assert len(reqs) == 1
    assert reqs[0].kwargs == {"id": "xyz", "project": "P", "targetDate": None}


def test_ms_update_skips_when_only_pull_changes():
    """If every field change on the MS row is PULL (Linear wins), no
    push request is emitted — same as the regular update path."""
    row = _mk_update_row(
        wbs_id="6", linear_id="MS-xyz", title="v1",
        field_changes=[
            _fc("title", "v1", "v1", "v1.1",
                FieldClassification.PULL, "v1.1", "linear"),
        ],
    )
    reqs = _build([row], project="P")
    assert reqs == []


def test_ms_update_does_not_emit_save_issue():
    """Regression guard: prior to this fix, MS- rows produced
    save_issue calls that always 404'd ("Entity not found: Issue").
    Confirm no save_issue request is ever emitted for an MS- row."""
    row = _mk_update_row(
        wbs_id="6", linear_id="MS-xyz", title="v1",
        field_changes=[
            _fc("due_date", "2026-06-15", "2026-06-01", "2026-06-01",
                FieldClassification.PUSH, "2026-06-15", "workbook"),
        ],
    )
    reqs = _build([row], project="P")
    assert all(r.tool != SAVE_ISSUE_TOOL for r in reqs)


def test_non_ms_update_still_routes_to_save_issue():
    """Sanity: regular IBO-X rows continue to use save_issue."""
    row = _mk_update_row(
        wbs_id="1", linear_id="IBO-5", title="Spec optics",
        field_changes=[
            _fc("due_date", "2026-06-15", "2026-06-01", "2026-06-01",
                FieldClassification.PUSH, "2026-06-15", "workbook"),
        ],
    )
    reqs = _build([row], project="P")
    assert len(reqs) == 1
    assert reqs[0].tool == SAVE_ISSUE_TOOL
    assert reqs[0].kwargs == {"id": "IBO-5", "dueDate": "2026-06-15"}

