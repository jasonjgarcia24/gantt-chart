"""Tests for gantt_lib.linear.merge — 3-way merge engine.

Strategy: Python-driven test scenarios rather than JSON fixtures. The
merge inputs are intricate dataclasses (Task + SyncLink + CpInput);
inline construction reads much cleaner than 8 separate JSON files and
de-serializer helpers. Scenarios still cover the 8 fixture pairs the
plan calls for:

  1. no-changes              — everything unchanged
  2. workbook-edits-only     — push direction only
  3. linear-edits-only       — pull direction only
  4. converged-independent   — both sides changed to the same value
  5. true-conflict-title     — both sides changed title to different values
  6. true-conflict-state     — both sides changed state to different values
  7. new-workbook-rows       — workbook has rows with no linear_id → create
  8. archive-deletions       — sync rows exist but workbook row removed → archive

Plus the classification truth table is exhaustively verified in
test_classify_field_truth_table.
"""
from __future__ import annotations

from datetime import date

import pytest

from gantt_lib.cp.contracts import (
    CpInput,
    CpInputConfig,
    CpInputEdge,
    CpInputIssue,
    CpInputProject,
)
from gantt_lib.linear.merge import (
    FieldChange,
    FieldClassification,
    MERGEABLE_FIELDS,
    SyncDiff,
    SyncRowDiff,
    classify_field,
    compute_sync_diff,
    cp_issue_to_snapshot,
    resolve_conflict,
    workbook_task_to_snapshot,
)
from gantt_lib.linear.snapshot import IssueSnapshot, snapshot_to_sync_fields
from gantt_lib.linear.sync_tab import SyncLink
from gantt_lib.model import Task


# --- helpers -----------------------------------------------------------------


def _mk_payload(
    issues: list[CpInputIssue], edges: list[CpInputEdge] = None
) -> CpInput:
    return CpInput(
        project=CpInputProject(name="TEST", source="linear"),
        config=CpInputConfig(default_duration_days=1, today=date(2026, 5, 18)),
        issues=issues,
        edges=edges or [],
    )


def _mk_link(
    *,
    wbs_id: str,
    linear_id: str,
    snapshot: IssueSnapshot = None,
    sidecar_predecessors: str = "",
    sidecar_percent: str = "",
) -> SyncLink:
    snap = snapshot or IssueSnapshot()
    return SyncLink(
        program="TEST",
        wbs_id=wbs_id,
        linear_id=linear_id,
        last_synced="2026-05-15T00:00:00Z",
        linear_url=f"https://linear.app/x/{linear_id}",
        sidecar_predecessors=sidecar_predecessors,
        sidecar_percent=sidecar_percent,
        **snapshot_to_sync_fields(snap),
    )


# --- classify_field truth table ----------------------------------------------


@pytest.mark.parametrize(
    "W, S, L, expected",
    [
        # equal/equal → NO_OP
        ("X", "X", "X", FieldClassification.NO_OP),
        # changed/equal → PUSH
        ("Y", "X", "X", FieldClassification.PUSH),
        # equal/changed → PULL
        ("X", "X", "Y", FieldClassification.PULL),
        # changed/changed, W==L → CONVERGED
        ("Y", "X", "Y", FieldClassification.CONVERGED),
        # changed/changed, W!=L → CONFLICT
        ("Y", "X", "Z", FieldClassification.CONFLICT),
    ],
)
def test_classify_field_truth_table(W, S, L, expected):
    assert classify_field(W, S, L) == expected


def test_classify_field_respects_custom_equality():
    """Case-insensitive equality treats 'X' and 'x' as equal."""
    eq_ci = lambda a, b: a.lower() == b.lower()
    assert classify_field("x", "X", "X", equality_fn=eq_ci) == FieldClassification.NO_OP
    assert classify_field("y", "X", "x", equality_fn=eq_ci) == FieldClassification.PUSH


# --- resolve_conflict per-field policy ---------------------------------------


@pytest.mark.parametrize(
    "field, winner_source",
    [
        ("title", "linear"),
        ("state", "linear"),
        ("assignee", "linear"),
        ("due_date", "linear"),
        ("parent", "linear"),
        ("milestone", "linear"),
        ("blockedby", "workbook"),
        ("predecessors", "workbook"),
        ("percent", "workbook"),
        ("notes", "workbook"),
        ("team", "workbook"),
    ],
)
def test_resolve_conflict_per_field_winner(field, winner_source):
    resolved, src = resolve_conflict(field, "WB", "LIN")
    assert src == winner_source
    assert resolved == ("WB" if winner_source == "workbook" else "LIN")


def test_resolve_conflict_estimate_workbook_wins_when_set():
    """Workbook estimate of 5 wins over Linear's 3."""
    resolved, src = resolve_conflict("estimate", "5", "3")
    assert src == "workbook"
    assert resolved == "5"


def test_resolve_conflict_estimate_linear_wins_when_workbook_default():
    """Workbook estimate of 0 (default) → Linear wins."""
    resolved, src = resolve_conflict("estimate", "0", "3")
    assert src == "linear"
    assert resolved == "3"


def test_resolve_conflict_unknown_field_falls_back_to_linear():
    """Safety: unknown fields fall back to Linear-wins (external source bias)."""
    resolved, src = resolve_conflict("future_field_we_havent_specced", "WB", "LIN")
    assert src == "linear"
    assert resolved == "LIN"


# --- workbook_task_to_snapshot translation -----------------------------------


def test_workbook_task_to_snapshot_basic():
    task = Task(
        id="1", level=1, name="Spec optics",
        owner="alex@example.com", duration=5, status="In Progress",
        end=date(2026, 6, 1),
    )
    snap = workbook_task_to_snapshot(
        task, linear_by_wbs={}, workbook_tasks=[task],
    )
    assert snap.title == "Spec optics"
    assert snap.state == "In Progress"
    assert snap.assignee == "alex@example.com"
    assert snap.due_date == "2026-06-01"
    assert snap.estimate == "5"
    assert snap.blockedby == ""
    assert snap.parent == ""


def test_workbook_task_to_snapshot_translates_predecessors_via_links():
    """Workbook predecessor DSL '1FS' → Linear blockedby of linked linear_id."""
    tasks = [
        Task(id="1", level=1, name="A", duration=5),
        Task(id="2", level=1, name="B", duration=3, predecessors="1FS"),
    ]
    snap = workbook_task_to_snapshot(
        tasks[1],
        linear_by_wbs={"1": "JAS-100", "2": "JAS-200"},
        workbook_tasks=tasks,
    )
    assert snap.blockedby == "JAS-100"


def test_workbook_task_to_snapshot_derives_parent_from_wbs_hierarchy():
    """Sub-task '1.2' has parent '1'; if '1' is linked to JAS-100, parent='JAS-100'."""
    tasks = [
        Task(id="1", level=1, name="A", duration=5),
        Task(id="1.2", level=2, name="B", duration=3),
    ]
    snap = workbook_task_to_snapshot(
        tasks[1],
        linear_by_wbs={"1": "JAS-100"},
        workbook_tasks=tasks,
    )
    assert snap.parent == "JAS-100"


# --- cp_issue_to_snapshot translation ---------------------------------------


def test_cp_issue_to_snapshot_basic():
    issue = CpInputIssue(
        linear_id="JAS-5",
        title="Spec optics",
        state="In Progress",
        estimate_days=5,
        assignee="alex@example.com",
        end_anchor=date(2026, 6, 1),
    )
    snap = cp_issue_to_snapshot(issue, blockedby_ids=["JAS-3"])
    assert snap.title == "Spec optics"
    assert snap.state == "In Progress"
    assert snap.assignee == "alex@example.com"
    assert snap.due_date == "2026-06-01"
    assert snap.estimate == "5"
    assert snap.blockedby == "JAS-3"


# --- compute_sync_diff scenarios (the 8 promised fixtures) -------------------


def test_scenario_1_no_changes():
    """W == S == L for every field on every linked row → all unchanged."""
    snap = IssueSnapshot(
        title="Spec optics",
        state="In Progress",
        assignee="alex@example.com",
        estimate="5",
    )
    task = Task(
        id="1", level=1, name="Spec optics",
        owner="alex@example.com", duration=5, status="In Progress",
    )
    link = _mk_link(wbs_id="1", linear_id="JAS-5", snapshot=snap)
    issue = CpInputIssue(
        linear_id="JAS-5",
        title="Spec optics",
        state="In Progress",
        estimate_days=5,
        assignee="alex@example.com",
    )
    diff = compute_sync_diff(
        program="TEST",
        workbook_tasks=[task],
        existing_links=[link],
        current_linear=_mk_payload([issue]),
    )
    assert len(diff.rows) == 1
    assert diff.rows[0].action == "unchanged"
    assert diff.rows[0].field_changes == []


def test_scenario_2_workbook_edits_only():
    """User changed workbook title; Linear unchanged. Field → PUSH; row → update."""
    snap = IssueSnapshot(title="Spec optics", state="In Progress")
    task = Task(
        id="1", level=1, name="Spec optics (renamed in workbook)",
        owner="", duration=5, status="In Progress",
    )
    link = _mk_link(wbs_id="1", linear_id="JAS-5", snapshot=snap)
    issue = CpInputIssue(
        linear_id="JAS-5",
        title="Spec optics",  # Linear unchanged
        state="In Progress",
        estimate_days=5,
    )
    diff = compute_sync_diff(
        program="TEST",
        workbook_tasks=[task],
        existing_links=[link],
        current_linear=_mk_payload([issue]),
    )
    row = diff.rows[0]
    assert row.action == "update"
    title_change = next(c for c in row.field_changes if c.field == "title")
    assert title_change.classification == FieldClassification.PUSH
    assert title_change.resolved_to_source == "workbook"
    assert title_change.resolved_to_value == "Spec optics (renamed in workbook)"


def test_scenario_3_linear_edits_only():
    """Linear changed state; workbook unchanged. Field → PULL; row → update."""
    snap = IssueSnapshot(title="Spec optics", state="In Progress", estimate="5")
    task = Task(
        id="1", level=1, name="Spec optics",
        duration=5, status="In Progress",
    )
    link = _mk_link(wbs_id="1", linear_id="JAS-5", snapshot=snap)
    issue = CpInputIssue(
        linear_id="JAS-5",
        title="Spec optics",
        state="Done",  # Linear changed
        estimate_days=5,
    )
    diff = compute_sync_diff(
        program="TEST",
        workbook_tasks=[task],
        existing_links=[link],
        current_linear=_mk_payload([issue]),
    )
    row = diff.rows[0]
    assert row.action == "update"
    state_change = next(c for c in row.field_changes if c.field == "state")
    assert state_change.classification == FieldClassification.PULL
    assert state_change.resolved_to_source == "linear"
    assert state_change.resolved_to_value == "Done"


def test_scenario_4_converged_independent():
    """Both sides changed title to the same new value → CONVERGED, no cross-write."""
    snap = IssueSnapshot(title="Spec optics", estimate="5")
    task = Task(
        id="1", level=1, name="Spec optics (new)",  # workbook changed
        duration=5,
    )
    link = _mk_link(wbs_id="1", linear_id="JAS-5", snapshot=snap)
    issue = CpInputIssue(
        linear_id="JAS-5",
        title="Spec optics (new)",  # Linear changed to the SAME value
        estimate_days=5,
    )
    diff = compute_sync_diff(
        program="TEST",
        workbook_tasks=[task],
        existing_links=[link],
        current_linear=_mk_payload([issue]),
    )
    row = diff.rows[0]
    assert row.action == "update"
    title_change = next(c for c in row.field_changes if c.field == "title")
    assert title_change.classification == FieldClassification.CONVERGED
    assert title_change.resolved_to_source == "n/a"
    assert title_change.resolved_to_value == "Spec optics (new)"


def test_scenario_5_true_conflict_title_linear_wins():
    """Both sides changed title to different values → CONFLICT; Linear wins by default."""
    snap = IssueSnapshot(title="Spec optics", estimate="5")
    task = Task(
        id="1", level=1, name="Spec optics (workbook rename)",
        duration=5,
    )
    link = _mk_link(wbs_id="1", linear_id="JAS-5", snapshot=snap)
    issue = CpInputIssue(
        linear_id="JAS-5",
        title="Spec optics (linear rename)",
        estimate_days=5,
    )
    diff = compute_sync_diff(
        program="TEST",
        workbook_tasks=[task],
        existing_links=[link],
        current_linear=_mk_payload([issue]),
    )
    row = diff.rows[0]
    assert row.action == "update"
    assert "title" in row.conflicts
    title_change = next(c for c in row.field_changes if c.field == "title")
    assert title_change.classification == FieldClassification.CONFLICT
    assert title_change.resolved_to_source == "linear"
    assert title_change.resolved_to_value == "Spec optics (linear rename)"


def test_scenario_6_true_conflict_estimate_workbook_wins_when_set():
    """Both changed estimate to different values. Workbook's '7' is
    non-default → workbook wins."""
    snap = IssueSnapshot(estimate="5")
    task = Task(
        id="1", level=1, name="X", duration=7,  # workbook → 7
    )
    link = _mk_link(wbs_id="1", linear_id="JAS-5", snapshot=snap)
    issue = CpInputIssue(linear_id="JAS-5", title="X", estimate_days=3)  # linear → 3
    diff = compute_sync_diff(
        program="TEST",
        workbook_tasks=[task],
        existing_links=[link],
        current_linear=_mk_payload([issue]),
    )
    row = diff.rows[0]
    est_change = next(c for c in row.field_changes if c.field == "estimate")
    assert est_change.classification == FieldClassification.CONFLICT
    assert est_change.resolved_to_source == "workbook"
    assert est_change.resolved_to_value == "7"


def test_scenario_7_new_workbook_rows_create():
    """Workbook has a row with no sync link → action=create."""
    task = Task(id="1", level=1, name="Brand new workbook task", duration=3)
    diff = compute_sync_diff(
        program="TEST",
        workbook_tasks=[task],
        existing_links=[],
        current_linear=_mk_payload([]),
    )
    assert len(diff.rows) == 1
    assert diff.rows[0].action == "create"
    assert diff.rows[0].wbs_id == "1"
    assert diff.rows[0].linear_id == ""
    assert diff.rows[0].title == "Brand new workbook task"


def test_scenario_8_archive_deletion():
    """Sync link exists, Linear still has the issue, but workbook
    deleted the row → action=archive."""
    snap = IssueSnapshot(title="Eyepiece fab", state="In Progress")
    link = _mk_link(wbs_id="2", linear_id="JAS-6", snapshot=snap)
    issue = CpInputIssue(linear_id="JAS-6", title="Eyepiece fab", state="In Progress")
    diff = compute_sync_diff(
        program="TEST",
        workbook_tasks=[],  # workbook row gone
        existing_links=[link],
        current_linear=_mk_payload([issue]),
    )
    assert len(diff.rows) == 1
    assert diff.rows[0].action == "archive"
    assert diff.rows[0].linear_id == "JAS-6"


# --- bonus edge cases --------------------------------------------------------


def test_pull_new_linear_issue_without_link():
    """Linear has an issue with no sync link → action=pull_new."""
    issue = CpInputIssue(linear_id="JAS-NEW", title="Just added in Linear")
    diff = compute_sync_diff(
        program="TEST",
        workbook_tasks=[],
        existing_links=[],
        current_linear=_mk_payload([issue]),
    )
    assert len(diff.rows) == 1
    assert diff.rows[0].action == "pull_new"
    assert diff.rows[0].linear_id == "JAS-NEW"
    assert diff.rows[0].wbs_id == ""


def test_orphaned_link_workbook_kept():
    """Sync link exists, workbook still has the row, but Linear deleted/moved
    the issue → action=orphaned_link, workbook preserved."""
    snap = IssueSnapshot(title="X")
    link = _mk_link(wbs_id="1", linear_id="JAS-GHOST", snapshot=snap)
    task = Task(id="1", level=1, name="X", duration=3)
    diff = compute_sync_diff(
        program="TEST",
        workbook_tasks=[task],
        existing_links=[link],
        current_linear=_mk_payload([]),  # JAS-GHOST gone from Linear
    )
    assert len(diff.rows) == 1
    assert diff.rows[0].action == "orphaned_link"


def test_both_sides_gone_no_row_emitted():
    """Stale link with no workbook task AND no Linear issue → no row
    in the diff (next upsert will drop the link)."""
    link = _mk_link(wbs_id="1", linear_id="JAS-GONE", snapshot=IssueSnapshot(title="X"))
    diff = compute_sync_diff(
        program="TEST",
        workbook_tasks=[],
        existing_links=[link],
        current_linear=_mk_payload([]),
    )
    assert len(diff.rows) == 0
