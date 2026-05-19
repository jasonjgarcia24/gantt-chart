"""Tests for gantt_lib.linear.sync — top-level sync orchestrator.

Verifies all 3 directions produce the right outputs (workbook writes,
MCP requests, sync_tab refresh) and that dry-run mode skips all writes.

Uses FakeSpreadsheet from tests/fixtures/fake_workbook.py to host both
the program tab and `_LinearSync`.
"""
from __future__ import annotations

from datetime import date

import pytest

from gantt_lib import schema, sheets
from gantt_lib.cp.contracts import (
    CpInput,
    CpInputConfig,
    CpInputEdge,
    CpInputIssue,
    CpInputProject,
)
from gantt_lib.linear.merge import FieldChange, FieldClassification
from gantt_lib.linear.sync import (
    ProgramTabMissingError,
    SyncResult,
    _apply_milestone_to_task,
    _assign_wbs_for_pull_new,
    _augment_milestone_predecessors_from_linear,
    _augment_predecessors_from_linear,
    _build_pull_new_tasks,
    sync,
)
from gantt_lib.linear.sync_tab import (
    SyncLink,
    read_links,
    upsert_links,
)
from gantt_lib.linear.snapshot import IssueSnapshot, snapshot_to_sync_fields
from gantt_lib.model import Task
from tests.fixtures.fake_workbook import FakeSpreadsheet, FakeWorksheet


def _mk_program_ws(ss: FakeSpreadsheet, program: str = "TEST") -> FakeWorksheet:
    tab = schema.program_tab_name(program)
    ws = ss.add_worksheet(title=tab, rows=200, cols=schema.NUM_DATA_COLS)
    blank = [""] * schema.NUM_DATA_COLS
    # Row 4 = data header row (DAY_HEADER_ROW). read_program_tasks_with_rows
    # validates the schema by checking this row matches DATA_HEADERS.
    ws.update(
        "A1",
        [blank, blank, blank, list(schema.DATA_HEADERS)],
        value_input_option="USER_ENTERED",
    )
    return ws


def _mk_payload(
    issues, edges=None, *, team="JasonGarcia", project="Test", archive_state="Cancelled"
):
    return CpInput(
        project=CpInputProject(name="TEST", source="linear"),
        config=CpInputConfig(
            default_duration_days=1,
            today=date(2026, 5, 18),
            linear_team=team,
            linear_project=project,
            linear_archive_state=archive_state,
        ),
        issues=issues,
        edges=edges or [],
    )


def _mk_link(*, wbs_id, linear_id, snapshot=None, sidecar_predecessors="", sidecar_percent=""):
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


# --- error path --------------------------------------------------------------


def test_sync_raises_on_missing_program_tab():
    ss = FakeSpreadsheet()
    payload = _mk_payload([])
    with pytest.raises(ProgramTabMissingError, match="program new"):
        sync(ss, payload, "TEST")


def test_sync_rejects_unknown_direction():
    ss = FakeSpreadsheet()
    _mk_program_ws(ss)
    with pytest.raises(ValueError, match="direction"):
        sync(ss, _mk_payload([]), "TEST", direction="sideways")


# --- direction=pull (regression compat with Phase 1) ------------------------


def test_pull_direction_pulls_new_linear_issue_into_workbook():
    """Linear has an issue with no link → action=pull_new → workbook gets a new row."""
    ss = FakeSpreadsheet()
    ws = _mk_program_ws(ss)
    issue = CpInputIssue(
        linear_id="JAS-5",
        title="Spec optics",
        estimate_days=2,
        state="In Progress",
        linear_url="https://linear.app/x/JAS-5",
    )
    payload = _mk_payload([issue])
    result = sync(ss, payload, "TEST", direction="pull")

    assert result.ok is True
    assert result.direction == "pull"
    assert result.summary["pull_new"] == 1
    # MCP requests are empty for pull direction.
    assert result.mcp_requests == []
    # Workbook now has the row.
    tasks = sheets.read_program_tasks(ws)
    assert len(tasks) == 1
    assert tasks[0].duration == 2
    # Sync tab has the link.
    links = read_links(ss, "TEST")
    assert len(links) == 1
    assert links[0].linear_id == "JAS-5"


def test_pull_direction_emits_no_mcp_requests_even_for_workbook_creates():
    """Pull direction never touches Linear, even if workbook has new rows."""
    ss = FakeSpreadsheet()
    ws = _mk_program_ws(ss)
    sheets.append_task(ws, Task(id="1", level=1, name="Manual task", duration=3))
    payload = _mk_payload([])
    result = sync(ss, payload, "TEST", direction="pull")
    assert result.mcp_requests == []
    # The create row is still in the diff (informational) but no MCP call generated.
    create_rows = [d for d in result.diffs if d["action"] == "create"]
    assert len(create_rows) == 1


# --- direction=push ----------------------------------------------------------


def test_push_direction_emits_mcp_requests_for_workbook_creates():
    """Push direction emits save_issue MCP requests for new workbook tasks."""
    ss = FakeSpreadsheet()
    ws = _mk_program_ws(ss)
    sheets.append_task(ws, Task(id="1", level=1, name="Manual task", duration=5))
    payload = _mk_payload([])
    result = sync(ss, payload, "TEST", direction="push")
    assert result.summary["created"] == 1
    # One MCP request for the create.
    assert len(result.mcp_requests) == 1
    create_req = result.mcp_requests[0]
    assert create_req["tool"] == "mcp__claude_ai_Linear__save_issue"
    assert create_req["kwargs"]["title"] == "Manual task"
    assert create_req["kwargs"]["team"] == "JasonGarcia"


def test_push_direction_emits_archive_for_deleted_workbook_row():
    """Sync link exists, workbook deleted the row → archive MCP request."""
    ss = FakeSpreadsheet()
    _mk_program_ws(ss)
    upsert_links(ss, "TEST", [_mk_link(
        wbs_id="1", linear_id="JAS-5",
        snapshot=IssueSnapshot(title="Spec optics", state="In Progress"),
    )])
    issue = CpInputIssue(
        linear_id="JAS-5", title="Spec optics", state="In Progress",
        estimate_days=2,
    )
    payload = _mk_payload([issue])
    result = sync(ss, payload, "TEST", direction="push")

    assert result.summary["archived"] == 1
    archive_reqs = [r for r in result.mcp_requests if r["kwargs"].get("state") == "Cancelled"]
    assert len(archive_reqs) == 1


# --- direction=both ----------------------------------------------------------


def test_both_direction_applies_pull_and_emits_push():
    """Both directions: workbook gets pulled rows; Linear gets MCP requests."""
    ss = FakeSpreadsheet()
    ws = _mk_program_ws(ss)
    # Pre-seed a workbook-only row (will trigger a Linear create).
    sheets.append_task(ws, Task(id="1", level=1, name="New workbook task", duration=2))
    # And a Linear issue with no link (will trigger a workbook pull_new).
    issue = CpInputIssue(
        linear_id="JAS-99", title="New linear issue", estimate_days=3,
        linear_url="https://linear.app/x/JAS-99",
    )
    payload = _mk_payload([issue])

    result = sync(ss, payload, "TEST", direction="both")
    assert result.summary["created"] == 1
    assert result.summary["pull_new"] == 1
    # Workbook now has both: the pre-existing manual task + the pulled-in JAS-99.
    tasks = sheets.read_program_tasks(ws)
    names = [t.name for t in tasks]
    assert "New workbook task" in names
    # The pulled name will be wrapped as HYPERLINK; check title content.
    assert any("New linear issue" in n for n in names)
    # One MCP request for the workbook-only create.
    assert len(result.mcp_requests) == 1
    assert result.mcp_requests[0]["kwargs"]["title"] == "New workbook task"


# --- dry-run -----------------------------------------------------------------


def test_dry_run_produces_same_summary_without_writes():
    """Dry-run reports identical counts but doesn't write workbook or sync_tab."""
    ss = FakeSpreadsheet()
    ws = _mk_program_ws(ss)
    issue = CpInputIssue(linear_id="JAS-5", title="X", estimate_days=2)
    payload = _mk_payload([issue])

    dry = sync(ss, payload, "TEST", direction="both", dry_run=True)
    assert dry.dry_run is True
    assert dry.summary["pull_new"] == 1

    # No workbook writes.
    tasks = sheets.read_program_tasks(ws)
    assert tasks == []
    # No sync_tab created.
    assert read_links(ss, "TEST") == []


# --- update with linear-side change pulls into workbook ----------------------


def test_update_with_linear_state_change_pulls_into_workbook():
    """Linear changed state; workbook unchanged → pull writes new state to workbook."""
    ss = FakeSpreadsheet()
    ws = _mk_program_ws(ss)
    sheets.append_task(ws, Task(
        id="1", level=1, name="Spec optics", duration=5, status="In Progress",
    ))
    upsert_links(ss, "TEST", [_mk_link(
        wbs_id="1", linear_id="JAS-5",
        snapshot=IssueSnapshot(title="Spec optics", state="In Progress", estimate="5"),
    )])
    issue = CpInputIssue(
        linear_id="JAS-5", title="Spec optics", state="Done", estimate_days=5,
    )
    payload = _mk_payload([issue])

    result = sync(ss, payload, "TEST", direction="both")
    assert result.summary["pulled"] == 1

    # Workbook row state updated.
    tasks = sheets.read_program_tasks(ws)
    by_id = {t.id: t for t in tasks}
    assert by_id["1"].status == "Done"


def test_unchanged_row_refreshes_timestamp_in_sync_tab():
    """An unchanged row gets its last_synced refreshed."""
    ss = FakeSpreadsheet()
    ws = _mk_program_ws(ss)
    sheets.append_task(ws, Task(
        id="1", level=1, name="X", duration=5, status="In Progress",
    ))
    upsert_links(ss, "TEST", [_mk_link(
        wbs_id="1", linear_id="JAS-5",
        snapshot=IssueSnapshot(title="X", state="In Progress", estimate="5"),
    )])
    issue = CpInputIssue(linear_id="JAS-5", title="X", state="In Progress", estimate_days=5)
    payload = _mk_payload([issue])

    result = sync(ss, payload, "TEST", direction="both")
    assert result.summary["unchanged"] == 1

    links_after = read_links(ss, "TEST")
    assert len(links_after) == 1
    assert links_after[0].last_synced != "2026-05-15T00:00:00Z"  # timestamp refreshed


# --- force -------------------------------------------------------------------


def test_force_drops_existing_links_and_treats_linear_issues_as_new():
    ss = FakeSpreadsheet()
    ws = _mk_program_ws(ss)
    sheets.append_task(ws, Task(id="1", level=1, name="X", duration=5))
    upsert_links(ss, "TEST", [_mk_link(
        wbs_id="1", linear_id="JAS-5",
        snapshot=IssueSnapshot(title="X"),
    )])
    issue = CpInputIssue(linear_id="JAS-5", title="X", estimate_days=5)
    payload = _mk_payload([issue])

    result = sync(ss, payload, "TEST", direction="both", force=True)
    # Old link dropped → JAS-5 is now pull_new.
    assert result.summary["pull_new"] == 1
    # Old wbs=1 has no Linear link anymore → action=create on push side.
    assert result.summary["created"] == 1


# --- _augment_predecessors_from_linear --------------------------------------


def _fc_blockedby(W: str, S: str, L: str, classification: FieldClassification, source: str = "n/a") -> FieldChange:
    resolved = W if source == "workbook" else L if source == "linear" else W
    return FieldChange(
        field="blockedby",
        workbook_value=W,
        snapshot_value=S,
        linear_value=L,
        classification=classification,
        resolved_to_value=resolved,
        resolved_to_source=source,
    )


def test_augment_appends_new_linear_blocker_as_bare_fs():
    """Linear has JAS-7 blocking us; workbook has no predecessors.
    Augment appends "<wbs>FS" for the corresponding workbook row."""
    task = Task(id="3", level=1, name="x", duration=2, predecessors="")
    fc = _fc_blockedby(W="", S="", L="JAS-7", classification=FieldClassification.PULL, source="linear")
    _augment_predecessors_from_linear(task, [fc], wbs_by_linear={"JAS-7": "2"})
    assert task.predecessors == "2FS"


def test_augment_preserves_existing_lags_and_appends_new_blockers():
    """Workbook DSL has '1FS+3' (a 3-day lag) and Linear adds JAS-7.
    Augment keeps the lag intact and adds 2FS at the end."""
    task = Task(id="3", level=1, name="x", duration=2, predecessors="1FS+3, 4SS")
    fc = _fc_blockedby(
        W="JAS-1,JAS-4", S="JAS-1,JAS-4",
        L="JAS-1,JAS-4,JAS-7",
        classification=FieldClassification.PULL,
        source="linear",
    )
    _augment_predecessors_from_linear(
        task, [fc],
        wbs_by_linear={"JAS-1": "1", "JAS-4": "4", "JAS-7": "2"},
    )
    # Existing entries preserved verbatim; new blocker appended as bare FS.
    assert "1FS+3" in task.predecessors
    assert "4SS" in task.predecessors
    assert task.predecessors.endswith("2FS")


def test_augment_does_not_remove_workbook_predecessors_linear_dropped():
    """Linear removed JAS-1 (it's no longer in current blockedby), but
    the workbook still references it via DSL. Augmentation must NOT
    remove the workbook's entry — it's append-only."""
    task = Task(id="3", level=1, name="x", duration=2, predecessors="1FS, 4SS+2")
    fc = _fc_blockedby(
        W="JAS-1,JAS-4", S="JAS-1,JAS-4",
        L="JAS-4",  # JAS-1 gone
        classification=FieldClassification.PULL,
        source="linear",
    )
    _augment_predecessors_from_linear(
        task, [fc],
        wbs_by_linear={"JAS-1": "1", "JAS-4": "4"},
    )
    # No removals; both entries survive.
    assert task.predecessors == "1FS, 4SS+2"


def test_augment_skips_blockers_already_in_dsl_regardless_of_relation():
    """If wbs '1' is already in DSL with SS relation, don't double-add
    even though Linear's blockedby implies FS+0."""
    task = Task(id="3", level=1, name="x", duration=2, predecessors="1SS")
    fc = _fc_blockedby(
        W="JAS-1", S="", L="JAS-1",
        classification=FieldClassification.PULL,
        source="linear",
    )
    _augment_predecessors_from_linear(
        task, [fc],
        wbs_by_linear={"JAS-1": "1"},
    )
    assert task.predecessors == "1SS"  # unchanged


def test_augment_skips_unresolvable_linear_ids():
    """Linear blocker references an issue not yet linked to a workbook
    row → silently skip (next sync after the linked-row appears will
    pick it up)."""
    task = Task(id="3", level=1, name="x", duration=2, predecessors="")
    fc = _fc_blockedby(W="", S="", L="JAS-99,JAS-7",
                       classification=FieldClassification.PULL, source="linear")
    _augment_predecessors_from_linear(
        task, [fc],
        wbs_by_linear={"JAS-7": "2"},  # JAS-99 not in map
    )
    assert task.predecessors == "2FS"


def test_augment_noop_on_push_classification():
    """PUSH = workbook changed since snapshot. Workbook is authoritative;
    no augmentation needed (and would be redundant — the workbook is
    about to push its richer view to Linear)."""
    task = Task(id="3", level=1, name="x", duration=2, predecessors="1FS+3")
    fc = _fc_blockedby(W="JAS-1", S="", L="",
                       classification=FieldClassification.PUSH, source="workbook")
    _augment_predecessors_from_linear(
        task, [fc],
        wbs_by_linear={"JAS-1": "1"},
    )
    assert task.predecessors == "1FS+3"  # unchanged


def test_augment_noop_when_no_blockedby_field_change():
    task = Task(id="3", level=1, name="x", duration=2, predecessors="1FS")
    title_fc = FieldChange(
        field="title", workbook_value="X", snapshot_value="X", linear_value="Y",
        classification=FieldClassification.PULL,
        resolved_to_value="Y", resolved_to_source="linear",
    )
    _augment_predecessors_from_linear(task, [title_fc], wbs_by_linear={})
    assert task.predecessors == "1FS"


# --- _augment_milestone_predecessors_from_linear -----------------------------


def _ms_payload(issues: list[CpInputIssue]) -> CpInput:
    """Bare-bones payload for milestone-predecessor tests."""
    return CpInput(
        project=CpInputProject(name="TEST", source="linear"),
        config=CpInputConfig(default_duration_days=1, today=date(2026, 5, 18)),
        issues=issues,
        edges=[],
    )


def test_milestone_pred_augment_appends_member_wbs_as_fs():
    """Two issues belong to a milestone; the milestone row's empty
    Predecessors becomes `1FS, 2FS` (members listed in insertion order)."""
    ms_task = Task(
        id="3", level=1, name="v1.0 launch", duration=0,
        milestone=True, predecessors="",
    )
    a = Task(id="1", level=1, name="A", duration=2)
    b = Task(id="2", level=1, name="B", duration=3)
    payload = _ms_payload([
        CpInputIssue(linear_id="IBO-5", title="A", milestone_id="MS-abc"),
        CpInputIssue(linear_id="IBO-6", title="B", milestone_id="MS-abc"),
        CpInputIssue(linear_id="MS-abc", title="v1.0 launch", is_milestone=True),
    ])
    wbs_by_linear = {"IBO-5": "1", "IBO-6": "2", "MS-abc": "3"}
    modified = _augment_milestone_predecessors_from_linear(
        workbook_tasks=[ms_task, a, b],
        payload=payload,
        wbs_by_linear=wbs_by_linear,
    )
    assert modified == [ms_task]
    assert "1FS" in ms_task.predecessors
    assert "2FS" in ms_task.predecessors


def test_milestone_pred_augment_preserves_user_added_predecessors():
    """User wrote `5FS+10` (a non-member with custom lag) on the milestone
    row. Augmenting with new members must NOT clobber it."""
    ms_task = Task(
        id="3", level=1, name="v1.0 launch", duration=0,
        milestone=True, predecessors="5FS+10",
    )
    payload = _ms_payload([
        CpInputIssue(linear_id="IBO-5", title="A", milestone_id="MS-abc"),
        CpInputIssue(linear_id="MS-abc", title="v1.0 launch", is_milestone=True),
    ])
    _augment_milestone_predecessors_from_linear(
        workbook_tasks=[ms_task],
        payload=payload,
        wbs_by_linear={"IBO-5": "1", "MS-abc": "3"},
    )
    assert "5FS+10" in ms_task.predecessors  # preserved verbatim
    assert "1FS" in ms_task.predecessors      # member appended


def test_milestone_pred_augment_skips_member_already_present():
    """Member already listed (with any relation/lag) → don't double-add."""
    ms_task = Task(
        id="3", level=1, name="v1.0 launch", duration=0,
        milestone=True, predecessors="1SS+5",  # member 1 with SS, not FS
    )
    payload = _ms_payload([
        CpInputIssue(linear_id="IBO-5", title="A", milestone_id="MS-abc"),
        CpInputIssue(linear_id="MS-abc", title="v1.0 launch", is_milestone=True),
    ])
    modified = _augment_milestone_predecessors_from_linear(
        workbook_tasks=[ms_task],
        payload=payload,
        wbs_by_linear={"IBO-5": "1", "MS-abc": "3"},
    )
    assert modified == []  # nothing to add
    assert ms_task.predecessors == "1SS+5"  # unchanged


def test_milestone_pred_augment_skips_unresolvable_members():
    """Member issue not yet linked to a workbook row → silently skip;
    next sync will pick it up once the member row exists."""
    ms_task = Task(
        id="3", level=1, name="v1.0 launch", duration=0,
        milestone=True, predecessors="",
    )
    payload = _ms_payload([
        CpInputIssue(linear_id="IBO-5", title="A", milestone_id="MS-abc"),
        CpInputIssue(linear_id="IBO-99", title="B", milestone_id="MS-abc"),
        CpInputIssue(linear_id="MS-abc", title="v1.0 launch", is_milestone=True),
    ])
    _augment_milestone_predecessors_from_linear(
        workbook_tasks=[ms_task],
        payload=payload,
        wbs_by_linear={"IBO-5": "1", "MS-abc": "3"},  # IBO-99 absent
    )
    # Only the resolvable one (IBO-5 → wbs 1) is added.
    assert ms_task.predecessors == "1FS"


def test_milestone_pred_augment_skips_missing_milestone_row():
    """Linear has a milestone with members, but the workbook hasn't
    materialized the milestone row yet → no-op (caller hasn't pulled
    the milestone row into the workbook)."""
    a = Task(id="1", level=1, name="A", duration=2)
    payload = _ms_payload([
        CpInputIssue(linear_id="IBO-5", title="A", milestone_id="MS-abc"),
        CpInputIssue(linear_id="MS-abc", title="v1.0 launch", is_milestone=True),
    ])
    modified = _augment_milestone_predecessors_from_linear(
        workbook_tasks=[a],  # no milestone row
        payload=payload,
        wbs_by_linear={"IBO-5": "1"},  # MS-abc not linked yet
    )
    assert modified == []


def test_milestone_pred_augment_noop_when_no_milestone_members():
    """Payload has no issues with milestone_id set → nothing to derive."""
    ms_task = Task(
        id="3", level=1, name="v1.0 launch", duration=0,
        milestone=True, predecessors="",
    )
    payload = _ms_payload([
        CpInputIssue(linear_id="IBO-5", title="A"),  # no milestone_id
        CpInputIssue(linear_id="MS-abc", title="v1.0 launch", is_milestone=True),
    ])
    modified = _augment_milestone_predecessors_from_linear(
        workbook_tasks=[ms_task],
        payload=payload,
        wbs_by_linear={"IBO-5": "1", "MS-abc": "3"},
    )
    assert modified == []


# --- _apply_milestone_to_task ------------------------------------------------


def test_apply_milestone_resolves_ms_prefix_to_workbook_wbs():
    """Linear's milestone_id 'MS-abc' → workbook task's milestone_link
    gets the WBS of the matching workbook milestone row."""
    task = Task(id="1.1", level=2, name="sub", duration=2, milestone_link="")
    _apply_milestone_to_task(
        task,
        linear_milestone_id="MS-abc",
        wbs_by_linear={"MS-abc": "5"},  # milestone row at WBS 5
    )
    assert task.milestone_link == "5"


def test_apply_milestone_empty_value_clears_link():
    task = Task(id="1.1", level=2, name="sub", duration=2, milestone_link="5")
    _apply_milestone_to_task(task, linear_milestone_id="", wbs_by_linear={"MS-abc": "5"})
    assert task.milestone_link == ""


def test_apply_milestone_unresolvable_preserves_existing_link():
    """Linear references a milestone we don't have a workbook row for yet.
    Don't blow away the existing link — silently keep, retry next sync."""
    task = Task(id="1.1", level=2, name="sub", duration=2, milestone_link="5")
    _apply_milestone_to_task(
        task,
        linear_milestone_id="MS-unknown",
        wbs_by_linear={"MS-abc": "5"},  # MS-unknown not present
    )
    assert task.milestone_link == "5"  # unchanged


# --- _assign_wbs_for_pull_new (parent-honoring WBS assignment) -------------


def test_assign_wbs_top_level_sequential():
    """No parent_linear_id → top-level WBS 1, 2, 3."""
    payload = _mk_payload([
        CpInputIssue(linear_id="A", title="A"),
        CpInputIssue(linear_id="B", title="B"),
        CpInputIssue(linear_id="C", title="C"),
    ])
    out = _assign_wbs_for_pull_new(payload, existing_links=[])
    assert out == {"A": "1", "B": "2", "C": "3"}


def test_assign_wbs_sub_issues_nest_under_parent():
    """A child of parent IBO-6 (top-level WBS 2) gets WBS 2.1 not 4."""
    payload = _mk_payload([
        CpInputIssue(linear_id="IBO-5", title="Spec optics"),
        CpInputIssue(linear_id="IBO-6", title="Eyepiece fab"),
        CpInputIssue(linear_id="IBO-7", title="Doc revision"),
        CpInputIssue(linear_id="IBO-8", title="Eyepiece QA",
                     parent_linear_id="IBO-6"),
    ])
    out = _assign_wbs_for_pull_new(payload, existing_links=[])
    assert out == {
        "IBO-5": "1",
        "IBO-6": "2",
        "IBO-7": "3",
        "IBO-8": "2.1",  # nested under IBO-6
    }


def test_assign_wbs_existing_links_preserved_and_omitted_from_output():
    """Issues already in existing_links keep their assigned WBS (and
    are not re-emitted in the output dict). New siblings/children pick
    up the next free integer."""
    payload = _mk_payload([
        CpInputIssue(linear_id="IBO-5", title="Spec optics"),
        CpInputIssue(linear_id="IBO-6", title="Eyepiece fab"),
        CpInputIssue(linear_id="IBO-NEW", title="A new sibling"),
    ])
    existing = [
        SyncLink(program="TEST", wbs_id="1", linear_id="IBO-5", last_synced=""),
        SyncLink(program="TEST", wbs_id="2", linear_id="IBO-6", last_synced=""),
    ]
    out = _assign_wbs_for_pull_new(payload, existing_links=existing)
    # IBO-5 + IBO-6 are not in the output (already linked).
    assert "IBO-5" not in out
    assert "IBO-6" not in out
    # IBO-NEW gets WBS 3 (next free top-level).
    assert out == {"IBO-NEW": "3"}


def test_assign_wbs_chain_of_new_parents_and_children():
    """New parent + new child: both get assigned in topological order."""
    payload = _mk_payload([
        CpInputIssue(linear_id="P", title="parent"),
        CpInputIssue(linear_id="C1", title="child 1", parent_linear_id="P"),
        CpInputIssue(linear_id="C2", title="child 2", parent_linear_id="P"),
    ])
    out = _assign_wbs_for_pull_new(payload, existing_links=[])
    assert out == {"P": "1", "C1": "1.1", "C2": "1.2"}


# --- _build_pull_new_tasks (cp/adapter + cascade for coherence) ------------


def test_build_pull_new_tasks_populates_predecessors_from_edges():
    """Linear blockedBy → workbook predecessor DSL.
    A blocked B: B.predecessors = "<A's WBS>FS"."""
    payload = CpInput(
        project=CpInputProject(name="TEST", source="linear"),
        config=CpInputConfig(default_duration_days=1, today=date(2026, 5, 18)),
        issues=[
            CpInputIssue(linear_id="A", title="A", estimate_days=2),
            CpInputIssue(linear_id="B", title="B", estimate_days=3),
        ],
        edges=[
            CpInputEdge(from_linear_id="A", to_linear_id="B", type="FS", lag_days=0),
        ],
    )
    pull_new_wbs = {"A": "1", "B": "2"}
    out = _build_pull_new_tasks(
        payload=payload, pull_new_wbs=pull_new_wbs, existing_links=[],
    )
    assert out["A"].predecessors == ""
    assert "1FS" in out["B"].predecessors


def test_build_pull_new_tasks_yields_coherent_start_end_duration():
    """After cascade, Start + Duration matches End in working days.
    A (2d, anchored today) → B (3d, blockedBy A)."""
    today = date(2026, 5, 18)  # Monday
    payload = CpInput(
        project=CpInputProject(name="TEST", source="linear"),
        config=CpInputConfig(default_duration_days=1, today=today),
        issues=[
            CpInputIssue(linear_id="A", title="A", estimate_days=2),
            CpInputIssue(linear_id="B", title="B", estimate_days=3),
        ],
        edges=[
            CpInputEdge(from_linear_id="A", to_linear_id="B", type="FS", lag_days=0),
        ],
    )
    out = _build_pull_new_tasks(
        payload=payload, pull_new_wbs={"A": "1", "B": "2"}, existing_links=[],
    )
    # A: starts Mon 5/18, dur=2 → ends Tue 5/19 (working days inclusive).
    assert out["A"].start == today
    assert out["A"].duration == 2
    assert out["A"].end is not None
    # B: starts right after A's end, dur=3 → has a real end date.
    assert out["B"].start is not None
    assert out["B"].duration == 3
    assert out["B"].end is not None
    # The cascade chain ran (B starts after A ends).
    assert out["B"].start >= out["A"].end


def test_build_pull_new_tasks_assigns_correct_level_from_wbs():
    """Sub-issue at WBS '2.1' gets level=2."""
    payload = CpInput(
        project=CpInputProject(name="TEST", source="linear"),
        config=CpInputConfig(default_duration_days=1, today=date(2026, 5, 18)),
        issues=[
            CpInputIssue(linear_id="P", title="parent", estimate_days=1),
            CpInputIssue(linear_id="C", title="child", estimate_days=1,
                         parent_linear_id="P"),
        ],
        edges=[],
    )
    out = _build_pull_new_tasks(
        payload=payload, pull_new_wbs={"P": "2", "C": "2.1"}, existing_links=[],
    )
    assert out["P"].level == 1
    assert out["C"].level == 2
    assert out["C"].id == "2.1"


def test_build_pull_new_tasks_returns_empty_when_no_pull_new():
    """No new issues to pull → return empty dict; don't waste cycles."""
    payload = CpInput(
        project=CpInputProject(name="TEST", source="linear"),
        config=CpInputConfig(default_duration_days=1, today=date(2026, 5, 18)),
        issues=[],
        edges=[],
    )
    out = _build_pull_new_tasks(
        payload=payload, pull_new_wbs={}, existing_links=[],
    )
    assert out == {}
