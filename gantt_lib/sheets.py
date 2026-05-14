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


def read_program_tasks(ws) -> list[Task]:
    """Read the editable region (cols A–M) and return a list of Task objects.

    A row is treated as empty (and skipped) when column A (id) is blank.
    This avoids false positives from checkbox validation: applying a
    checkbox to col L causes Sheets to populate every cell with "FALSE",
    so a "row has any non-empty cell" check would never skip anything.
    """
    last_col = schema.col_letter(schema.NUM_DATA_COLS)
    rng = ws.get_values(f"A{FIRST_DATA_ROW}:{last_col}")
    out: list[Task] = []
    for row in rng:
        if not row or not row[0].strip():
            continue
        out.append(Task.from_row(row))
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
        values=[task.to_row()],
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
        values=[task.to_row()],
        value_input_option="USER_ENTERED",
    )


def delete_task_row(ws, row: int) -> None:
    """Delete the entire row from the sheet. Sheets auto-shifts row references in formulas."""
    ws.delete_rows(row)
