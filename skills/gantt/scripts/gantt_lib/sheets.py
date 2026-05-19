"""Thin Sheets I/O layer over gspread for program tabs.

Operates on a `gspread.Worksheet` (the program tab) and translates between
in-memory `Task` objects and Sheet rows. Pure I/O — no business logic;
schema constants and row layouts come from `gantt_lib.schema`.
"""
from __future__ import annotations

from typing import Optional

from . import schema
from .model import Task

# Three header rows (quarters/months/weeks); tasks start at row 4.
FIRST_DATA_ROW = schema.FIRST_TASK_ROW

def indent_prefix(level: int) -> str:
    """Tree-style level prefix applied to col C on write.

      L1 → ''         (no prefix)
      L2 → '-- '      (2 dashes + space)
      L3 → '---- '    (4 dashes + space)
      L4 → '------ '  (6 dashes + space)

    Stripped on read in Task.from_row via lstrip(' -').
    """
    if level <= 1:
        return ""
    return "--" * (level - 1) + " "


def _hyperlink_formula(url: str, display_text: str) -> str:
    """Build a Sheets =HYPERLINK formula. Escapes double quotes per Sheets
    string-literal rules (`"` → `""`) so titles with quotes don't break
    the formula parser."""
    escaped_url = url.replace('"', '""')
    escaped_text = display_text.replace('"', '""')
    return f'=HYPERLINK("{escaped_url}", "{escaped_text}")'


def _indented_row(task: Task) -> list[str]:
    """Build the data row with col C name prefixed by the task's level indent.

    When the task carries a `linear_url` (set by the Linear-pull path),
    col C is written as a `=HYPERLINK(linear_url, indented_title)` formula
    so the cell is clickable. Otherwise it's the plain indented title.
    """
    row = task.to_row()
    indented = indent_prefix(task.level) + task.name
    if task.linear_url:
        row[2] = _hyperlink_formula(task.linear_url, indented)
    else:
        row[2] = indented
    return row


def read_program_tasks(ws) -> list[Task]:
    """Read the editable region (cols A–M) and return a list of Task objects.

    A row is treated as empty (and skipped) when column A (id) is blank.
    This avoids false positives from checkbox validation: applying a
    checkbox to col L causes Sheets to populate every cell with "FALSE",
    so a "row has any non-empty cell" check would never skip anything.
    """
    return [task for task, _row, _raw in read_program_tasks_with_rows(ws)]


def read_program_tasks_with_rows(ws) -> list[tuple[Task, int, list[str]]]:
    """Same as read_program_tasks but pairs each task with its 1-based row number
    and the raw cell values for that row.

    The raw row lets cmd_recalc compare the displayed col C (which may have
    leading-space indentation already applied — or not, for tasks added before
    the indent feature shipped) against the expected indented form, and only
    write when they differ.

    Validates the tab's header row before reading. Raises
    `ProgramTabSchemaError` when the tab is on an older schema (v1, no
    Milestone Link column) — prevents silent data corruption from the
    PR2b column shift.
    """
    header_row = ws.get_values("A4:O4")  # row 4 = data header row (post-PR2b: 14 cols + extras)
    header_cells = header_row[0] if header_row else []
    schema.assert_program_tab_v2(header_cells, program_name=getattr(ws, "title", "<unknown>"))

    last_col = schema.col_letter(schema.NUM_DATA_COLS)
    rng = ws.get_values(f"A{FIRST_DATA_ROW}:{last_col}")
    out: list[tuple[Task, int, list[str]]] = []
    for offset, row in enumerate(rng):
        if not row or not row[0].strip():
            continue
        out.append((Task.from_row(row), FIRST_DATA_ROW + offset, list(row)))
    return out


def find_task_row(ws, task_id: str) -> Optional[int]:
    """Return 1-based row number for the task with the given id, or None."""
    col_a = ws.col_values(1)  # 1-based logical, 0-based list
    for idx, val in enumerate(col_a, start=1):
        if idx < FIRST_DATA_ROW:
            continue  # skip header rows
        if val.strip() == task_id:
            return idx
    return None


def _next_empty_row(ws) -> int:
    """First row in col A with no value (1-based). Always at or after FIRST_DATA_ROW."""
    col_a = ws.col_values(1)
    return max(len(col_a) + 1, FIRST_DATA_ROW)


def append_task(ws, task: Task) -> int:
    """Append `task` as a new row (cols A-M only). Returns the row number.

    The timeline cells (cols N+) are filled by a single ARRAYFORMULA at
    {first task row, col N} placed at tab creation — so adding a task only
    requires writing its 13 data cells.
    """
    row = _next_empty_row(ws)
    ws.update(
        range_name=f"A{row}",
        values=[_indented_row(task)],
        value_input_option="USER_ENTERED",
    )
    return row


def update_task_data(ws, row: int, task: Task) -> None:
    """Overwrite cols A..M for the given row with the task's data fields.

    Does NOT touch the timeline formulas in cols N+, since they reference
    this row by absolute column / relative-row and don't need rewriting.
    """
    last_col = schema.col_letter(schema.NUM_DATA_COLS)
    ws.update(
        range_name=f"A{row}:{last_col}{row}",
        values=[_indented_row(task)],
        value_input_option="USER_ENTERED",
    )


def find_child_insertion_row(pairs: list[tuple[Task, int, list[str]]],
                              parent_id: str) -> Optional[int]:
    """1-based row where a new child of `parent_id` should be inserted.

    Inserts after the parent's last existing direct/indirect descendant so
    children stay contiguous beneath their parent in WBS order. Returns None
    if `parent_id` is not in `pairs`.

    Caveat: assumes existing children are already contiguous after the parent.
    If a parent's children were added out of order and live in scattered rows,
    the insertion lands after only the contiguous descendant block; scattered
    siblings remain where they are.
    """
    parent_idx = None
    parent_level = None
    for i, (t, _r, _raw) in enumerate(pairs):
        if t.id == parent_id:
            parent_idx = i
            parent_level = t.level
            break
    if parent_idx is None:
        return None
    insertion_row = pairs[parent_idx][1] + 1
    for i in range(parent_idx + 1, len(pairs)):
        t, r, _raw = pairs[i]
        if t.level > parent_level:
            insertion_row = r + 1
        else:
            break
    return insertion_row


def insert_task_at_row(ws, task: Task, row: int) -> int:
    """Insert a row at the given 1-based position with the task's data.

    Shifts existing rows below down. The timeline ARRAYFORMULA at N{FIRST_TASK_ROW}
    auto-extends its range when rows are inserted, so the new row gets bars
    rendered without extra writes.
    """
    indented = _indented_row(task)
    ws.insert_row(indented, index=row, value_input_option="USER_ENTERED")
    return row


def compute_row_groups(tasks_with_rows: list[tuple[Task, int]]) -> list[tuple[int, int]]:
    """Compute (start_row, end_row_exclusive) ranges for each WBS-anchor with descendants.

    For each task at index i, scan forward for consecutive tasks whose `level`
    is strictly greater (descendants). If any exist, emit a group spanning
    those descendant rows.

    Sheets infers depth from containment: a depth-2 group nested inside a
    depth-1 range becomes a sub-group automatically when both addDimensionGroup
    requests are issued.

    Both indices are 0-based (Sheets API convention) and exclusive at end.
    Assumes tasks are in WBS-sorted order — children directly follow parents.
    """
    groups: list[tuple[int, int]] = []
    n = len(tasks_with_rows)
    for i in range(n):
        anchor_task, _ = tasks_with_rows[i]
        j = i + 1
        while j < n and tasks_with_rows[j][0].level > anchor_task.level:
            j += 1
        if j > i + 1:
            start_1based = tasks_with_rows[i + 1][1]
            end_1based = tasks_with_rows[j - 1][1] + 1
            groups.append((start_1based - 1, end_1based - 1))
    return groups


def delete_task_row(ws, row: int) -> None:
    """Delete the entire row from the sheet. Sheets auto-shifts row references in formulas."""
    ws.delete_rows(row)
