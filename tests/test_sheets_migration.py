"""Tests for `migrate_program_tab_v1_to_v2` in gantt_lib.sheets.

Covers: idempotency on v2 tabs, no-op + raise on unknown shapes, and the
actual v1 → v2 column insertion (Notes shifts M → N, timeline cells
shift N+ → O+, "Milestone Link" lands at col M row 4).
"""
from __future__ import annotations

import pytest

from gantt_lib import schema
from gantt_lib.sheets import (
    apply_grey_out_cf,
    migrate_program_tab_v1_to_v2,
)
from tests.fixtures.fake_workbook import FakeSpreadsheet, FakeWorksheet


_V1_HEADERS = [
    "ID", "Level", "Name", "Owner", "Team",
    "Start", "End", "Duration", "% Complete",
    "Status", "Predecessors", "Milestone?", "Notes",
]


def _seed_v1_tab(ss: FakeSpreadsheet, program: str = "TPM90") -> FakeWorksheet:
    """Build a Phase-1-style program tab: 13 data cols + 3 timeline cols."""
    tab_name = schema.program_tab_name(program)
    ws = FakeWorksheet(tab_name, sheet_id=42)
    # Rows 1-3 blank; row 4 = v1 data headers + 3 timeline day labels.
    blank = [""] * (len(_V1_HEADERS) + 3)
    timeline_days = ["1", "2", "3"]
    ws.update("A1", [
        list(blank),
        list(blank),
        list(blank),
        list(_V1_HEADERS) + timeline_days,
    ])
    # Row 5: a sample data row with notes in col M and a value in the first timeline col (col N).
    sample = [
        "1", "1", "Sample task", "Alex", "Eng",
        "2026-06-01", "2026-06-05", "5", "0",
        "Not Started", "", "FALSE", "important notes here",
        "TIMELINE_CELL_A", "TIMELINE_CELL_B", "TIMELINE_CELL_C",
    ]
    ws.update("A5", [sample])
    ss.add_existing_worksheet(ws)
    return ws


def _seed_v2_tab(ss: FakeSpreadsheet, program: str = "TPM90") -> FakeWorksheet:
    """Build a current-schema program tab: 14 data cols + 3 timeline."""
    tab_name = schema.program_tab_name(program)
    ws = FakeWorksheet(tab_name, sheet_id=43)
    blank = [""] * (schema.NUM_DATA_COLS + 3)
    timeline_days = ["1", "2", "3"]
    ws.update("A1", [
        list(blank),
        list(blank),
        list(blank),
        list(schema.DATA_HEADERS) + timeline_days,
    ])
    ss.add_existing_worksheet(ws)
    return ws


def test_migrate_v2_tab_is_no_op():
    """A tab already on the current schema returns 'v2' without changes."""
    ss = FakeSpreadsheet()
    _seed_v2_tab(ss)
    result = migrate_program_tab_v1_to_v2(ss, "TPM90")
    assert result == "v2"


def test_migrate_v1_inserts_milestone_link_column_and_shifts_notes():
    """v1 tab (Notes at col M, timeline starts at col N) → v2 (Milestone
    Link at col M, Notes at col N, timeline starts at col O)."""
    ss = FakeSpreadsheet()
    ws = _seed_v1_tab(ss)
    result = migrate_program_tab_v1_to_v2(ss, "TPM90")
    assert result == "v2"

    # Header row 4 now matches DATA_HEADERS for the first 14 cols.
    header_after = ws.get_values("A4:N4")[0]
    assert header_after == list(schema.DATA_HEADERS)

    # The data row's Notes value ("important notes here") moved M → N.
    data_after = ws.get_values("A5:Q5")[0]
    assert data_after[schema.COL_MILESTONE_LINK_IDX] == ""        # col M: new, empty
    assert data_after[schema.COL_NOTES_IDX] == "important notes here"  # col N
    # Timeline cell that was at col N before is now at col O (idx 14).
    assert data_after[schema.TIMELINE_FIRST_COL_IDX] == "TIMELINE_CELL_A"


def test_migrate_v1_preserves_pre_milestone_columns_verbatim():
    """Cols A-L (everything to the left of the insertion point) must
    be untouched by the column insert."""
    ss = FakeSpreadsheet()
    ws = _seed_v1_tab(ss)
    migrate_program_tab_v1_to_v2(ss, "TPM90")
    row5 = ws.get_values("A5:L5")[0]
    assert row5 == [
        "1", "1", "Sample task", "Alex", "Eng",
        "2026-06-01", "2026-06-05", "5", "0",
        "Not Started", "", "FALSE",
    ]


def test_migrate_unknown_schema_raises_with_recovery_hint():
    """Header row that doesn't match v1 OR v2 raises ProgramTabSchemaError."""
    ss = FakeSpreadsheet()
    tab_name = schema.program_tab_name("WEIRD")
    ws = FakeWorksheet(tab_name, sheet_id=99)
    ws.update("A4", [["totally", "wrong", "headers"]])
    ss.add_existing_worksheet(ws)
    with pytest.raises(schema.ProgramTabSchemaError, match="not a known schema"):
        migrate_program_tab_v1_to_v2(ss, "WEIRD")


def test_migrate_missing_program_raises():
    ss = FakeSpreadsheet()
    with pytest.raises(schema.ProgramTabSchemaError, match="not found"):
        migrate_program_tab_v1_to_v2(ss, "NOPE")


def test_migrate_is_idempotent_after_first_upgrade():
    """Run migrate twice — first call upgrades v1→v2, second is a no-op."""
    ss = FakeSpreadsheet()
    _seed_v1_tab(ss)
    first = migrate_program_tab_v1_to_v2(ss, "TPM90")
    second = migrate_program_tab_v1_to_v2(ss, "TPM90")
    assert first == "v2"
    assert second == "v2"


# --- apply_grey_out_cf ------------------------------------------------------


def test_apply_grey_out_cf_emits_addConditionalFormatRule_batch():
    """Patch path: a v2 tab gets one addConditionalFormatRule request
    targeting the workbook-only-grey-out rule shape."""
    ss = FakeSpreadsheet()
    _seed_v2_tab(ss)
    apply_grey_out_cf(ss, "TPM90")

    # Find the batch_update body for the patch (skip any from the seed).
    cf_bodies = [
        b for b in ss.batch_updates
        if any("addConditionalFormatRule" in req for req in b.get("requests", []))
    ]
    assert len(cf_bodies) == 1
    rule = cf_bodies[0]["requests"][0]["addConditionalFormatRule"]["rule"]
    # Greys % Complete + Notes (the workbook-only columns).
    col_starts = sorted(r["startColumnIndex"] for r in rule["ranges"])
    assert col_starts == [schema.COL_PERCENT_IDX, schema.COL_NOTES_IDX]


def test_apply_grey_out_cf_raises_on_v1_tab():
    """Grey-out formula assumes v2 column positions (Notes at idx 13).
    On a v1 tab Notes is still at idx 12, so the rule would target the
    wrong column. Force the caller to migrate first."""
    ss = FakeSpreadsheet()
    _seed_v1_tab(ss)
    with pytest.raises(schema.ProgramTabSchemaError, match="migrate-schema"):
        apply_grey_out_cf(ss, "TPM90")


def test_apply_grey_out_cf_raises_on_missing_program():
    ss = FakeSpreadsheet()
    with pytest.raises(schema.ProgramTabSchemaError, match="not found"):
        apply_grey_out_cf(ss, "NOPE")
