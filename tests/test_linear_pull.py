"""Tests for gantt_lib.linear.pull — the Linear→workbook orchestrator.

Strategy: use FakeSpreadsheet from tests/fixtures/fake_workbook.py to host
both the program tab (`P_TEST`) and the `_LinearSync` tab. Each test
seeds the program tab with any pre-existing rows it needs, optionally
seeds `_LinearSync` rows, calls `pull(...)`, and asserts on:

  - the returned PullResult (action counts, diff records, warnings)
  - the resulting Sheet state (program tab cells + sync tab rows)
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from gantt_lib import schema, sheets
from gantt_lib.cp.contracts import from_json
from gantt_lib.linear.pull import (
    PullResult,
    ProgramTabMissingError,
    assign_wbs_ids,
    pull,
)
from gantt_lib.linear.sync_tab import (
    SyncLink,
    read_links,
    upsert_links,
)
from gantt_lib.model import Task
from tests.fixtures.fake_workbook import FakeSpreadsheet, FakeWorksheet

FIXTURES = Path(__file__).parent / "fixtures" / "linear_pull"

# Real Sheets renders HYPERLINK formulas as their display text on read;
# FakeWorksheet has no formula evaluator and returns the raw formula
# string. Tests that compare names need to peel the display text out.
_HYPERLINK_DISPLAY_RE = re.compile(
    r'^=HYPERLINK\("[^"]*",\s*"(.*)"\)$', re.DOTALL
)


def _display_text(cell: str) -> str:
    """Extract the display text from a `=HYPERLINK(url, text)` formula,
    or return the cell unchanged if it isn't a HYPERLINK. Reverses
    Sheets' `""` → `"` escape on the way out."""
    m = _HYPERLINK_DISPLAY_RE.match(cell)
    if m:
        return m.group(1).replace('""', '"')
    return cell


def _task_display_name(t: Task) -> str:
    return _display_text(t.name)


def _load(name: str):
    return from_json((FIXTURES / name).read_text())


def _mk_program_ws(ss: FakeSpreadsheet, program: str = "TEST") -> FakeWorksheet:
    """Create a minimal program tab in the fake workbook. Adds the 4
    header rows so sheets.read_program_tasks reads from row 5 correctly.
    Row 4 carries DATA_HEADERS so the schema-version validation in
    read_program_tasks_with_rows passes."""
    tab_name = schema.program_tab_name(program)
    ws = ss.add_worksheet(title=tab_name, rows=200, cols=schema.NUM_DATA_COLS)
    blank = [""] * schema.NUM_DATA_COLS
    ws.update(
        range_name="A1",
        values=[blank, blank, blank, list(schema.DATA_HEADERS)],
        value_input_option="USER_ENTERED",
    )
    return ws


def _seed_existing_task(ws, task: Task) -> int:
    """Append an existing task to a fake program tab (cols A-M)."""
    return sheets.append_task(ws, task)


# --- assign_wbs_ids ----------------------------------------------------------


def test_assign_wbs_ids_first_pull_sequential():
    inp = _load("first_pull.json")
    a = assign_wbs_ids(inp, existing_links=[])
    # 3 top-level issues, no parents.
    assert a == {"TPM-1": "1", "TPM-2": "2", "TPM-3": "3"}


def test_assign_wbs_ids_preserves_existing():
    inp = _load("repull_no_changes.json")
    existing = [
        SyncLink(program="TEST", wbs_id="5", linear_id="TPM-1", last_synced=""),
        SyncLink(program="TEST", wbs_id="7", linear_id="TPM-2", last_synced=""),
    ]
    a = assign_wbs_ids(inp, existing_links=existing)
    assert a == {"TPM-1": "5", "TPM-2": "7"}


def test_assign_wbs_ids_new_issue_gets_next_free():
    """One existing + one new issue → new one gets next free top-level id."""
    inp = _load("first_pull.json")  # TPM-1, TPM-2, TPM-3
    existing = [
        SyncLink(program="TEST", wbs_id="2", linear_id="TPM-2", last_synced=""),
    ]
    a = assign_wbs_ids(inp, existing_links=existing)
    assert a["TPM-2"] == "2"  # preserved
    # TPM-1 and TPM-3 are new; "2" is reserved by existing.
    assert a["TPM-1"] != "2"
    assert a["TPM-3"] != "2"
    assert a["TPM-1"] != a["TPM-3"]


# --- pull: error paths -------------------------------------------------------


def test_pull_raises_when_program_tab_missing():
    ss = FakeSpreadsheet()
    inp = _load("first_pull.json")
    with pytest.raises(ProgramTabMissingError, match="program new"):
        pull(ss, inp, "TEST")


# --- pull: first pull --------------------------------------------------------


def test_first_pull_adds_all_issues():
    ss = FakeSpreadsheet()
    _mk_program_ws(ss)
    inp = _load("first_pull.json")

    result = pull(ss, inp, "TEST")
    assert isinstance(result, PullResult)
    assert result.ok is True
    assert result.dry_run is False
    assert result.summary["added"] == 3
    assert result.summary["updated"] == 0
    assert result.summary["kept_unchanged"] == 0
    assert result.summary["workbook_only_preserved"] == 0


def test_first_pull_writes_tasks_to_program_tab():
    ss = FakeSpreadsheet()
    ws = _mk_program_ws(ss)
    inp = _load("first_pull.json")

    pull(ss, inp, "TEST")
    tasks = sheets.read_program_tasks(ws)
    assert len(tasks) == 3
    # WBS ids assigned sequentially.
    assert [t.id for t in tasks] == ["1", "2", "3"]
    # Predecessors translated correctly.
    by_id = {t.id: t for t in tasks}
    assert by_id["2"].predecessors == "1FS"
    assert by_id["3"].predecessors == "2FS"


def test_first_pull_writes_hyperlink_formula_in_name_column():
    ss = FakeSpreadsheet()
    ws = _mk_program_ws(ss)
    inp = _load("first_pull.json")

    pull(ss, inp, "TEST")
    # Read the raw cells (FakeWorksheet stores the formula text since it
    # doesn't model formula evaluation).
    rows = ws.get_values("A5:M7")
    name_cells = [row[2] for row in rows]
    # Each name cell is a HYPERLINK formula pointing at the Linear URL.
    assert all(cell.startswith("=HYPERLINK(") for cell in name_cells)
    assert "https://linear.app/x/issue/TPM-1" in name_cells[0]
    assert "https://linear.app/x/issue/TPM-2" in name_cells[1]


def test_first_pull_populates_sync_tab():
    ss = FakeSpreadsheet()
    _mk_program_ws(ss)
    inp = _load("first_pull.json")

    pull(ss, inp, "TEST")
    links = read_links(ss, "TEST")
    assert len(links) == 3
    by_linear = {l.linear_id: l for l in links}
    assert by_linear["TPM-1"].wbs_id == "1"
    assert by_linear["TPM-2"].wbs_id == "2"
    assert by_linear["TPM-3"].wbs_id == "3"
    assert by_linear["TPM-1"].linear_url == "https://linear.app/x/issue/TPM-1"


# --- pull: re-pull with no changes -------------------------------------------


def test_repull_no_changes_classifies_all_unchanged():
    ss = FakeSpreadsheet()
    ws = _mk_program_ws(ss)
    # Seed existing tasks identical to what Linear has.
    _seed_existing_task(
        ws,
        Task(
            id="1", level=1, name="Spec optics", owner="alex@example.com",
            duration=5, status="In Progress", predecessors="",
        ),
    )
    _seed_existing_task(
        ws,
        Task(
            id="2", level=1, name="Eyepiece fab",
            duration=8, status="Not Started", predecessors="1FS",
        ),
    )
    upsert_links(
        ss,
        "TEST",
        [
            SyncLink(program="TEST", wbs_id="1", linear_id="TPM-1",
                     last_synced="2026-05-01T00:00:00Z",
                     linear_url="https://linear.app/x/issue/TPM-1"),
            SyncLink(program="TEST", wbs_id="2", linear_id="TPM-2",
                     last_synced="2026-05-01T00:00:00Z",
                     linear_url="https://linear.app/x/issue/TPM-2"),
        ],
    )

    inp = _load("repull_no_changes.json")
    result = pull(ss, inp, "TEST")
    assert isinstance(result, PullResult)
    assert result.summary["added"] == 0
    assert result.summary["updated"] == 0
    assert result.summary["kept_unchanged"] == 2


# --- pull: re-pull with conflicts --------------------------------------------


def test_repull_with_conflicts_applies_conflict_policy():
    """Linear changed title + state + assignee + estimate.
    Workbook had a duration override + percent_complete + a note.
    Expected: title/state/assignee updated from Linear, duration/percent/notes preserved.
    """
    ss = FakeSpreadsheet()
    ws = _mk_program_ws(ss)
    _seed_existing_task(
        ws,
        Task(
            id="1", level=1, name="Spec optics", owner="alex@example.com",
            duration=10,  # workbook override (Linear has 7)
            percent_complete=50,  # workbook-only
            status="In Progress", predecessors="",
            notes="Spec needs a second review",  # workbook-only
        ),
    )
    _seed_existing_task(
        ws,
        Task(
            id="2", level=1, name="Eyepiece fab",
            duration=8, status="Not Started", predecessors="1FS",
            percent_complete=10,
        ),
    )
    upsert_links(
        ss,
        "TEST",
        [
            SyncLink(program="TEST", wbs_id="1", linear_id="TPM-1",
                     last_synced="2026-05-01T00:00:00Z"),
            SyncLink(program="TEST", wbs_id="2", linear_id="TPM-2",
                     last_synced="2026-05-01T00:00:00Z"),
        ],
    )

    inp = _load("repull_with_conflicts.json")
    result = pull(ss, inp, "TEST")
    assert isinstance(result, PullResult)
    assert result.summary["updated"] == 2
    assert result.summary["kept_unchanged"] == 0

    # Verify per-field policy on the diff record.
    diff_for_1 = next(d for d in result.diffs if d["linear_id"] == "TPM-1")
    change_fields = {c["field"]: c for c in diff_for_1["changes"]}
    assert "title" in change_fields
    assert change_fields["title"]["to"] == "Spec optics (renamed in Linear)"
    assert change_fields["title"]["source"] == "linear"
    assert "state" in change_fields
    assert change_fields["state"]["to"] == "Done"
    assert "assignee" in change_fields
    assert change_fields["assignee"]["to"] == "bob@example.com"
    # duration is NOT in the change list — workbook silently wins.
    assert "duration" not in change_fields

    # Verify the sheet was actually written with workbook-wins fields preserved.
    written = sheets.read_program_tasks(ws)
    by_id = {t.id: t for t in written}
    assert _task_display_name(by_id["1"]) == "Spec optics (renamed in Linear)"
    assert by_id["1"].status == "Done"
    assert by_id["1"].owner == "bob@example.com"
    assert by_id["1"].duration == 10  # workbook value preserved
    assert by_id["1"].percent_complete == 50  # workbook-only preserved
    assert by_id["1"].notes == "Spec needs a second review"


# --- pull: workbook-only rows ------------------------------------------------


def test_repull_preserves_workbook_only_rows():
    """Workbook has 2 rows with no Linear ID (manually added).
    Linear has 1 issue. Expected: the workbook-only rows survive."""
    ss = FakeSpreadsheet()
    ws = _mk_program_ws(ss)
    # Existing linked-to-Linear task.
    _seed_existing_task(
        ws, Task(id="1", level=1, name="Spec optics", duration=5),
    )
    # Existing workbook-only tasks (no _LinearSync entries).
    _seed_existing_task(
        ws, Task(id="2", level=1, name="Manual task A", duration=3, start=date(2026, 5, 18)),
    )
    _seed_existing_task(
        ws, Task(id="3", level=1, name="Manual task B", duration=4, start=date(2026, 5, 20)),
    )
    upsert_links(
        ss,
        "TEST",
        [
            SyncLink(program="TEST", wbs_id="1", linear_id="TPM-1",
                     last_synced="2026-05-01T00:00:00Z"),
        ],
    )

    inp = _load("repull_workbook_only_rows.json")
    result = pull(ss, inp, "TEST")
    assert isinstance(result, PullResult)
    assert result.summary["workbook_only_preserved"] == 2

    written = sheets.read_program_tasks(ws)
    names = {_task_display_name(t) for t in written}
    assert "Spec optics" in names
    assert "Manual task A" in names
    assert "Manual task B" in names


# --- pull: dry-run -----------------------------------------------------------


def test_dry_run_does_not_modify_sheet():
    ss = FakeSpreadsheet()
    ws = _mk_program_ws(ss)
    inp = _load("first_pull.json")

    result = pull(ss, inp, "TEST", dry_run=True)
    assert isinstance(result, PullResult)
    assert result.dry_run is True
    assert result.summary["added"] == 3

    # Nothing should have been written to the program tab beyond the
    # 4-row header we seeded.
    written = sheets.read_program_tasks(ws)
    assert written == []

    # And no sync tab created (we use ensure on write only).
    links = read_links(ss, "TEST")
    assert links == []


# --- pull: special characters -----------------------------------------------


def test_special_chars_in_title_quote_escaped_in_hyperlink():
    """Title with `"`, em-dash, and emoji round-trips into a valid
    HYPERLINK formula with quotes escaped per Sheets rules."""
    ss = FakeSpreadsheet()
    ws = _mk_program_ws(ss)
    inp = _load("special_chars_in_title.json")
    result = pull(ss, inp, "TEST")
    assert isinstance(result, PullResult)
    assert result.summary["added"] == 1

    # The name cell should be a HYPERLINK formula where inner quotes are
    # doubled (Sheets escape) and the em-dash + emoji pass through.
    rows = ws.get_values("A5:M5")
    name_cell = rows[0][2]
    assert name_cell.startswith("=HYPERLINK(")
    # Double-quote escape rule: any `"` in the title becomes `""`.
    assert '""quotes""' in name_cell
    # Em-dash and emoji unchanged.
    assert "—" in name_cell
    assert "🚀" in name_cell


# --- pull: force reset -------------------------------------------------------


def test_force_drops_existing_links_and_reassigns():
    ss = FakeSpreadsheet()
    ws = _mk_program_ws(ss)
    # Seed with a start anchor so cascade can compute its end. (Real
    # workbooks shouldn't have unanchored tasks lingering — cmd_recalc
    # would have errored.)
    _seed_existing_task(
        ws,
        Task(
            id="9", level=1, name="Spec optics",
            duration=5, start=date(2026, 5, 18),
        ),
    )
    upsert_links(
        ss, "TEST",
        [SyncLink(program="TEST", wbs_id="9", linear_id="TPM-1",
                  last_synced="old")],
    )

    inp = _load("first_pull.json")
    result = pull(ss, inp, "TEST", force=True)
    assert isinstance(result, PullResult)
    # Force drops old links — the existing wbs="9" row is no longer linked
    # and the fresh pull assigns new sequential ids.
    assert result.summary["added"] == 3
    # Old row at wbs "9" survives as workbook_only_preserved.
    assert result.summary["workbook_only_preserved"] == 1
