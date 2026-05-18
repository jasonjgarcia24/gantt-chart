"""Tests for gantt_lib.linear_cmds.cmd_linear_sync — the stdin handler.

Drives the handler with injected stdin/stdout/stderr buffers and verifies:
- happy path (success exit code, JSON shape, result-line format)
- dry-run marker in result line
- contract validation failures (exit 1)
- program-tab-missing (exit 2)
- direction flag honored
- MCP request list surfaced in JSON output
"""
from __future__ import annotations

import argparse
import io
import json
from datetime import date

import pytest

from gantt_lib import schema, sheets
from gantt_lib.linear.sync_tab import (
    SyncLink,
    read_links,
    upsert_links,
)
from gantt_lib.linear.snapshot import IssueSnapshot, snapshot_to_sync_fields
from gantt_lib.linear_cmds import cmd_linear_sync
from gantt_lib.model import Task
from tests.fixtures.fake_workbook import FakeSpreadsheet


def _mk_program_ws(ss: FakeSpreadsheet, program: str = "TEST"):
    tab = schema.program_tab_name(program)
    ws = ss.add_worksheet(title=tab, rows=200, cols=schema.NUM_DATA_COLS)
    blank = [""] * schema.NUM_DATA_COLS
    ws.update("A1", [blank, blank, blank, blank], value_input_option="USER_ENTERED")
    return ws


def _args(*, program="TEST", direction="both", dry_run=False, force=False):
    return argparse.Namespace(
        program=program,
        direction=direction,
        dry_run=dry_run,
        force=force,
        stdin=True,
    )


def _run(ss, payload, args):
    stdin = io.StringIO(payload)
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = cmd_linear_sync(args, ss, stdin=stdin, stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def _basic_payload():
    return json.dumps({
        "project": {"name": "TEST", "source": "linear"},
        "config": {
            "default_duration_days": 1,
            "today": "2026-05-18",
            "linear_team": "JasonGarcia",
            "linear_project": "Test Project",
            "linear_archive_state": "Cancelled",
        },
        "issues": [],
        "edges": [],
    })


# --- happy paths -------------------------------------------------------------


def test_empty_payload_succeeds_with_unchanged_count_zero():
    """Sync with no Linear issues + no workbook tasks → all zeros, exit 0."""
    ss = FakeSpreadsheet()
    _mk_program_ws(ss)
    code, out, err = _run(ss, _basic_payload(), _args())
    assert code == 0
    parsed = json.loads(out)
    assert parsed["ok"] is True
    assert parsed["program"] == "TEST"
    assert parsed["direction"] == "both"
    assert "gantt: linear-sync TEST" in err
    assert "✓" in err


def test_sync_with_workbook_create_emits_mcp_request_in_json():
    """JSON output contains the agent's MCP TODO list."""
    ss = FakeSpreadsheet()
    ws = _mk_program_ws(ss)
    sheets.append_task(ws, Task(id="1", level=1, name="Manual task", duration=5))
    code, out, err = _run(ss, _basic_payload(), _args(direction="push"))
    assert code == 0
    parsed = json.loads(out)
    assert parsed["summary"]["created"] == 1
    assert len(parsed["mcp_requests"]) == 1
    assert parsed["mcp_requests"][0]["tool"] == "mcp__claude_ai_Linear__save_issue"
    assert parsed["mcp_requests"][0]["kwargs"]["title"] == "Manual task"


def test_dry_run_marker_in_result_line():
    ss = FakeSpreadsheet()
    _mk_program_ws(ss)
    code, out, err = _run(ss, _basic_payload(), _args(dry_run=True))
    assert code == 0
    parsed = json.loads(out)
    assert parsed["dry_run"] is True
    assert "DRY RUN" in err


def test_dry_run_does_not_modify_sync_tab():
    ss = FakeSpreadsheet()
    ws = _mk_program_ws(ss)
    sheets.append_task(ws, Task(id="1", level=1, name="Manual", duration=3))
    code, _out, _err = _run(ss, _basic_payload(), _args(dry_run=True))
    assert code == 0
    assert read_links(ss, "TEST") == []


# --- direction flag ---------------------------------------------------------


def test_direction_pull_emits_no_mcp_requests():
    """Even with workbook-only rows, direction=pull skips push side."""
    ss = FakeSpreadsheet()
    ws = _mk_program_ws(ss)
    sheets.append_task(ws, Task(id="1", level=1, name="Manual", duration=3))
    code, out, _err = _run(ss, _basic_payload(), _args(direction="pull"))
    assert code == 0
    parsed = json.loads(out)
    assert parsed["mcp_requests"] == []


def test_direction_invalid_returns_exit_1():
    """Argparse choices catches `sideways`, but if someone constructs an
    args object directly with an invalid direction, the handler catches
    the ValueError from sync()."""
    ss = FakeSpreadsheet()
    _mk_program_ws(ss)
    code, out, err = _run(ss, _basic_payload(), _args(direction="sideways"))
    assert code == 1
    parsed = json.loads(out)
    assert parsed["error"] == "invalid_argument"


# --- error paths -------------------------------------------------------------


def test_invalid_json_returns_exit_1():
    ss = FakeSpreadsheet()
    _mk_program_ws(ss)
    code, out, err = _run(ss, "{not valid", _args())
    assert code == 1
    parsed = json.loads(out)
    assert parsed["error"] == "invalid_json"


def test_contract_validation_error_returns_exit_1():
    ss = FakeSpreadsheet()
    _mk_program_ws(ss)
    payload = json.dumps({
        "project": {"name": "TEST"},  # missing source
        "config": {"default_duration_days": 1, "today": "2026-05-18"},
        "issues": [],
        "edges": [],
    })
    code, out, _err = _run(ss, payload, _args())
    assert code == 1
    parsed = json.loads(out)
    assert parsed["error"] == "contract_validation"


def test_missing_program_tab_returns_exit_2():
    ss = FakeSpreadsheet()  # no program tab
    code, out, err = _run(ss, _basic_payload(), _args())
    assert code == 2
    parsed = json.loads(out)
    assert parsed["error"] == "program_tab_missing"


# --- result-line shape -------------------------------------------------------


def test_result_line_includes_all_5_summary_counts_in_apply_mode():
    ss = FakeSpreadsheet()
    _mk_program_ws(ss)
    code, _out, err = _run(ss, _basic_payload(), _args())
    # Apply mode: result line contains pushed/pulled/created/archived/conflicts/unchanged
    assert "pushed" in err
    assert "pulled" in err
    assert "created" in err
    assert "archived" in err
    assert "unchanged" in err


def test_result_line_includes_would_phrasing_in_dry_run():
    ss = FakeSpreadsheet()
    _mk_program_ws(ss)
    code, _out, err = _run(ss, _basic_payload(), _args(dry_run=True))
    assert "would push" in err
    assert "would create" in err
    assert "DRY RUN" in err
