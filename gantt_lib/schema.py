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
    "Status", "Predecessors", "Milestone?", "Notes",
]
NUM_DATA_COLS = len(DATA_HEADERS)  # 13

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
COL_TEAM_IDX = 4
COL_STATUS_IDX = 9
COL_MILESTONE_IDX = 11
TIMELINE_FIRST_COL_IDX = 13  # column N

TIMELINE_DAYS = 126          # calendar days of horizon (~18 weeks, Mon-Sun cells)
TIMELINE_COL_PIXELS = 20     # narrow daily columns; status text overflows
DEFAULT_DATA_ROWS = 100      # rows reserved for tasks
STATUS_VALUES = Status.all()

# Visual styling
WEEKEND_BG = {"red": 0.93, "green": 0.93, "blue": 0.93}     # subtle light grey
MONTH_BORDER_COLOR = {"red": 0.75, "green": 0.75, "blue": 0.75}    # lighter grey
QUARTER_BORDER_COLOR = {"red": 0.40, "green": 0.40, "blue": 0.40}  # darker grey


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
