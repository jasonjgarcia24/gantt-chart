"""Tests for gantt_lib.baseline_io.

Pure parsing/serialization tests live here. Handler-level tests with gspread
mocks for the cmd_baseline_* commands are added in T8 to the same file.
"""
from __future__ import annotations

from datetime import date

from gantt_lib.baseline import BASELINE_HEADERS, BaselineRow
from gantt_lib.baseline_io import (
    baseline_row_to_cells,
    cells_to_baseline_row,
)
from tests.fixtures.baselines import make_baseline_row


# ---------- baseline_row_to_cells ----------

def test_serialize_row_emits_headers_in_order():
    """Cell count and order must match BASELINE_HEADERS exactly — sheet contract."""
    cells = baseline_row_to_cells(make_baseline_row())
    assert len(cells) == len(BASELINE_HEADERS)


def test_serialize_row_round_trip():
    """Serialize then parse should reproduce the original row."""
    original = make_baseline_row(
        program="TPM90", wbs="2.3", task_name="Eyepiece fab",
        snapshot_date=date(2026, 4, 12),
        baseline_start=date(2026, 4, 9), baseline_end=date(2026, 4, 22),
        baseline_duration=10,
        baseline_predecessors="1FS, 2.1SS+3",
        snapshot_label="Q2 plan freeze", snapshot_actor="jason.garcia",
    )
    cells = baseline_row_to_cells(original)
    parsed = cells_to_baseline_row(cells)
    assert parsed == original


def test_serialize_row_with_empty_dates():
    """A baselined task without start/end emits empty strings for those cells."""
    r = make_baseline_row(baseline_start=None, baseline_end=None)
    cells = baseline_row_to_cells(r)
    assert cells[4] == ""  # baseline_start col E
    assert cells[5] == ""  # baseline_end col F


# ---------- cells_to_baseline_row ----------

def test_parse_row_with_only_required_fields():
    """Optional cols (predecessors, label, actor) absent → defaults to empty string."""
    cells = ["TPM90", "1", "Concept", "2026-04-01", "2026-04-01", "2026-04-08", "6"]
    parsed = cells_to_baseline_row(cells)
    assert parsed is not None
    assert parsed.program == "TPM90"
    assert parsed.baseline_predecessors == ""
    assert parsed.snapshot_label == ""
    assert parsed.snapshot_actor == ""


def test_parse_row_with_blank_baseline_dates():
    """Empty start/end is valid — snapshot of a task without scheduled dates."""
    cells = ["TPM90", "1", "x", "2026-04-01", "", "", "0"]
    parsed = cells_to_baseline_row(cells)
    assert parsed is not None
    assert parsed.baseline_start is None
    assert parsed.baseline_end is None


def test_parse_row_returns_none_when_too_short():
    """Fewer than 7 cells → can't fill required fields → None."""
    assert cells_to_baseline_row(["TPM90", "1", "x"]) is None


def test_parse_row_returns_none_for_empty_program():
    cells = ["", "1", "x", "2026-04-01", "2026-04-01", "2026-04-08", "6"]
    assert cells_to_baseline_row(cells) is None


def test_parse_row_returns_none_for_empty_wbs():
    cells = ["TPM90", "", "x", "2026-04-01", "2026-04-01", "2026-04-08", "6"]
    assert cells_to_baseline_row(cells) is None


def test_parse_row_returns_none_for_malformed_snapshot_date():
    cells = ["TPM90", "1", "x", "not-a-date", "2026-04-01", "2026-04-08", "6"]
    assert cells_to_baseline_row(cells) is None


def test_parse_row_returns_none_for_malformed_baseline_start():
    cells = ["TPM90", "1", "x", "2026-04-01", "garbage", "2026-04-08", "6"]
    assert cells_to_baseline_row(cells) is None


def test_parse_row_returns_none_for_non_int_duration():
    cells = ["TPM90", "1", "x", "2026-04-01", "2026-04-01", "2026-04-08", "six"]
    assert cells_to_baseline_row(cells) is None


def test_parse_row_strips_whitespace_in_required_text_fields():
    """Hand-edited cells often have leading/trailing spaces."""
    cells = [" TPM90 ", " 1 ", "Concept", "2026-04-01", "2026-04-01", "2026-04-08", "6"]
    parsed = cells_to_baseline_row(cells)
    assert parsed is not None
    assert parsed.program == "TPM90"
    assert parsed.wbs == "1"
