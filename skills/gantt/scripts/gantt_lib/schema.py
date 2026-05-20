"""Per-program tab schema: column layout, daily timeline, formatting requests.

Layout (cols A–M):
    A id | B level | C name | D owner | E team | F start | G end | H duration
    I percent_complete | J status | K predecessors | L milestone | M notes

Timeline (cols N+):
    Row 1: quarter labels    (one merged cell per quarter)   "Q2 2026"
    Row 2: month   labels    (one merged cell per month)     "May"
    Row 3: week-num labels   (one merged cell per ISO week)  "Wk20"
    Row 4: day labels        (one cell per calendar day)     "11"
    Row 5+: tasks. Each task row writes status text only into its first
            colored cell; conditional formatting paints the rest based on
            the data row's start/end and team — letting the status text
            overflow visually into the adjacent (empty) colored cells.
"""
from __future__ import annotations

from datetime import date, timedelta

from .model import Status

PROGRAM_TAB_PREFIX = "P_"

DATA_HEADERS = [
    "ID", "Level", "Name", "Owner", "Team",
    "Start", "End", "Duration", "% Complete",
    "Status", "Predecessors", "Milestone?", "Milestone Link", "Notes",
]
NUM_DATA_COLS = len(DATA_HEADERS)  # 14

# v1 (pre-PR2b) schema header — 13 data cols, no Milestone Link.
# Kept as a constant so the detection path can recognize old tabs and
# raise a descriptive error instead of silently writing v2 rows into a
# v1 layout (which would clobber the first timeline column + shift Notes
# data into the new Milestone Link slot).
_V1_DATA_HEADERS = [
    "ID", "Level", "Name", "Owner", "Team",
    "Start", "End", "Duration", "% Complete",
    "Status", "Predecessors", "Milestone?", "Notes",
]


class ProgramTabSchemaError(ValueError):
    """Raised when a program tab's header row doesn't match the current
    schema. Carries a recovery hint pointing at the migration path."""


def detect_program_tab_schema(header_row: list[str]) -> str:
    """Return 'v2' (current), 'v1' (pre-PR2b 13-col), or 'unknown'.

    Compares only the data-region columns (first NUM_DATA_COLS cells); any
    timeline-column headers in the same row are ignored.
    """
    if not header_row:
        return "unknown"
    head = [c.strip() for c in header_row]
    if head[:NUM_DATA_COLS] == DATA_HEADERS:
        return "v2"
    if head[:len(_V1_DATA_HEADERS)] == _V1_DATA_HEADERS:
        return "v1"
    return "unknown"


def assert_program_tab_v2(header_row: list[str], program_name: str = "<program>") -> None:
    """Raise ProgramTabSchemaError if `header_row` isn't the current
    schema. Prevents silent corruption when a v1 tab gets v2 writes
    (Milestone Link would land in the first timeline col, Notes would
    shift to col M, etc.)."""
    version = detect_program_tab_schema(header_row)
    if version == "v2":
        return
    if version == "v1":
        raise ProgramTabSchemaError(
            f"Program tab for {program_name!r} is on the old v1 13-col schema "
            f"(no Milestone Link column). Cannot safely apply v2 reads/writes — "
            f"the column shift would corrupt the first timeline column. "
            "Recover by running the program-tab schema migration (see "
            "task #102) or, for unsynced programs, recreating the tab "
            "via `gantt program new`."
        )
    raise ProgramTabSchemaError(
        f"Program tab for {program_name!r} header row {header_row!r} does not "
        f"match any known schema (expected v2: {DATA_HEADERS!r} or "
        f"v1: {_V1_DATA_HEADERS!r}). Inspect the tab and either restore the "
        "header row or recreate the program."
    )

# Sheet structure: 4 grouping/header rows above the task region.
HEADER_ROWS = 4
QUARTER_HEADER_ROW = 1
MONTH_HEADER_ROW = 2
WEEK_HEADER_ROW = 3
DAY_HEADER_ROW = 4
FIRST_TASK_ROW = HEADER_ROWS + 1  # 5

# Column letters (1-based: A=1)
COL_TEAM_LETTER = "E"
COL_START_LETTER = "F"
COL_END_LETTER = "G"
COL_STATUS_LETTER = "J"
COL_MILESTONE_LETTER = "L"

# Sheets API uses 0-based indices in batch_update payloads.
COL_NAME_IDX = 2
COL_OWNER_IDX = 3
COL_TEAM_IDX = 4
COL_START_IDX = 5
COL_END_IDX = 6
COL_DURATION_IDX = 7
COL_PERCENT_IDX = 8
COL_STATUS_IDX = 9
COL_PREDECESSORS_IDX = 10
COL_MILESTONE_IDX = 11
COL_MILESTONE_LINK_IDX = 12
COL_NOTES_IDX = 13
TIMELINE_FIRST_COL_IDX = 14  # column O

# Letter form for formulas.
COL_NAME_LETTER = "C"
COL_MILESTONE_LINK_LETTER = "M"

TIMELINE_DAYS = 126          # calendar days of horizon (~18 weeks, Mon-Sun cells)
TIMELINE_COL_PIXELS = 20     # narrow daily columns; status text overflows
DEFAULT_DATA_ROWS = 100      # rows reserved for tasks
STATUS_VALUES = Status.all()

# Visual styling
WEEKEND_BG = {"red": 0.93, "green": 0.93, "blue": 0.93}     # subtle light grey
# Slightly darker than weekend grey so the "this won't sync" hint
# reads as intentional muting, not just another weekend cell.
WORKBOOK_ONLY_GREY = {"red": 0.88, "green": 0.88, "blue": 0.88}
# Distinctly darker than WORKBOOK_ONLY_GREY so milestone-row "doesn't
# apply" cells read as a stronger signal than the linked-row hint.
MILESTONE_ROW_GREY = {"red": 0.6, "green": 0.6, "blue": 0.6}
MONTH_BORDER_COLOR = {"red": 0.75, "green": 0.75, "blue": 0.75}    # lighter grey
QUARTER_BORDER_COLOR = {"red": 0.40, "green": 0.40, "blue": 0.40}  # darker grey
# Default team color: applied to timeline bars on rows whose team cell
# is blank. Low-saturation pastel blue — visible enough to read as "this
# task has a scheduled bar" but distinct from any team palette entry so
# the user can tell at a glance that the row is unassigned.
DEFAULT_TEAM_COLOR = "#CFE2F3"


def program_tab_name(name: str) -> str:
    return f"{PROGRAM_TAB_PREFIX}{name}"


def col_letter(index_1based: int) -> str:
    """Convert 1-based column index to A1 letter. 1->A, 26->Z, 27->AA."""
    s = ""
    n = index_1based
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def hex_to_rgb01(hex_str: str) -> dict[str, float]:
    """#4285F4 → {red: 0.259, green: 0.522, blue: 0.957}."""
    h = hex_str.lstrip("#")
    return {
        "red": int(h[0:2], 16) / 255.0,
        "green": int(h[2:4], 16) / 255.0,
        "blue": int(h[4:6], 16) / 255.0,
    }


# ---------- timeline date computation ----------

def _monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def make_timeline_days(start: date, n: int = TIMELINE_DAYS) -> list[date]:
    """Return n consecutive calendar dates starting at the Monday of/before `start`.

    Aligning to Monday means every full ISO week becomes a 7-cell run, so the
    week-row merges line up with the underlying day cells. Weekends are visible
    cells (shaded grey via CF); they don't affect bars because cascade always
    lands End on a working day.
    """
    first = _monday_of(start)
    return [first + timedelta(days=i) for i in range(n)]


def quarter_label(d: date) -> str:
    q = (d.month - 1) // 3 + 1
    return f"Q{q} {d.year}"


def month_label(d: date) -> str:
    return d.strftime("%b")


def week_num_label(d: date) -> str:
    """ISO annual week number, zero-padded: 'Wk01', 'Wk20', 'Wk52'."""
    return f"Wk{d.isocalendar()[1]:02d}"


def grouping_runs(days: list[date]) -> tuple[
    list[tuple[int, int, str]],
    list[tuple[int, int, str]],
    list[tuple[int, int, str]],
]:
    """Collapse consecutive same-quarter / same-month / same-week days into runs.

    Returns (quarter_runs, month_runs, week_runs). Each run is
    (start_idx, end_idx, label). Used to build mergeCells API requests.

    Week runs additionally break at month boundaries — otherwise a merged
    "Wk27" cell spanning Jun 29-Jul 5 would be cut through by the full-vertical
    month-boundary border at Jul 1.
    """
    if not days:
        return [], [], []

    def simple_runs(key_fn, label_fn):
        out: list[tuple[int, int, str]] = []
        start = 0
        for i in range(1, len(days)):
            if key_fn(days[i]) != key_fn(days[i - 1]):
                out.append((start, i - 1, label_fn(days[start])))
                start = i
        out.append((start, len(days) - 1, label_fn(days[start])))
        return out

    quarter_runs = simple_runs(quarter_label, quarter_label)
    month_runs = simple_runs(month_label, month_label)

    # Week runs: break at month boundary OR at ISO week boundary.
    week_runs: list[tuple[int, int, str]] = []
    start = 0
    for i in range(1, len(days)):
        d, prev = days[i], days[i - 1]
        if _monday_of(d) != _monday_of(prev) or d.month != prev.month:
            week_runs.append((start, i - 1, week_num_label(days[start])))
            start = i
    week_runs.append((start, len(days) - 1, week_num_label(days[start])))

    return quarter_runs, month_runs, week_runs


# ---------- timeline ARRAYFORMULA ----------

def timeline_arrayformula(
    first_task_row: int,
    last_task_row: int,
    first_timeline_col: str,
    last_timeline_col: str,
    day_header_row: int = DAY_HEADER_ROW,
) -> str:
    """Single ARRAYFORMULA that fills the entire task timeline region.

    Broadcasts a per-row column vector ($F/$G/$J/$L) against a per-col row
    vector (day header) to produce a 2D array of cell contents:
      - milestone row → "◆" when the cell's day == end day
      - else → status text when the cell's day == start day
      - else → "" (cell stays visually empty; CF paints the bar)

    Performance: ONE formula instead of (rows × cols) per-cell formulas, so
    Sheets recomputes the whole timeline in a single pass. New tasks added
    inside the row range are rendered automatically without writing additional
    formulas — `gantt task add` only needs to fill cols A:M.
    """
    f_range = f"$F${first_task_row}:$F${last_task_row}"
    g_range = f"$G${first_task_row}:$G${last_task_row}"
    j_range = f"$J${first_task_row}:$J${last_task_row}"
    l_range = f"$L${first_task_row}:$L${last_task_row}"
    day_range = f"${first_timeline_col}${day_header_row}:${last_timeline_col}${day_header_row}"
    return (
        f'=ARRAYFORMULA('
        f'IF({l_range}=TRUE,'
        f'IF({day_range}={g_range},"◆",""),'
        f'IF({day_range}={f_range},{j_range},"")))'
    )


# ---------- Sheets-API request builders ----------

def _max_data_row(rows: int = DEFAULT_DATA_ROWS) -> int:
    """0-based exclusive end-row for the task region, used in API ranges."""
    return HEADER_ROWS + rows  # e.g. 4 + 100 = 104


def status_validation_request(sheet_id: int) -> dict:
    return {
        "setDataValidation": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": HEADER_ROWS,
                "endRowIndex": _max_data_row(),
                "startColumnIndex": COL_STATUS_IDX,
                "endColumnIndex": COL_STATUS_IDX + 1,
            },
            "rule": {
                "condition": {
                    "type": "ONE_OF_LIST",
                    "values": [{"userEnteredValue": v} for v in STATUS_VALUES],
                },
                "showCustomUi": True,
                "strict": True,
            },
        }
    }


def milestone_checkbox_request(sheet_id: int) -> dict:
    return {
        "setDataValidation": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": HEADER_ROWS,
                "endRowIndex": _max_data_row(),
                "startColumnIndex": COL_MILESTONE_IDX,
                "endColumnIndex": COL_MILESTONE_IDX + 1,
            },
            "rule": {
                "condition": {"type": "BOOLEAN"},
                "showCustomUi": False,
            },
        }
    }


def team_color_cf_request(
    sheet_id: int,
    team_name: str,
    hex_color: str,
    timeline_cols: int = TIMELINE_DAYS,
) -> dict:
    """Conditional formatting: paint timeline cells where the data row's team
    matches and the cell's day falls within [Start, End].

    The CF formula is evaluated as if anchored at top-left of the rule's range
    (column N, row {FIRST_TASK_ROW}). Relative references shift per-cell.
    """
    n_letter = col_letter(TIMELINE_FIRST_COL_IDX + 1)  # "N"
    anchor_row = FIRST_TASK_ROW
    cell_day = f"{n_letter}${DAY_HEADER_ROW}"
    formula = (
        f'=AND(${COL_TEAM_LETTER}{anchor_row}="{team_name}",'
        f'NOT(ISBLANK($F{anchor_row})),'
        f'NOT(ISBLANK($G{anchor_row})),'
        f'{cell_day}>=$F{anchor_row},'
        f'{cell_day}<=$G{anchor_row})'
    )
    return {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [{
                    "sheetId": sheet_id,
                    "startRowIndex": HEADER_ROWS,
                    "endRowIndex": _max_data_row(),
                    "startColumnIndex": TIMELINE_FIRST_COL_IDX,
                    "endColumnIndex": TIMELINE_FIRST_COL_IDX + timeline_cols,
                }],
                "booleanRule": {
                    "condition": {
                        "type": "CUSTOM_FORMULA",
                        "values": [{"userEnteredValue": formula}],
                    },
                    "format": {"backgroundColor": hex_to_rgb01(hex_color)},
                },
            },
            "index": 0,
        }
    }


def default_team_color_cf_request(
    sheet_id: int,
    hex_color: str = DEFAULT_TEAM_COLOR,
    timeline_cols: int = TIMELINE_DAYS,
) -> dict:
    """Conditional formatting: paint timeline cells where the data row's
    team is BLANK (no team assigned) and the cell's day falls within
    [Start, End].

    This is the fallback rule applied alongside per-team rules so that
    tasks without a team — including everything pulled from Linear,
    which doesn't carry a per-issue team field — still render with bars
    on the timeline. Per-team rules should be inserted at higher
    priority (i.e. added LATER) so a team assignment overrides the
    default color.

    The formula handles both truly-empty cells (ISBLANK) and cells
    holding an empty string (gspread USER_ENTERED of "" lands as "",
    not blank).
    """
    n_letter = col_letter(TIMELINE_FIRST_COL_IDX + 1)  # "N"
    anchor_row = FIRST_TASK_ROW
    cell_day = f"{n_letter}${DAY_HEADER_ROW}"
    formula = (
        f'=AND('
        f'OR(ISBLANK(${COL_TEAM_LETTER}{anchor_row}),'
        f'${COL_TEAM_LETTER}{anchor_row}=""),'
        f'NOT(ISBLANK($F{anchor_row})),'
        f'NOT(ISBLANK($G{anchor_row})),'
        f'{cell_day}>=$F{anchor_row},'
        f'{cell_day}<=$G{anchor_row})'
    )
    return {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [{
                    "sheetId": sheet_id,
                    "startRowIndex": HEADER_ROWS,
                    "endRowIndex": _max_data_row(),
                    "startColumnIndex": TIMELINE_FIRST_COL_IDX,
                    "endColumnIndex": TIMELINE_FIRST_COL_IDX + timeline_cols,
                }],
                "booleanRule": {
                    "condition": {
                        "type": "CUSTOM_FORMULA",
                        "values": [{"userEnteredValue": formula}],
                    },
                    "format": {"backgroundColor": hex_to_rgb01(hex_color)},
                },
            },
            "index": 0,
        }
    }


def weekend_cf_request(sheet_id: int, timeline_cols: int = TIMELINE_DAYS) -> dict:
    """Conditional formatting: shade weekend (Sat/Sun) timeline cells light grey.

    Range starts at the DAY row — the Q and M merged cells in rows 1-2 are
    excluded so a month whose first day is a weekend (Aug 1 2026 is Sat)
    doesn't get its merged cell shaded grey.
    """
    n_letter = col_letter(TIMELINE_FIRST_COL_IDX + 1)  # "N"
    cell_day = f"{n_letter}${DAY_HEADER_ROW}"
    formula = f"=OR(WEEKDAY({cell_day},2)=6,WEEKDAY({cell_day},2)=7)"
    return {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [{
                    "sheetId": sheet_id,
                    "startRowIndex": DAY_HEADER_ROW - 1,  # day row + tasks; excludes Q/M
                    "endRowIndex": _max_data_row(),
                    "startColumnIndex": TIMELINE_FIRST_COL_IDX,
                    "endColumnIndex": TIMELINE_FIRST_COL_IDX + timeline_cols,
                }],
                "booleanRule": {
                    "condition": {
                        "type": "CUSTOM_FORMULA",
                        "values": [{"userEnteredValue": formula}],
                    },
                    "format": {"backgroundColor": WEEKEND_BG},
                },
            },
            "index": 0,
        }
    }


def milestone_row_grey_out_cf_request(
    sheet_id: int,
    extra_col_idxs: tuple[int, ...] = (),
) -> dict:
    """Conditional formatting: grey out fields that don't round-trip with
    Linear on milestone rows.

    Linear's `ProjectMilestone` exposes only three workbook-syncable
    fields: `name` (col C — Name), `targetDate` (col G — End), and
    `description` (col N — Notes, not yet wired). Everything else on a
    milestone row is workbook-local — no Linear counterpart exists. The
    grey signals "edit if you like, but Linear has no opinion."

    Sync protection: the merge engine treats the corresponding fields as
    workbook-authoritative on milestone rows (see
    `linear.merge.MILESTONE_WORKBOOK_PROTECTED`). Whatever you put in a
    greyed cell stays — Linear can never clear it.

    Greyed columns:
      - `Owner` (col D)        — no `assignee` on milestones
      - `Team`  (col E)        — no `labels` on milestones
      - `Start` (col F)        — no start field on milestones
      - `Duration` (col H)     — no `estimate` on milestones
      - `% Complete` (col I)   — milestone is hit-or-not, not %
      - `Status` (col J)       — derived from member issues in Linear
      - `Milestone Link` (col M) — milestones can't nest
      - `Notes` (col N)        — `description` sync not wired yet

    Predecessors (col K) is intentionally NOT greyed: it doubles as the
    milestone-membership editor — `<wbs>FS` entries become `issue.milestone`
    membership pushes on next sync.

    `extra_col_idxs` lets callers extend the greyed set.
    """
    cols = (
        COL_OWNER_IDX,
        COL_TEAM_IDX,
        COL_START_IDX,
        COL_DURATION_IDX,
        COL_PERCENT_IDX,
        COL_STATUS_IDX,
        COL_MILESTONE_LINK_IDX,
        COL_NOTES_IDX,
        *extra_col_idxs,
    )
    # Sheets stores the checkbox as the boolean TRUE; `=$L5=TRUE` matches.
    formula = f"=${col_letter(COL_MILESTONE_IDX + 1)}{FIRST_TASK_ROW}=TRUE"
    ranges = [
        {
            "sheetId": sheet_id,
            "startRowIndex": HEADER_ROWS,
            "endRowIndex": _max_data_row(),
            "startColumnIndex": col_idx,
            "endColumnIndex": col_idx + 1,
        }
        for col_idx in cols
    ]
    return {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": ranges,
                "booleanRule": {
                    "condition": {
                        "type": "CUSTOM_FORMULA",
                        "values": [{"userEnteredValue": formula}],
                    },
                    "format": {"backgroundColor": MILESTONE_ROW_GREY},
                },
            },
            "index": 0,
        }
    }


def linked_workbook_only_grey_out_cf_request(
    sheet_id: int,
    extra_col_idxs: tuple[int, ...] = (),
) -> dict:
    """Conditional formatting: grey out cells in workbook-only fields
    (`% Complete`, `Notes`) on rows that are linked to Linear.

    Linked rows are detected by checking whether the Name cell (column C)
    contains a formula — the linear-sync flow writes a `=HYPERLINK(...)`
    formula into the Name column whenever it links a workbook row to a
    Linear issue, so `ISFORMULA($C{row})` is a reliable marker.

    The visual hint signals to the user "this field is intentionally
    workbook-local — editing it has no effect on Linear." Cells stay
    fully editable; this is a cosmetic cue only.

    Greyed columns (the workbook-only fields on any linked row):
      - `Start` (col F)       — Linear's `startedAt` is derived (set when
        the issue enters a `started` state); workbook pushes are no-ops
      - `% Complete` (col I)  — Linear doesn't carry %complete
      - `Notes` (col N)       — `issue.description` sync not wired yet

    `extra_col_idxs` lets callers extend the greyed set.
    """
    cols = (COL_START_IDX, COL_PERCENT_IDX, COL_NOTES_IDX, *extra_col_idxs)
    formula = f"=ISFORMULA(${COL_NAME_LETTER}{FIRST_TASK_ROW})"
    ranges = [
        {
            "sheetId": sheet_id,
            "startRowIndex": HEADER_ROWS,
            "endRowIndex": _max_data_row(),
            "startColumnIndex": col_idx,
            "endColumnIndex": col_idx + 1,
        }
        for col_idx in cols
    ]
    return {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": ranges,
                "booleanRule": {
                    "condition": {
                        "type": "CUSTOM_FORMULA",
                        "values": [{"userEnteredValue": formula}],
                    },
                    "format": {"backgroundColor": WORKBOOK_ONLY_GREY},
                },
            },
            "index": 0,
        }
    }


UNRESOLVED_OWNER_TEXT_COLOR = {"red": 0.55, "green": 0.55, "blue": 0.55}


def unresolved_owner_marker_cf_request(sheet_id: int, program_name: str) -> dict:
    """Conditional formatting: italicize + dim the text color on Owner
    cells (col D) where the workbook value didn't resolve to a Linear
    workspace user during the last sync.

    Drives off the `sidecar_owner_resolved` column in `_LinearSync`
    (PR-H). The formula uses INDEX/MATCH to find the row for the
    current WBS *within this program* (filtering by program prevents
    same-WBS collisions across multiple programs) and checks the flag.

    Cell text becomes italic + grey when:
      1. The Owner cell is not blank
      2. The row's WBS has a matching row in `_LinearSync` for this program
      3. That row's `sidecar_owner_resolved` is exactly "FALSE"

    Rows without an Owner value, unlinked rows (no SyncLink), and
    resolved rows ("TRUE") all bypass the formatting. Empty
    `sidecar_owner_resolved` ("") also bypasses — that's the "validation
    disabled" or "Owner blank at last sync" case.

    `program_name` is baked into the formula at apply time so the
    per-tab CF correctly scopes lookups.
    """
    formula = (
        f"=AND("
        f"NOT(ISBLANK($D{FIRST_TASK_ROW})), "
        f'IFERROR(INDEX(_LinearSync!$T:$T, MATCH(1, '
        f'(_LinearSync!$A:$A="{program_name}")*'
        f"(_LinearSync!$B:$B=$A{FIRST_TASK_ROW}), 0))=\"FALSE\", FALSE)"
        f")"
    )
    return {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [{
                    "sheetId": sheet_id,
                    "startRowIndex": HEADER_ROWS,
                    "endRowIndex": _max_data_row(),
                    "startColumnIndex": COL_OWNER_IDX,
                    "endColumnIndex": COL_OWNER_IDX + 1,
                }],
                "booleanRule": {
                    "condition": {
                        "type": "CUSTOM_FORMULA",
                        "values": [{"userEnteredValue": formula}],
                    },
                    "format": {
                        "textFormat": {
                            "italic": True,
                            "foregroundColor": UNRESOLVED_OWNER_TEXT_COLOR,
                        },
                    },
                },
            },
            "index": 0,
        }
    }


def boundary_border_request(sheet_id: int, start_col_idx: int,
                             end_col_idx_exclusive: int, total_rows: int,
                             weight: str = "month") -> dict:
    """Outline a column range with left+right borders that span the full tab.

    `weight="month"` → SOLID light grey; `weight="quarter"` → SOLID_MEDIUM
    darker grey. Issued AFTER the merge so the borders attach to the merged
    cell perimeter; spans rows 0 to total_rows so the line continues through
    the task region.

    At quarter boundaries (which are also month boundaries) the heavier
    quarter border overwrites the lighter month border — so issue month
    requests first, quarter requests second.
    """
    style = "SOLID_MEDIUM" if weight == "quarter" else "SOLID"
    color = QUARTER_BORDER_COLOR if weight == "quarter" else MONTH_BORDER_COLOR
    border = {"style": style, "color": color}
    return {
        "updateBorders": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 0,
                "endRowIndex": total_rows,
                "startColumnIndex": start_col_idx,
                "endColumnIndex": end_col_idx_exclusive,
            },
            "left": border,
            "right": border,
        }
    }


def wbs_column_text_format_request(sheet_id: int) -> dict:
    """Force col A (WBS ids) to TEXT format so values like '4.10' / '6.10' aren't
    silently coerced to numbers and truncated to '4.1' / '6.1' (issue #1).

    Repro: write '4.10' with USER_ENTERED to a DEFAULT-formatted col A → Sheets
    parses as float 4.1 → stored as 4.1 → collides with the existing 4.1 row.
    TEXT format on col A keeps the string verbatim.
    """
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startColumnIndex": 0, "endColumnIndex": 1,
            },
            "cell": {
                "userEnteredFormat": {
                    "numberFormat": {"type": "TEXT"},
                }
            },
            "fields": "userEnteredFormat.numberFormat",
        }
    }


def freeze_layout_request(sheet_id: int, frozen_cols: int = 3) -> dict:
    return {
        "updateSheetProperties": {
            "properties": {
                "sheetId": sheet_id,
                "gridProperties": {
                    "frozenRowCount": HEADER_ROWS,
                    "frozenColumnCount": frozen_cols,
                },
            },
            "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
        }
    }


def bold_header_rows_request(sheet_id: int, end_col: int) -> dict:
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 0,
                "endRowIndex": HEADER_ROWS,
                "startColumnIndex": 0,
                "endColumnIndex": end_col,
            },
            "cell": {
                "userEnteredFormat": {
                    "textFormat": {"bold": True},
                    "horizontalAlignment": "CENTER",
                },
            },
            "fields": "userEnteredFormat.textFormat.bold,userEnteredFormat.horizontalAlignment",
        }
    }


def wrap_strategy_request(sheet_id: int, timeline_cols: int = TIMELINE_DAYS) -> dict:
    """Set wrapStrategy=OVERFLOW_CELL on the timeline area in the task region.

    Lets status text in the first cell of a bar overflow visually into the
    adjacent (empty-string) cells produced by the ARRAYFORMULA.
    """
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": HEADER_ROWS,
                "endRowIndex": _max_data_row(),
                "startColumnIndex": TIMELINE_FIRST_COL_IDX,
                "endColumnIndex": TIMELINE_FIRST_COL_IDX + timeline_cols,
            },
            "cell": {"userEnteredFormat": {"wrapStrategy": "OVERFLOW_CELL"}},
            "fields": "userEnteredFormat.wrapStrategy",
        }
    }


def day_row_format_request(sheet_id: int, timeline_cols: int = TIMELINE_DAYS) -> dict:
    """Format the day-row cells to display the day-of-month only.

    Day cells STORE actual dates so WEEKDAY (weekend CF) and >= comparisons
    against task start/end dates work correctly. The pattern "d" displays just
    the day number ("11", "12", …) so the visual stays compact.
    """
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": DAY_HEADER_ROW - 1,
                "endRowIndex": DAY_HEADER_ROW,
                "startColumnIndex": TIMELINE_FIRST_COL_IDX,
                "endColumnIndex": TIMELINE_FIRST_COL_IDX + timeline_cols,
            },
            "cell": {
                "userEnteredFormat": {
                    "numberFormat": {"type": "DATE", "pattern": "d"},
                    "horizontalAlignment": "CENTER",
                },
            },
            "fields": "userEnteredFormat.numberFormat,userEnteredFormat.horizontalAlignment",
        }
    }


def timeline_column_width_request(sheet_id: int, pixels: int = TIMELINE_COL_PIXELS,
                                   timeline_cols: int = TIMELINE_DAYS) -> dict:
    return {
        "updateDimensionProperties": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "COLUMNS",
                "startIndex": TIMELINE_FIRST_COL_IDX,
                "endIndex": TIMELINE_FIRST_COL_IDX + timeline_cols,
            },
            "properties": {"pixelSize": pixels},
            "fields": "pixelSize",
        }
    }


def clear_task_bold_request(sheet_id: int, num_data_rows: int = DEFAULT_DATA_ROWS) -> dict:
    """Set bold=False across all task rows (cols A-M) — used to wipe prior
    critical-path highlighting before applying a fresh set."""
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": HEADER_ROWS,
                "endRowIndex": HEADER_ROWS + num_data_rows,
                "startColumnIndex": 0,
                "endColumnIndex": NUM_DATA_COLS,
            },
            "cell": {"userEnteredFormat": {"textFormat": {"bold": False}}},
            "fields": "userEnteredFormat.textFormat.bold",
        }
    }


def bold_row_request(sheet_id: int, row_idx_0based: int) -> dict:
    """Set bold=True on cols A-M for a single row — used to highlight a critical-path task."""
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": row_idx_0based,
                "endRowIndex": row_idx_0based + 1,
                "startColumnIndex": 0,
                "endColumnIndex": NUM_DATA_COLS,
            },
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
            "fields": "userEnteredFormat.textFormat.bold",
        }
    }


def add_row_group_request(sheet_id: int, start_row_idx: int,
                           end_row_idx_exclusive: int) -> dict:
    """Add a Sheets row dimension group over the given 0-based row range.

    Sheets infers depth from containment — issue an outer group first, then a
    nested group on a sub-range, and Sheets renders the +/− toggle hierarchy.
    """
    return {
        "addDimensionGroup": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "ROWS",
                "startIndex": start_row_idx,
                "endIndex": end_row_idx_exclusive,
            }
        }
    }


def delete_row_group_request(sheet_id: int, start_row_idx: int,
                              end_row_idx_exclusive: int) -> dict:
    """Decrement one depth level of row grouping over the given 0-based range.

    Used to wipe existing groups before re-applying a fresh structure on
    `gantt recalc`. To fully clear a depth-N group, call once per depth.
    """
    return {
        "deleteDimensionGroup": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "ROWS",
                "startIndex": start_row_idx,
                "endIndex": end_row_idx_exclusive,
            }
        }
    }


def merge_cells_request(sheet_id: int, row_idx: int, start_col_idx: int,
                        end_col_idx_exclusive: int) -> dict:
    return {
        "mergeCells": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": row_idx,
                "endRowIndex": row_idx + 1,
                "startColumnIndex": start_col_idx,
                "endColumnIndex": end_col_idx_exclusive,
            },
            "mergeType": "MERGE_ALL",
        }
    }


# ---------- _Config readers ----------

def read_team_palette_from_config(config_ws) -> list[tuple[str, str]]:
    rows = config_ws.get_values("F2:G50")
    pairs: list[tuple[str, str]] = []
    for r in rows:
        if len(r) >= 2 and r[0].strip() and r[1].strip():
            pairs.append((r[0].strip(), r[1].strip()))
    return pairs


def read_holidays_from_config(config_ws) -> set[date]:
    rows = config_ws.get_values("C2:C50")
    out: set[date] = set()
    for r in rows:
        if r and r[0].strip():
            try:
                out.add(date.fromisoformat(r[0].strip()))
            except ValueError:
                continue
    return out
