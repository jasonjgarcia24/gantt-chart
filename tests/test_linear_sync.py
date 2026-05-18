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
from gantt_lib.linear.sync import (
    ProgramTabMissingError,
    SyncResult,
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
    ws.update("A1", [blank, blank, blank, blank], value_input_option="USER_ENTERED")
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
