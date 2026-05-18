"""Tests for gantt_lib.linear.sync_tab — `_LinearSync` hidden tab CRUD.

Uses the existing FakeSpreadsheet / FakeWorksheet from tests/fixtures/
fake_workbook.py — see its docstring for the modelled subset of the
gspread surface.
"""
from __future__ import annotations

import pytest

from gantt_lib.linear.sync_tab import (
    SYNC_HEADERS,
    SYNC_TAB,
    SyncLink,
    SyncTabSchemaError,
    delete_links,
    ensure_sync_tab,
    read_links,
    upsert_links,
)
from tests.fixtures.fake_workbook import FakeSpreadsheet, FakeWorksheet


def _mk_link(program="P1", wbs="1", linear_id="TPM-1", url="https://linear.app/x/issue/TPM-1"):
    return SyncLink(
        program=program,
        wbs_id=wbs,
        linear_id=linear_id,
        last_synced="2026-05-16T17:00:00Z",
        linear_url=url,
    )


# --- ensure_sync_tab ---------------------------------------------------------


def test_ensure_creates_tab_with_headers_and_warning():
    ss = FakeSpreadsheet()
    ws = ensure_sync_tab(ss)
    assert ws.title == SYNC_TAB
    # Row 1 = headers, row 2 = warning.
    headers = ws.get_values(f"A1:{chr(ord('A') + len(SYNC_HEADERS) - 1)}1")[0]
    assert headers == SYNC_HEADERS
    warning = ws.get_values("A2:A2")[0][0]
    assert "DO NOT EDIT" in warning


def test_ensure_is_idempotent():
    ss = FakeSpreadsheet()
    ws1 = ensure_sync_tab(ss)
    ws2 = ensure_sync_tab(ss)
    assert ws1 is ws2


def test_ensure_issues_batch_update_for_hidden_property():
    """Tab creation should issue an updateSheetProperties with hidden=True."""
    ss = FakeSpreadsheet()
    ensure_sync_tab(ss)
    hidden_requests = []
    for body in ss.batch_updates:
        for req in body.get("requests", []):
            usp = req.get("updateSheetProperties", {})
            if usp.get("fields") == "hidden" and usp.get("properties", {}).get("hidden") is True:
                hidden_requests.append(req)
    assert len(hidden_requests) == 1, "expected exactly one hidden=True request"


def test_ensure_existing_tab_with_drifted_header_raises():
    """Manually-edited header → SyncTabSchemaError on next ensure()."""
    ss = FakeSpreadsheet()
    ws = FakeWorksheet(SYNC_TAB, sheet_id=42)
    ws.update("A1", [["program", "wbs", "linear", "ts", "url"]], value_input_option="RAW")
    ss.add_existing_worksheet(ws)

    with pytest.raises(SyncTabSchemaError, match="header row"):
        ensure_sync_tab(ss)


# --- read_links --------------------------------------------------------------


def test_read_links_from_missing_tab_returns_empty():
    ss = FakeSpreadsheet()
    assert read_links(ss, "P1") == []


def test_read_links_returns_empty_for_program_with_no_rows():
    ss = FakeSpreadsheet()
    ensure_sync_tab(ss)
    assert read_links(ss, "P1") == []


def test_read_links_returns_only_matching_program():
    ss = FakeSpreadsheet()
    upsert_links(ss, "P1", [_mk_link(program="P1", linear_id="TPM-1")])
    upsert_links(ss, "P2", [_mk_link(program="P2", linear_id="OTHER-1")])

    p1 = read_links(ss, "P1")
    assert len(p1) == 1
    assert p1[0].program == "P1"
    assert p1[0].linear_id == "TPM-1"

    p2 = read_links(ss, "P2")
    assert len(p2) == 1
    assert p2[0].program == "P2"


def test_read_links_no_program_filter_returns_all():
    ss = FakeSpreadsheet()
    upsert_links(ss, "P1", [_mk_link(program="P1", linear_id="TPM-1")])
    upsert_links(ss, "P2", [_mk_link(program="P2", linear_id="OTHER-1")])

    all_links = read_links(ss)
    assert len(all_links) == 2
    programs = {l.program for l in all_links}
    assert programs == {"P1", "P2"}


def test_read_links_skips_blank_and_malformed_rows():
    """Rows with empty linear_id are silently skipped (corruption-tolerant)."""
    ss = FakeSpreadsheet()
    ws = ensure_sync_tab(ss)
    # Inject a blank-ish row directly to simulate a corrupted hand-edit.
    ws.update(
        "A3:E3",
        [["P1", "1", "", "", ""]],
        value_input_option="USER_ENTERED",
    )
    # And a valid row.
    ws.update(
        "A4:E4",
        [["P1", "2", "TPM-2", "2026-05-16", "https://linear.app/x/2"]],
        value_input_option="USER_ENTERED",
    )

    links = read_links(ss, "P1")
    assert len(links) == 1
    assert links[0].linear_id == "TPM-2"


# --- upsert_links ------------------------------------------------------------


def test_upsert_writes_new_links_to_empty_tab():
    ss = FakeSpreadsheet()
    links = [
        _mk_link(program="P1", wbs="1", linear_id="TPM-1"),
        _mk_link(program="P1", wbs="2", linear_id="TPM-2"),
    ]
    upsert_links(ss, "P1", links)
    assert read_links(ss, "P1") == links


def test_upsert_replaces_existing_links_for_program():
    ss = FakeSpreadsheet()
    upsert_links(ss, "P1", [_mk_link(program="P1", wbs="1", linear_id="TPM-1")])
    # Replace with a different set.
    upsert_links(
        ss,
        "P1",
        [
            _mk_link(program="P1", wbs="1", linear_id="TPM-1-renamed"),
            _mk_link(program="P1", wbs="2", linear_id="TPM-99"),
        ],
    )
    links = read_links(ss, "P1")
    assert {l.linear_id for l in links} == {"TPM-1-renamed", "TPM-99"}


def test_upsert_preserves_other_programs():
    ss = FakeSpreadsheet()
    upsert_links(ss, "P1", [_mk_link(program="P1", linear_id="TPM-1")])
    upsert_links(ss, "P2", [_mk_link(program="P2", linear_id="OTHER-1")])

    # Replace P1; P2 untouched.
    upsert_links(
        ss,
        "P1",
        [_mk_link(program="P1", wbs="1", linear_id="TPM-A")],
    )

    p1 = read_links(ss, "P1")
    p2 = read_links(ss, "P2")
    assert {l.linear_id for l in p1} == {"TPM-A"}
    assert {l.linear_id for l in p2} == {"OTHER-1"}


def test_upsert_with_empty_list_clears_program():
    ss = FakeSpreadsheet()
    upsert_links(ss, "P1", [_mk_link(program="P1", linear_id="TPM-1")])
    upsert_links(ss, "P1", [])
    assert read_links(ss, "P1") == []


def test_upsert_rejects_link_for_wrong_program():
    ss = FakeSpreadsheet()
    with pytest.raises(ValueError, match="passed to upsert for program"):
        upsert_links(
            ss,
            "P1",
            [_mk_link(program="P2", linear_id="OTHER")],
        )


# --- delete_links ------------------------------------------------------------


def test_delete_links_returns_zero_on_missing_tab():
    ss = FakeSpreadsheet()
    assert delete_links(ss, "P1") == 0


def test_delete_links_returns_zero_when_no_rows_for_program():
    ss = FakeSpreadsheet()
    ensure_sync_tab(ss)
    assert delete_links(ss, "P1") == 0


def test_delete_links_removes_only_matching_program_and_returns_count():
    ss = FakeSpreadsheet()
    upsert_links(
        ss,
        "P1",
        [
            _mk_link(program="P1", wbs="1", linear_id="TPM-1"),
            _mk_link(program="P1", wbs="2", linear_id="TPM-2"),
        ],
    )
    upsert_links(ss, "P2", [_mk_link(program="P2", linear_id="OTHER-1")])

    n = delete_links(ss, "P1")
    assert n == 2
    assert read_links(ss, "P1") == []
    assert len(read_links(ss, "P2")) == 1
