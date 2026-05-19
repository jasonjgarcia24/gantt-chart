"""Tests for gantt_lib.schema — pure-Python helpers (no Sheets API).

The Sheets-API request builders are just dict shapers and are exercised
end-to-end by `gantt program new` against a real workbook.
"""
from __future__ import annotations

from datetime import date

import pytest

from gantt_lib.schema import (
    COL_DURATION_IDX,
    COL_MILESTONE_LINK_IDX,
    COL_NOTES_IDX,
    COL_PERCENT_IDX,
    COL_TEAM_IDX,
    DATA_HEADERS,
    DAY_HEADER_ROW,
    DEFAULT_TEAM_COLOR,
    FIRST_TASK_ROW,
    HEADER_ROWS,
    NUM_DATA_COLS,
    PROGRAM_TAB_PREFIX,
    STATUS_VALUES,
    TIMELINE_DAYS,
    WEEK_HEADER_ROW,
    WORKBOOK_ONLY_GREY,
    ProgramTabSchemaError,
    assert_program_tab_v2,
    boundary_border_request,
    col_letter,
    day_row_format_request,
    default_team_color_cf_request,
    detect_program_tab_schema,
    grouping_runs,
    hex_to_rgb01,
    linked_workbook_only_grey_out_cf_request,
    make_timeline_days,
    milestone_row_grey_out_cf_request,
    month_label,
    program_tab_name,
    quarter_label,
    team_color_cf_request,
    timeline_arrayformula,
    week_num_label,
    weekend_cf_request,
    wrap_strategy_request,
)


def test_program_tab_name_uses_prefix():
    assert program_tab_name("TPM90") == "P_TPM90"
    assert PROGRAM_TAB_PREFIX == "P_"


# ---------- program-tab schema detection ----------

V1_HEADERS_FOR_TEST = [
    "ID", "Level", "Name", "Owner", "Team",
    "Start", "End", "Duration", "% Complete",
    "Status", "Predecessors", "Milestone?", "Notes",
]


def test_detect_schema_v2_matches_current_data_headers():
    assert detect_program_tab_schema(DATA_HEADERS) == "v2"


def test_detect_schema_v2_ignores_trailing_timeline_cells():
    """Header row in the sheet also carries day-of-month values past
    NUM_DATA_COLS. Detection compares only the first NUM_DATA_COLS cells."""
    row_with_timeline = list(DATA_HEADERS) + ["1", "2", "3"]
    assert detect_program_tab_schema(row_with_timeline) == "v2"


def test_detect_schema_v1_matches_old_13col_headers():
    assert detect_program_tab_schema(V1_HEADERS_FOR_TEST) == "v1"


def test_detect_schema_unknown_for_empty_or_mangled_row():
    assert detect_program_tab_schema([]) == "unknown"
    assert detect_program_tab_schema(["", "", "", ""]) == "unknown"
    assert detect_program_tab_schema(["wrong", "labels", "here"]) == "unknown"


def test_assert_v2_raises_on_v1_with_recovery_hint():
    with pytest.raises(ProgramTabSchemaError, match="v1 13-col schema"):
        assert_program_tab_v2(V1_HEADERS_FOR_TEST, program_name="TPM90")


def test_assert_v2_raises_on_unknown_with_recovery_hint():
    with pytest.raises(ProgramTabSchemaError, match="any known schema"):
        assert_program_tab_v2(["", "", ""], program_name="TPM90")


def test_assert_v2_passes_silently_on_current_schema():
    # Should not raise.
    assert_program_tab_v2(list(DATA_HEADERS), program_name="TPM90")


def test_data_headers_match_v2_schema():
    """v2 schema: 14 cols (added Milestone Link between Milestone? and Notes)."""
    assert NUM_DATA_COLS == 14
    assert DATA_HEADERS[0] == "ID"
    assert DATA_HEADERS[9] == "Status"
    assert DATA_HEADERS[11] == "Milestone?"
    assert DATA_HEADERS[12] == "Milestone Link"
    assert DATA_HEADERS[13] == "Notes"


def test_status_values_match_full_enum():
    assert STATUS_VALUES == [
        "Not Started", "In Progress", "Blocked", "At Risk", "Done", "Cancelled",
    ]


@pytest.mark.parametrize("idx,letter", [
    (1, "A"), (5, "E"), (13, "M"), (14, "N"),
    (26, "Z"), (27, "AA"), (28, "AB"), (52, "AZ"), (53, "BA"),
])
def test_col_letter_one_based(idx, letter):
    assert col_letter(idx) == letter


def test_hex_to_rgb01_known_color():
    rgb = hex_to_rgb01("#4285F4")
    assert rgb["red"] == pytest.approx(66 / 255.0)
    assert rgb["green"] == pytest.approx(133 / 255.0)
    assert rgb["blue"] == pytest.approx(244 / 255.0)


def test_hex_to_rgb01_handles_no_hash():
    assert hex_to_rgb01("FFFFFF") == {"red": 1.0, "green": 1.0, "blue": 1.0}


# ---------- daily timeline headers (calendar days incl. weekends) ----------

def test_make_timeline_days_starts_on_monday_of_input():
    # Wed 2026-05-13 → start should align to Mon 2026-05-11 so week merges line up.
    out = make_timeline_days(date(2026, 5, 13), n=4)
    assert out == [
        date(2026, 5, 11),  # Mon
        date(2026, 5, 12),  # Tue
        date(2026, 5, 13),  # Wed
        date(2026, 5, 14),  # Thu
    ]


def test_make_timeline_days_when_input_is_monday_uses_that_day():
    out = make_timeline_days(date(2026, 5, 11), n=3)
    assert out == [date(2026, 5, 11), date(2026, 5, 12), date(2026, 5, 13)]


def test_make_timeline_days_includes_weekends():
    out = make_timeline_days(date(2026, 5, 11), n=7)
    # Full ISO week Mon-Sun.
    assert out[5] == date(2026, 5, 16)  # Sat
    assert out[6] == date(2026, 5, 17)  # Sun


def test_make_timeline_days_default_length():
    out = make_timeline_days(date(2026, 5, 11))
    assert len(out) == TIMELINE_DAYS


def test_quarter_label_for_each_quarter():
    assert quarter_label(date(2026, 2, 14)) == "Q1 2026"
    assert quarter_label(date(2026, 4, 1)) == "Q2 2026"
    assert quarter_label(date(2026, 7, 31)) == "Q3 2026"
    assert quarter_label(date(2026, 12, 25)) == "Q4 2026"


def test_month_label_is_short_abbreviation():
    assert month_label(date(2026, 5, 11)) == "May"
    assert month_label(date(2026, 1, 1)) == "Jan"
    assert month_label(date(2026, 12, 31)) == "Dec"


def test_grouping_runs_collapses_consecutive_same_label():
    days = [date(2026, 5, 11) + __import__("datetime").timedelta(days=i) for i in range(7)]
    quarters, months, weeks = grouping_runs(days)
    assert quarters == [(0, 6, "Q2 2026")]
    assert months == [(0, 6, "May")]
    # Mon May 11 is in ISO week 20 of 2026.
    assert weeks == [(0, 6, "Wk20")]


def test_grouping_runs_splits_at_month_and_quarter_boundaries():
    days = [
        date(2026, 6, 30),  # Tue, Jun, Q2, Wk27
        date(2026, 7, 1),   # Wed, Jul, Q3, Wk27
        date(2026, 7, 2),   # Thu, Jul, Q3, Wk27
    ]
    quarters, months, weeks = grouping_runs(days)
    assert quarters == [(0, 0, "Q2 2026"), (1, 2, "Q3 2026")]
    assert months == [(0, 0, "Jun"), (1, 2, "Jul")]
    # Same ISO week split at month boundary so the merged week cell doesn't
    # cross the Jun/Jul border.
    assert weeks == [(0, 0, "Wk27"), (1, 2, "Wk27")]


def test_week_num_label_format():
    assert week_num_label(date(2026, 5, 11)) == "Wk20"
    assert week_num_label(date(2026, 1, 5)) == "Wk02"
    assert week_num_label(date(2026, 12, 28)) == "Wk53"


def test_header_rows_constant_is_four():
    # Quarter / month / week-num / day rows; tasks start at row 5.
    assert HEADER_ROWS == 4
    assert WEEK_HEADER_ROW == 3
    assert DAY_HEADER_ROW == 4


# ---------- timeline formula (daily, status-on-first-cell) ----------

def test_timeline_arrayformula_uses_column_and_row_ranges_for_broadcast():
    # Single ARRAYFORMULA covers the full task region by broadcasting
    # column ranges ($F/$G/$J/$L by row) against the day-row range (by col).
    f = timeline_arrayformula(
        first_task_row=5, last_task_row=104,
        first_timeline_col="N", last_timeline_col="EI",
        day_header_row=4,
    )
    assert f.startswith("=ARRAYFORMULA(")
    # Per-row vectors (column ranges).
    assert "$L$5:$L$104" in f
    assert "$F$5:$F$104" in f
    assert "$G$5:$G$104" in f
    assert "$J$5:$J$104" in f
    # Per-col vector (row range, day header).
    assert "$N$4:$EI$4" in f
    # Two branches: milestone diamond, then status text on start day.
    assert '"◆"' in f
    assert '""' in f  # empty-string fallback for non-matching cells


def test_wbs_column_text_format_request_targets_col_a_only():
    """Issue #1: col A (wbs) must be TEXT-formatted so '4.10' isn't truncated."""
    from gantt_lib.schema import wbs_column_text_format_request
    req = wbs_column_text_format_request(sheet_id=42)
    rc = req["repeatCell"]
    assert rc["range"]["sheetId"] == 42
    assert rc["range"]["startColumnIndex"] == 0
    assert rc["range"]["endColumnIndex"] == 1
    assert rc["cell"]["userEnteredFormat"]["numberFormat"]["type"] == "TEXT"
    assert rc["fields"] == "userEnteredFormat.numberFormat"


def test_wrap_strategy_request_sets_overflow_cell_on_timeline():
    req = wrap_strategy_request(sheet_id=42, timeline_cols=5)
    rc = req["repeatCell"]
    assert rc["range"]["sheetId"] == 42
    # Applies to the task region only (header rows above are unaffected).
    assert rc["range"]["startRowIndex"] == HEADER_ROWS
    assert rc["range"]["startColumnIndex"] == 14
    assert rc["range"]["endColumnIndex"] == 19
    assert rc["cell"]["userEnteredFormat"]["wrapStrategy"] == "OVERFLOW_CELL"
    assert rc["fields"] == "userEnteredFormat.wrapStrategy"


# ---------- weekend CF + boundary borders ----------

def test_default_team_color_cf_matches_blank_team_cells():
    """Fallback CF rule fires on rows where team is blank or empty-string
    AND both Start (F) and End (G) are populated AND the cell's day is
    in range. Per-team rules and the weekend rule must be added LATER so
    they take precedence (each addConditionalFormatRule inserts at index 0)."""
    req = default_team_color_cf_request(sheet_id=42, timeline_cols=5)
    rule = req["addConditionalFormatRule"]["rule"]
    rng = rule["ranges"][0]
    assert rng["sheetId"] == 42
    assert rng["startRowIndex"] == HEADER_ROWS
    assert rng["startColumnIndex"] == 14
    assert rng["endColumnIndex"] == 19

    formula = rule["booleanRule"]["condition"]["values"][0]["userEnteredValue"]
    # Must check both ISBLANK and ="" — gspread USER_ENTERED of "" writes
    # an empty string, which is NOT ISBLANK.
    assert "ISBLANK($E" in formula
    assert '$E5=""' in formula
    # Still requires both Start AND End populated, like the per-team rule.
    assert "ISBLANK($F5)" in formula
    assert "ISBLANK($G5)" in formula
    # Day-in-range comparison.
    assert ">=$F5" in formula
    assert "<=$G5" in formula

    # Default color (light pastel blue) — visible but distinct from any
    # team palette entry.
    color = rule["booleanRule"]["format"]["backgroundColor"]
    assert 0 <= color["red"] <= 1
    assert 0 <= color["green"] <= 1
    assert 0 <= color["blue"] <= 1


def test_default_team_color_is_distinct_from_weekend_grey():
    """The default-team color shouldn't visually collide with the weekend
    grey shading — otherwise users can't tell at a glance whether a cell
    is a weekend or a teamless bar."""
    expected = hex_to_rgb01(DEFAULT_TEAM_COLOR)
    # Weekend uses ~0.93 grey-on-grey-on-grey.
    is_grey = (
        abs(expected["red"] - expected["green"]) < 0.02
        and abs(expected["green"] - expected["blue"]) < 0.02
        and expected["red"] > 0.85
    )
    assert not is_grey, (
        f"DEFAULT_TEAM_COLOR={DEFAULT_TEAM_COLOR} resolves to a near-grey "
        f"that would clash with weekend shading"
    )


def test_default_team_color_accepts_custom_hex_override():
    """Future-proofing: callers can override the default by passing a
    different hex_color, e.g. from a _Config-driven palette entry."""
    req = default_team_color_cf_request(
        sheet_id=42, hex_color="#FF5733", timeline_cols=5,
    )
    color = req["addConditionalFormatRule"]["rule"]["booleanRule"]["format"]["backgroundColor"]
    expected = hex_to_rgb01("#FF5733")
    assert color["red"] == expected["red"]
    assert color["green"] == expected["green"]
    assert color["blue"] == expected["blue"]


def test_weekend_cf_excludes_quarter_and_month_header_rows():
    # Weekend shading must NOT apply to rows 1-2 (Q/M merges) — otherwise a
    # merged month cell whose first day lands on a weekend (e.g. Aug 1 2026)
    # would get a grey background. Range starts at the day row.
    req = weekend_cf_request(sheet_id=42, timeline_cols=5)
    rule = req["addConditionalFormatRule"]["rule"]
    rng = rule["ranges"][0]
    assert rng["sheetId"] == 42
    # 0-indexed: row 2 = day row (DAY_HEADER_ROW=3 1-based).
    assert rng["startRowIndex"] == DAY_HEADER_ROW - 1
    assert rng["startColumnIndex"] == 14
    assert rng["endColumnIndex"] == 19
    formula = rule["booleanRule"]["condition"]["values"][0]["userEnteredValue"]
    assert "WEEKDAY" in formula
    assert "$4" in formula  # DAY_HEADER_ROW = 4 with the new week-num row
    color = rule["booleanRule"]["format"]["backgroundColor"]
    assert 0.85 < color["red"] < 1.0
    assert color["red"] == color["green"] == color["blue"]


def test_linked_workbook_only_grey_out_cf_default_columns():
    """Grey-out CF must cover %Complete and Notes by default — the two
    workbook-only fields that have no Linear counterpart and won't sync."""
    req = linked_workbook_only_grey_out_cf_request(sheet_id=42)
    rule = req["addConditionalFormatRule"]["rule"]
    col_starts = sorted(r["startColumnIndex"] for r in rule["ranges"])
    assert col_starts == [COL_PERCENT_IDX, COL_NOTES_IDX]
    for r in rule["ranges"]:
        assert r["sheetId"] == 42
        assert r["startRowIndex"] == HEADER_ROWS  # task region only, not headers
        # Each range is a single column (endColumnIndex = startColumnIndex+1).
        assert r["endColumnIndex"] == r["startColumnIndex"] + 1


def test_linked_workbook_only_grey_out_cf_formula_uses_isformula_on_name():
    """The marker for 'this row is linked to Linear' is a HYPERLINK formula
    in the Name column (col C) — written by the sync flow on link. So the
    CF formula checks ISFORMULA($C5) (anchor row, relative)."""
    req = linked_workbook_only_grey_out_cf_request(sheet_id=42)
    formula = req["addConditionalFormatRule"]["rule"]["booleanRule"]["condition"]["values"][0]["userEnteredValue"]
    assert "ISFORMULA" in formula
    assert f"$C{FIRST_TASK_ROW}" in formula


def test_linked_workbook_only_grey_out_cf_uses_workbook_only_grey():
    req = linked_workbook_only_grey_out_cf_request(sheet_id=42)
    color = req["addConditionalFormatRule"]["rule"]["booleanRule"]["format"]["backgroundColor"]
    assert color == WORKBOOK_ONLY_GREY


def test_linked_workbook_only_grey_out_cf_accepts_extra_columns():
    """extra_col_idxs lets the caller add columns to the greyed set —
    e.g. include Team (col E) until PR3 wires teams via Linear labels."""
    req = linked_workbook_only_grey_out_cf_request(
        sheet_id=42, extra_col_idxs=(COL_TEAM_IDX,),
    )
    col_starts = sorted(r["startColumnIndex"] for r in req["addConditionalFormatRule"]["rule"]["ranges"])
    assert COL_TEAM_IDX in col_starts
    assert COL_PERCENT_IDX in col_starts
    assert COL_NOTES_IDX in col_starts


# ---------- milestone-row grey-out CF ----------


def test_milestone_row_grey_out_cf_targets_duration_percent_milestone_link():
    """Greys out fields that don't apply to milestone rows: Duration (H),
    % Complete (I), Milestone Link (M)."""
    req = milestone_row_grey_out_cf_request(sheet_id=42)
    rule = req["addConditionalFormatRule"]["rule"]
    col_starts = sorted(r["startColumnIndex"] for r in rule["ranges"])
    assert col_starts == sorted([COL_DURATION_IDX, COL_PERCENT_IDX, COL_MILESTONE_LINK_IDX])
    for r in rule["ranges"]:
        assert r["sheetId"] == 42
        assert r["startRowIndex"] == HEADER_ROWS  # task region only
        assert r["endColumnIndex"] == r["startColumnIndex"] + 1


def test_milestone_row_grey_out_cf_triggers_on_milestone_checkbox():
    """The CF formula checks the Milestone? column (col L) for TRUE."""
    req = milestone_row_grey_out_cf_request(sheet_id=42)
    formula = req["addConditionalFormatRule"]["rule"]["booleanRule"]["condition"]["values"][0]["userEnteredValue"]
    # col L = the Milestone? checkbox column.
    assert f"$L{FIRST_TASK_ROW}=TRUE" in formula


def test_milestone_row_grey_out_cf_uses_workbook_only_grey():
    """Same shade as the linked-row grey-out, since both signal 'this
    field doesn't apply / won't sync — probably don't edit'."""
    req = milestone_row_grey_out_cf_request(sheet_id=42)
    color = req["addConditionalFormatRule"]["rule"]["booleanRule"]["format"]["backgroundColor"]
    assert color == WORKBOOK_ONLY_GREY


def test_milestone_row_grey_out_cf_accepts_extra_columns():
    """extra_col_idxs lets callers add columns (e.g. Predecessors) for
    program-specific conventions about what doesn't apply to milestones."""
    from gantt_lib.schema import COL_STATUS_IDX
    req = milestone_row_grey_out_cf_request(
        sheet_id=42, extra_col_idxs=(COL_STATUS_IDX,),
    )
    col_starts = sorted(r["startColumnIndex"] for r in req["addConditionalFormatRule"]["rule"]["ranges"])
    assert COL_STATUS_IDX in col_starts
    assert COL_DURATION_IDX in col_starts


def test_boundary_border_request_spans_full_vertical():
    # Borders go on left+right of the merge column range and extend through
    # the entire tab height (row 0 to total_rows).
    req = boundary_border_request(
        sheet_id=42, start_col_idx=13, end_col_idx_exclusive=51,
        total_rows=103, weight="quarter",
    )
    ub = req["updateBorders"]
    assert ub["range"]["sheetId"] == 42
    assert ub["range"]["startRowIndex"] == 0
    assert ub["range"]["endRowIndex"] == 103
    assert ub["range"]["startColumnIndex"] == 13
    assert ub["range"]["endColumnIndex"] == 51
    assert "left" in ub and "right" in ub
    assert ub["left"]["style"] == ub["right"]["style"]


def test_day_row_format_displays_date_as_day_only():
    # Day cells must STORE actual dates (so WEEKDAY and date comparisons work),
    # but DISPLAY as the day-of-month number only ("11" not "5/11/2026").
    req = day_row_format_request(sheet_id=42, timeline_cols=5)
    rc = req["repeatCell"]
    assert rc["range"]["sheetId"] == 42
    assert rc["range"]["startRowIndex"] == DAY_HEADER_ROW - 1
    assert rc["range"]["endRowIndex"] == DAY_HEADER_ROW
    assert rc["range"]["startColumnIndex"] == 14
    assert rc["range"]["endColumnIndex"] == 19
    fmt = rc["cell"]["userEnteredFormat"]["numberFormat"]
    assert fmt["type"] == "DATE"
    assert fmt["pattern"] == "d"


def test_boundary_border_request_quarter_is_thicker_and_darker():
    month_req = boundary_border_request(42, start_col_idx=13, end_col_idx_exclusive=44,
                                         total_rows=103, weight="month")
    quarter_req = boundary_border_request(42, start_col_idx=13, end_col_idx_exclusive=51,
                                           total_rows=103, weight="quarter")
    m_left = month_req["updateBorders"]["left"]
    q_left = quarter_req["updateBorders"]["left"]
    assert q_left["style"] == "SOLID_MEDIUM"
    assert m_left["style"] == "SOLID"
    assert q_left["color"]["red"] < m_left["color"]["red"]
