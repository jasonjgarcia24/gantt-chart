"""Tests for gantt_lib.linear_cmds — the cmd_linear_pull stdin handler.

Drives the handler with injected stdin/stdout/stderr buffers so we can
assert on the JSON written to stdout and the result-line written to
stderr without monkey-patching sys.
"""
from __future__ import annotations

import argparse
import io
import json
from datetime import date
from pathlib import Path

import pytest

from gantt_lib import schema, sheets
from gantt_lib.linear.sync_tab import (
    SyncLink,
    read_links,
    upsert_links,
)
from gantt_lib.linear_cmds import cmd_linear_pull
from gantt_lib.model import Task
from tests.fixtures.fake_workbook import FakeSpreadsheet

PULL_FIXTURES = Path(__file__).parent / "fixtures" / "linear_pull"


def _mk_program_ws(ss: FakeSpreadsheet, program: str = "TEST"):
    tab_name = schema.program_tab_name(program)
    ws = ss.add_worksheet(title=tab_name, rows=200, cols=schema.NUM_DATA_COLS)
    blank = [""] * schema.NUM_DATA_COLS
    ws.update(
        range_name="A1",
        values=[blank, blank, blank, list(schema.DATA_HEADERS)],
        value_input_option="USER_ENTERED",
    )
    return ws


def _args(program="TEST", *, dry_run=False, force=False) -> argparse.Namespace:
    return argparse.Namespace(
        program=program, dry_run=dry_run, force=force, stdin=True,
    )


def _run(ss, payload: str, args: argparse.Namespace):
    """Drive cmd_linear_pull with the given stdin payload; return
    (exit_code, stdout_text, stderr_text)."""
    stdin = io.StringIO(payload)
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = cmd_linear_pull(args, ss, stdin=stdin, stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


# --- Happy path --------------------------------------------------------------


def test_first_pull_returns_success_summary():
    ss = FakeSpreadsheet()
    _mk_program_ws(ss)
    payload = (PULL_FIXTURES / "first_pull.json").read_text()

    code, out, err = _run(ss, payload, _args())
    assert code == 0
    parsed = json.loads(out)
    assert parsed["ok"] is True
    assert parsed["program"] == "TEST"
    assert parsed["summary"]["added"] == 3
    # Result line on stderr.
    assert "gantt: linear-pull TEST" in err
    assert "3 added" in err
    assert "✓" in err


def test_dry_run_emits_dry_run_marker_in_result_line():
    ss = FakeSpreadsheet()
    _mk_program_ws(ss)
    payload = (PULL_FIXTURES / "first_pull.json").read_text()

    code, out, err = _run(ss, payload, _args(dry_run=True))
    assert code == 0
    parsed = json.loads(out)
    assert parsed["dry_run"] is True
    assert "DRY RUN" in err
    assert "would add" in err


def test_dry_run_does_not_write_sheet_or_sync_tab():
    ss = FakeSpreadsheet()
    ws = _mk_program_ws(ss)
    payload = (PULL_FIXTURES / "first_pull.json").read_text()

    code, _out, _err = _run(ss, payload, _args(dry_run=True))
    assert code == 0
    assert sheets.read_program_tasks(ws) == []
    assert read_links(ss, "TEST") == []


# --- Error paths -------------------------------------------------------------


def test_invalid_json_returns_exit_1():
    ss = FakeSpreadsheet()
    _mk_program_ws(ss)
    code, out, err = _run(ss, "{not valid json", _args())
    assert code == 1
    parsed = json.loads(out)
    assert parsed["ok"] is False
    assert parsed["error"] == "invalid_json"
    assert "invalid JSON" in err
    assert "✗" in err


def test_contract_validation_error_returns_exit_1():
    """Missing required field → contract_validation error, exit 1."""
    ss = FakeSpreadsheet()
    _mk_program_ws(ss)
    # Missing project.source
    payload = json.dumps(
        {
            "project": {"name": "P1"},
            "config": {"default_duration_days": 1, "today": "2026-05-16"},
            "issues": [],
            "edges": [],
        }
    )
    code, out, err = _run(ss, payload, _args())
    assert code == 1
    parsed = json.loads(out)
    assert parsed["error"] == "contract_validation"
    assert "project.source" in parsed["detail"]


def test_missing_program_tab_returns_exit_2():
    """Program tab doesn't exist → exit 2 with program_tab_missing error."""
    ss = FakeSpreadsheet()
    payload = (PULL_FIXTURES / "first_pull.json").read_text()
    code, out, err = _run(ss, payload, _args())
    assert code == 2
    parsed = json.loads(out)
    assert parsed["error"] == "program_tab_missing"
    assert "program new" in parsed["detail"]


def test_cycle_in_payload_returns_exit_2():
    """Cyclic blockedBy in the payload → pull returns error dict → exit 2."""
    ss = FakeSpreadsheet()
    _mk_program_ws(ss)
    payload = json.dumps(
        {
            "project": {"name": "Cycle", "source": "linear"},
            "config": {"default_duration_days": 1, "today": "2026-05-18"},
            "issues": [
                {"linear_id": "TPM-A", "title": "A", "estimate_days": 1},
                {"linear_id": "TPM-B", "title": "B", "estimate_days": 1},
            ],
            "edges": [
                {"from_linear_id": "TPM-A", "to_linear_id": "TPM-B", "type": "FS"},
                {"from_linear_id": "TPM-B", "to_linear_id": "TPM-A", "type": "FS"},
            ],
        }
    )
    code, out, err = _run(ss, payload, _args())
    assert code == 2
    parsed = json.loads(out)
    assert parsed["error"] == "cycle_detected"


# --- Result-line shape -------------------------------------------------------


def test_apply_path_result_line_has_correct_counts():
    """Re-pull with 0 changes prints '0 added, 0 updated, N unchanged'."""
    ss = FakeSpreadsheet()
    ws = _mk_program_ws(ss)
    sheets.append_task(
        ws,
        Task(
            id="1", level=1, name="Spec optics", owner="alex@example.com",
            duration=5, status="In Progress", predecessors="",
        ),
    )
    sheets.append_task(
        ws,
        Task(
            id="2", level=1, name="Eyepiece fab",
            duration=8, status="Not Started", predecessors="1FS",
        ),
    )
    upsert_links(
        ss, "TEST",
        [
            SyncLink(program="TEST", wbs_id="1", linear_id="TPM-1",
                     last_synced="2026-05-01T00:00:00Z"),
            SyncLink(program="TEST", wbs_id="2", linear_id="TPM-2",
                     last_synced="2026-05-01T00:00:00Z"),
        ],
    )
    payload = (PULL_FIXTURES / "repull_no_changes.json").read_text()
    code, out, err = _run(ss, payload, _args())
    assert code == 0
    assert "0 added" in err
    assert "0 updated" in err
    assert "2 unchanged" in err
