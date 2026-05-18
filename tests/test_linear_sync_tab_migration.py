"""Tests for the Phase-1 → Phase-2 `_LinearSync` migration path.

Phase 1 shipped with a 5-column schema (program, wbs_id, linear_id,
last_synced, linear_url). Phase 2 extends this to 18 columns (adds 4
sidecar + 9 snapshot fields). Existing Phase-1 installs (LINEAR_TEST
and any future ones) must auto-upgrade on first Phase-2 read without
losing their identity-row data.

These tests verify:
- `migrate_sync_tab(ss)` no-ops on Phase-2 tabs
- `migrate_sync_tab(ss)` no-ops when the tab is absent
- `migrate_sync_tab(ss)` upgrades a Phase-1 5-col tab in place,
  preserving identity rows and blank-filling the new columns
- `ensure_sync_tab(ss)` auto-migrates a Phase-1 tab encountered during
  a Phase-2 read
- `read_links(ss, program)` also auto-migrates so Phase-1 callers
  upgraded mid-flight still get usable data
- An unknown header raises SyncTabSchemaError (no silent guessing)
"""
from __future__ import annotations

import pytest

from gantt_lib.linear.sync_tab import (
    PHASE1_HEADERS,
    SYNC_HEADERS,
    SYNC_TAB,
    SyncTabSchemaError,
    ensure_sync_tab,
    migrate_sync_tab,
    read_links,
)
from tests.fixtures.fake_workbook import FakeSpreadsheet, FakeWorksheet


def _last_col_letter(n: int) -> str:
    """1 → 'A', 18 → 'R'."""
    return chr(ord("A") + n - 1)


def _seed_phase1_tab(ss: FakeSpreadsheet, *, data_rows: list[list[str]] | None = None) -> FakeWorksheet:
    """Create a `_LinearSync` worksheet on `ss` with Phase-1 5-col shape:
    row 1 = PHASE1_HEADERS, row 2 = a 5-col warning row, optional data
    rows from row 3+. Mimics the state an LINEAR_TEST install was left
    in after Phase 1 shipped."""
    ws = FakeWorksheet(SYNC_TAB, sheet_id=99)
    ws.update(
        "A1",
        [PHASE1_HEADERS, ["DO NOT EDIT — Phase 1 warning row.", "", "", "", ""]],
        value_input_option="USER_ENTERED",
    )
    if data_rows:
        ws.update(
            "A3",
            data_rows,
            value_input_option="USER_ENTERED",
        )
    ss.add_existing_worksheet(ws)
    return ws


# --- migrate_sync_tab: no-op cases ------------------------------------------


def test_migrate_returns_zero_when_tab_absent():
    ss = FakeSpreadsheet()
    assert migrate_sync_tab(ss) == 0
    # Tab should NOT have been created — migration is upgrade-only.
    with pytest.raises(Exception):
        ss.worksheet(SYNC_TAB)


def test_migrate_returns_zero_when_already_phase2():
    ss = FakeSpreadsheet()
    ensure_sync_tab(ss)  # bootstraps Phase-2 directly
    assert migrate_sync_tab(ss) == 0


# --- migrate_sync_tab: actual migration --------------------------------------


def test_migrate_upgrades_empty_phase1_tab_to_phase2():
    """Phase-1 tab with header + warning only, no data rows. Migration
    expands the header to 18 cols and leaves no data behind."""
    ss = FakeSpreadsheet()
    _seed_phase1_tab(ss)

    rows_migrated = migrate_sync_tab(ss)
    assert rows_migrated == 0  # no data rows present

    ws = ss.worksheet(SYNC_TAB)
    last_col = _last_col_letter(len(SYNC_HEADERS))
    headers = ws.get_values(f"A1:{last_col}1")[0]
    assert headers == SYNC_HEADERS
    warning = ws.get_values("A2:A2")[0][0]
    assert "DO NOT EDIT" in warning


def test_migrate_preserves_phase1_data_rows_and_blanks_new_cols():
    """Phase-1 tab with 3 data rows: each row's existing 5 cols survive,
    the 13 new cols default to blank, and the row count is reported back."""
    ss = FakeSpreadsheet()
    _seed_phase1_tab(
        ss,
        data_rows=[
            ["P1", "1", "TPM-1", "2026-05-01T00:00:00Z", "https://linear.app/x/1"],
            ["P1", "2", "TPM-2", "2026-05-01T00:00:00Z", "https://linear.app/x/2"],
            ["P2", "1", "OTHER-1", "2026-05-02T00:00:00Z", "https://linear.app/x/o1"],
        ],
    )

    rows_migrated = migrate_sync_tab(ss)
    assert rows_migrated == 3

    # Round-trip via read_links — confirms data rows materialize with the
    # new defaults (sidecar + snapshot cols blank).
    links = read_links(ss)
    assert len(links) == 3

    p1_links = [l for l in links if l.program == "P1"]
    assert len(p1_links) == 2
    by_linear = {l.linear_id: l for l in p1_links}
    assert by_linear["TPM-1"].linear_url == "https://linear.app/x/1"
    # New sidecar + snapshot cols default to "".
    assert by_linear["TPM-1"].sidecar_predecessors == ""
    assert by_linear["TPM-1"].sidecar_percent == ""
    assert by_linear["TPM-1"].snapshot_title == ""
    assert by_linear["TPM-1"].snapshot_assignee == ""
    assert by_linear["TPM-1"].snapshot_blockedby == ""


def test_migrate_is_idempotent_after_upgrade():
    """Running migrate twice on a freshly-upgraded tab is a no-op the
    second time (header matches Phase 2, so returns 0)."""
    ss = FakeSpreadsheet()
    _seed_phase1_tab(
        ss,
        data_rows=[["P1", "1", "TPM-1", "2026-05-01T00:00:00Z", ""]],
    )
    first = migrate_sync_tab(ss)
    second = migrate_sync_tab(ss)
    assert first == 1
    assert second == 0


# --- ensure_sync_tab: auto-migration on encounter ----------------------------


def test_ensure_auto_migrates_phase1_tab():
    """Calling ensure_sync_tab on a Phase-1 tab upgrades it transparently
    — caller doesn't need to invoke migrate first."""
    ss = FakeSpreadsheet()
    _seed_phase1_tab(
        ss,
        data_rows=[["P1", "1", "TPM-1", "2026-05-01T00:00:00Z", ""]],
    )
    ws = ensure_sync_tab(ss)
    last_col = _last_col_letter(len(SYNC_HEADERS))
    headers = ws.get_values(f"A1:{last_col}1")[0]
    assert headers == SYNC_HEADERS


# --- read_links: auto-migration on first read --------------------------------


def test_read_links_auto_migrates_phase1_tab():
    """read_links on a Phase-1 tab triggers migration so the returned
    SyncLinks have the full Phase-2 shape."""
    ss = FakeSpreadsheet()
    _seed_phase1_tab(
        ss,
        data_rows=[["P1", "1", "TPM-1", "2026-05-01T00:00:00Z", "https://x/1"]],
    )
    links = read_links(ss, "P1")
    assert len(links) == 1
    link = links[0]
    assert link.linear_id == "TPM-1"
    # Phase-2 fields present and default to "".
    assert hasattr(link, "snapshot_title")
    assert link.snapshot_title == ""
    assert link.sidecar_predecessors == ""

    # Verify the tab itself was actually upgraded (not just the in-memory view).
    ws = ss.worksheet(SYNC_TAB)
    last_col = _last_col_letter(len(SYNC_HEADERS))
    headers = ws.get_values(f"A1:{last_col}1")[0]
    assert headers == SYNC_HEADERS


# --- Schema drift detection --------------------------------------------------


def test_unknown_header_raises_during_migrate():
    """A header that's neither Phase 1 nor Phase 2 (e.g. someone
    hand-renamed a column) raises SyncTabSchemaError with a recovery
    hint."""
    ss = FakeSpreadsheet()
    ws = FakeWorksheet(SYNC_TAB, sheet_id=42)
    ws.update("A1", [["program", "wbs", "linear", "ts", "url"]], value_input_option="RAW")
    ss.add_existing_worksheet(ws)

    with pytest.raises(SyncTabSchemaError, match="cannot auto-migrate"):
        migrate_sync_tab(ss)
