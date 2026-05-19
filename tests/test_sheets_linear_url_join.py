"""Tests for the `_LinearSync` join in `read_program_tasks_with_rows`.

The join populates `task.linear_url` on read so that subsequent writes via
`update_task_data` re-render the `=HYPERLINK(...)` formula in the Name
column instead of clobbering it with plain text.

Without this join, recalc / baseline / task add-update-delete silently
strip Linear link formulas — which in turn breaks the grey-out CF
(ISFORMULA($C5) never fires once col C is plain text).
"""
from __future__ import annotations

from gantt_lib import schema
from gantt_lib.linear.sync_tab import (
    SYNC_HEADERS,
    SYNC_TAB,
    SyncLink,
    ensure_sync_tab,
    upsert_links,
)
from gantt_lib.sheets import (
    _hyperlink_formula,
    _indented_row,
    read_program_tasks,
    read_program_tasks_with_rows,
    update_task_data,
)
from tests.fixtures.fake_workbook import FakeSpreadsheet, FakeWorksheet


def _mk_program_ws(ss: FakeSpreadsheet, program: str = "LINEAR_TEST") -> FakeWorksheet:
    tab = schema.program_tab_name(program)
    ws = ss.add_worksheet(title=tab, rows=200, cols=schema.NUM_DATA_COLS)
    blank = [""] * schema.NUM_DATA_COLS
    ws.update(
        "A1",
        [blank, blank, blank, list(schema.DATA_HEADERS)],
        value_input_option="USER_ENTERED",
    )
    return ws


def _seed_task_row(ws: FakeWorksheet, wbs_id: str, name: str, *, row: int = 5):
    """Append a minimal task row (cols A-N) at the given 1-based row."""
    cells = [""] * schema.NUM_DATA_COLS
    cells[0] = wbs_id
    cells[1] = "1"
    cells[2] = name
    cells[7] = "3"  # duration
    cells[8] = "0"
    cells[11] = "FALSE"
    ws.update(f"A{row}", [cells], value_input_option="USER_ENTERED")


def _seed_sync_link(ss: FakeSpreadsheet, *, program: str, wbs_id: str,
                    linear_id: str, linear_url: str) -> None:
    """Drop a single SyncLink onto `_LinearSync`."""
    ensure_sync_tab(ss)
    upsert_links(ss, program, [SyncLink(
        program=program,
        wbs_id=wbs_id,
        linear_id=linear_id,
        last_synced="2026-05-18T00:00:00Z",
        linear_url=linear_url,
    )])


def test_read_with_no_sync_tab_returns_tasks_with_no_linear_url():
    """Workbook without _LinearSync (Phase-1 or non-Linear use) → no join,
    no auto-creation, tasks come back with linear_url == None."""
    ss = FakeSpreadsheet()
    ws = _mk_program_ws(ss)
    _seed_task_row(ws, "1", "Standalone task")

    out = read_program_tasks_with_rows(ws)
    assert len(out) == 1
    task, _, _ = out[0]
    assert task.linear_url is None
    # Critical: the join must NOT create _LinearSync as a side effect.
    assert SYNC_TAB not in {w.title for w in ss.worksheets()}


def test_read_populates_linear_url_for_linked_rows():
    """When _LinearSync has a link for the task's WBS id, the join
    sets task.linear_url so a subsequent update_task_data write
    re-renders the HYPERLINK formula in col C."""
    ss = FakeSpreadsheet()
    ws = _mk_program_ws(ss, program="LINEAR_TEST")
    _seed_task_row(ws, "1", "Spec optics")
    _seed_sync_link(
        ss, program="LINEAR_TEST", wbs_id="1",
        linear_id="JAS-5",
        linear_url="https://linear.app/jasongarcia/issue/JAS-5/spec-optics",
    )

    out = read_program_tasks_with_rows(ws)
    assert len(out) == 1
    task, _, _ = out[0]
    assert task.linear_url == "https://linear.app/jasongarcia/issue/JAS-5/spec-optics"


def test_read_leaves_unlinked_rows_with_no_linear_url():
    """Only rows whose WBS id appears in _LinearSync get a URL; other
    rows remain plain (linear_url=None)."""
    ss = FakeSpreadsheet()
    ws = _mk_program_ws(ss, program="LINEAR_TEST")
    _seed_task_row(ws, "1", "Linked task", row=5)
    _seed_task_row(ws, "2", "Unlinked task", row=6)
    _seed_sync_link(
        ss, program="LINEAR_TEST", wbs_id="1",
        linear_id="JAS-5", linear_url="https://linear.app/x/JAS-5",
    )

    by_id = {t.id: t for t, _, _ in read_program_tasks_with_rows(ws)}
    assert by_id["1"].linear_url == "https://linear.app/x/JAS-5"
    assert by_id["2"].linear_url is None


def test_read_then_write_round_trip_preserves_hyperlink_formula():
    """The end-to-end fix: read a linked task, then write it back via
    update_task_data → col C should now contain a =HYPERLINK formula,
    not the plain rendered text. This is the regression test for the
    bug where recalc silently stripped the formula."""
    ss = FakeSpreadsheet()
    ws = _mk_program_ws(ss, program="LINEAR_TEST")
    _seed_task_row(ws, "1", "Spec optics")
    url = "https://linear.app/jasongarcia/issue/JAS-5/spec-optics"
    _seed_sync_link(
        ss, program="LINEAR_TEST", wbs_id="1",
        linear_id="JAS-5", linear_url=url,
    )

    # Read (join fires) → write back (HYPERLINK re-rendered).
    tasks = read_program_tasks(ws)
    assert tasks[0].linear_url == url
    update_task_data(ws, 5, tasks[0])

    # Col C now carries the HYPERLINK formula, not plain "Spec optics".
    col_c = ws.get_values("C5:C5")[0][0]
    expected = _hyperlink_formula(url, "Spec optics")
    assert col_c == expected
    assert col_c.startswith("=HYPERLINK(")


def test_read_join_skips_when_program_isnt_in_sync_tab():
    """_LinearSync exists but has no rows for this program (e.g. another
    program's links): no URLs populated, no side effects."""
    ss = FakeSpreadsheet()
    ws = _mk_program_ws(ss, program="LINEAR_TEST")
    _seed_task_row(ws, "1", "Solo task")
    # Sync links exist for a different program.
    _seed_sync_link(
        ss, program="OTHER_PROGRAM", wbs_id="1",
        linear_id="OTH-1", linear_url="https://linear.app/x/OTH-1",
    )

    task = read_program_tasks(ws)[0]
    assert task.linear_url is None


def test_read_join_tolerates_link_with_empty_url():
    """A SyncLink with linear_url="" (rare but possible) shouldn't
    populate task.linear_url — that would render an empty HYPERLINK."""
    ss = FakeSpreadsheet()
    ws = _mk_program_ws(ss, program="LINEAR_TEST")
    _seed_task_row(ws, "1", "x")
    _seed_sync_link(
        ss, program="LINEAR_TEST", wbs_id="1",
        linear_id="JAS-5", linear_url="",
    )

    task = read_program_tasks(ws)[0]
    assert task.linear_url is None
