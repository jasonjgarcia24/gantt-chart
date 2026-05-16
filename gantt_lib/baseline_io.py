"""Sheets I/O for the workbook-level `_Baselines` tab.

Translates between BaselineRow instances and raw cell values, plus
ensure-create / read / append / delete-by-program ops on the tab. Mirrors the
shape of `gantt_lib.sheets` (which handles per-program tabs) but operates at
the workbook level since `_Baselines` is a single shared tab.
"""
from __future__ import annotations

import sys
from datetime import date
from typing import Optional

from . import schema
from .baseline import BASELINE_HEADERS, BASELINE_TAB, BaselineRow


def baseline_row_to_cells(b: BaselineRow) -> list[str]:
    """Serialize one BaselineRow to a list[str] in BASELINE_HEADERS column order."""
    return [
        b.program,
        b.wbs,
        b.task_name,
        b.snapshot_date.isoformat(),
        b.baseline_start.isoformat() if b.baseline_start else "",
        b.baseline_end.isoformat() if b.baseline_end else "",
        str(b.baseline_duration),
        b.baseline_predecessors,
        b.snapshot_label,
        b.snapshot_actor,
    ]


def cells_to_baseline_row(cells: list[str]) -> Optional[BaselineRow]:
    """Parse one raw row into a BaselineRow, or None if malformed.

    Required fields (return None if missing/unparseable): program, wbs,
    snapshot_date, baseline_duration. baseline_start/end may be blank
    (snapshotting a task that hadn't been scheduled yet). Trailing optional
    columns (predecessors, label, actor) default to empty string when absent.
    """
    if len(cells) < 7:
        return None
    try:
        program = cells[0].strip()
        wbs = cells[1].strip()
        task_name = cells[2]
        if not program or not wbs:
            return None
        snapshot_date = date.fromisoformat(cells[3].strip())
        baseline_start = (
            date.fromisoformat(cells[4].strip()) if cells[4].strip() else None
        )
        baseline_end = (
            date.fromisoformat(cells[5].strip()) if cells[5].strip() else None
        )
        baseline_duration = int(cells[6].strip())
    except (ValueError, IndexError):
        return None
    return BaselineRow(
        program=program,
        wbs=wbs,
        task_name=task_name,
        snapshot_date=snapshot_date,
        baseline_start=baseline_start,
        baseline_end=baseline_end,
        baseline_duration=baseline_duration,
        baseline_predecessors=cells[7] if len(cells) > 7 else "",
        snapshot_label=cells[8] if len(cells) > 8 else "",
        snapshot_actor=cells[9] if len(cells) > 9 else "",
    )


def read_baselines(ss) -> list[BaselineRow]:
    """Read all rows from `_Baselines` as BaselineRow instances.

    Returns [] if the tab doesn't exist. Skips malformed rows with a stderr
    warning rather than crashing — keeps `show` resilient when someone has
    hand-edited the tab and broken a date.
    """
    try:
        ws = ss.worksheet(BASELINE_TAB)
    except Exception:
        return []
    last_col = schema.col_letter(len(BASELINE_HEADERS))
    rows = ws.get_values(f"A2:{last_col}")  # skip header row
    out: list[BaselineRow] = []
    for idx, raw in enumerate(rows, start=2):
        if not raw or not (raw[0].strip() if raw and raw[0] else ""):
            continue  # blank row
        parsed = cells_to_baseline_row(raw)
        if parsed is None:
            print(
                f"gantt: warning — skipping malformed _Baselines row {idx}: {raw!r}",
                file=sys.stderr,
            )
            continue
        out.append(parsed)
    return out


def ensure_baselines_tab(ss):
    """Return the `_Baselines` worksheet, creating it with formatting if missing.

    Idempotent: a duplicate-sheet error from concurrent creation falls through
    to a re-fetch and returns the existing tab. New tabs get the header row
    written, header bolded + frozen, first 3 cols frozen, and date columns
    (D-F) formatted ISO.
    """
    try:
        return ss.worksheet(BASELINE_TAB)
    except Exception:
        pass

    try:
        ws = ss.add_worksheet(
            title=BASELINE_TAB, rows=200, cols=len(BASELINE_HEADERS),
        )
    except Exception:
        # Race: another caller created it between our check and our create.
        return ss.worksheet(BASELINE_TAB)

    ws.update(
        range_name="A1",
        values=[BASELINE_HEADERS],
        value_input_option="USER_ENTERED",
    )

    n_cols = len(BASELINE_HEADERS)
    requests = [
        {
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 0, "endRowIndex": 1,
                    "startColumnIndex": 0, "endColumnIndex": n_cols,
                },
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.textFormat.bold",
            }
        },
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": ws.id,
                    "gridProperties": {
                        "frozenRowCount": 1,
                        "frozenColumnCount": 3,
                    },
                },
                "fields": (
                    "gridProperties.frozenRowCount,"
                    "gridProperties.frozenColumnCount"
                ),
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 1,
                    "startColumnIndex": 3, "endColumnIndex": 6,
                },
                "cell": {
                    "userEnteredFormat": {
                        "numberFormat": {"type": "DATE", "pattern": "yyyy-mm-dd"},
                    }
                },
                "fields": "userEnteredFormat.numberFormat",
            }
        },
        # Force col B (wbs) to TEXT so WBS ids like '4.10' / '6.10' aren't
        # silently truncated to '4.1' / '6.1' by Sheets numeric coercion —
        # same root cause as issue #1, applied here to the _Baselines tab.
        {
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startColumnIndex": 1, "endColumnIndex": 2,
                },
                "cell": {
                    "userEnteredFormat": {
                        "numberFormat": {"type": "TEXT"},
                    }
                },
                "fields": "userEnteredFormat.numberFormat",
            }
        },
    ]
    ss.batch_update({"requests": requests})
    return ws


def append_baselines(ss, rows: list[BaselineRow]) -> None:
    """Atomic batch write of N BaselineRow instances to `_Baselines`.

    Uses worksheet.append_rows(), which gspread translates into a single Sheets
    API request — either all rows land or none do, satisfying the spec's
    all-or-nothing-per-snapshot guarantee.
    """
    if not rows:
        return
    ws = ensure_baselines_tab(ss)
    # RAW (not USER_ENTERED) so WBS strings like '4.10' aren't parsed as
    # numbers and truncated to '4.1' — same root cause as issue #1. Dates
    # are still readable as 'yyyy-mm-dd' strings under DATE-formatted cols.
    ws.append_rows(
        [baseline_row_to_cells(r) for r in rows],
        value_input_option="RAW",
    )


def list_program_names(ss) -> list[str]:
    """Return names of every program in the workbook, sorted.

    Scans for tabs prefixed with `P_` and strips the prefix. Used by
    `--all` / `--all-programs` to fan out across the full portfolio.
    """
    out: list[str] = []
    prefix = schema.PROGRAM_TAB_PREFIX
    for ws in ss.worksheets():
        if ws.title.startswith(prefix):
            out.append(ws.title[len(prefix):])
    return sorted(out)


def program_tab_exists(ss, program: str) -> bool:
    """True if `P_<program>` exists in the workbook."""
    try:
        ss.worksheet(schema.program_tab_name(program))
        return True
    except Exception:
        return False


def delete_baselines_for_programs(ss, programs: list[str]) -> int:
    """Delete every `_Baselines` row whose program column matches. Returns count deleted.

    Safe when the tab is missing (returns 0). Packs all deletions into a single
    Spreadsheet.batch_update — N row deletes cost 1 Sheets write quota unit
    (vs. N units for the previous per-row implementation, which tripped the
    60/min/user limit on programs with ~80+ baseline rows). See
    `docs/issues/baseline-clear-quota.md` for the incident that motivated this.

    Within the batch, requests run sequentially; deleting row 3 first would
    shift row 5 → row 4 and break subsequent index references. Sorting
    descending keeps indices stable across the whole batch.
    """
    try:
        ws = ss.worksheet(BASELINE_TAB)
    except Exception:
        return 0
    program_col = ws.col_values(1)
    targets = set(programs)
    rows_to_delete = [
        idx for idx, val in enumerate(program_col, start=1)
        if idx > 1 and val.strip() in targets
    ]
    if not rows_to_delete:
        return 0
    requests = [
        {
            "deleteDimension": {
                "range": {
                    "sheetId": ws.id,
                    "dimension": "ROWS",
                    "startIndex": r - 1,
                    "endIndex": r,
                },
            },
        }
        for r in sorted(rows_to_delete, reverse=True)
    ]
    ss.batch_update({"requests": requests})
    return len(rows_to_delete)
