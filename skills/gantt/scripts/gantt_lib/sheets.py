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

    Joins each task with the `_LinearSync` tab (best-effort, side-effect-free)
    so `task.linear_url` is populated for any task linked to a Linear issue.
    This is what lets `update_task_data` re-render the HYPERLINK formula in
    the Name column on subsequent writes — without the join, gspread reads
    return the rendered text of `=HYPERLINK(...)` formulas as plain strings,
    `task.linear_url` stays None, and the next write would clobber the
    formula with plain text (breaking the grey-out CF among other things).
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

    url_by_wbs = _read_linear_urls_by_wbs(ws)
    if url_by_wbs:
        for task, _row_idx, _raw in out:
            url = url_by_wbs.get(task.id)
            if url:
                task.linear_url = url

    return out


def _read_linear_urls_by_wbs(ws) -> dict[str, str]:
    """Best-effort: return `{wbs_id: linear_url}` for tasks on this program
    that are linked to Linear. Returns `{}` when:
      - the worksheet has no `spreadsheet` back-reference (standalone fixture)
      - the workbook has no `_LinearSync` tab yet (Phase-1 install)
      - any error occurs reading or parsing the tab (degrade gracefully)

    Never raises and never has side effects — does NOT auto-create the
    sync tab, so recalc / baseline / task mutations don't grow a hidden
    sidecar on workbooks that aren't using Linear sync.
    """
    ss = getattr(ws, "spreadsheet", None)
    if ss is None:
        return {}
    try:
        from gantt_lib.linear.sync_tab import SYNC_TAB, read_links
    except Exception:
        return {}
    # Probe for the tab without creating it.
    try:
        existing_titles = {w.title for w in ss.worksheets()}
    except Exception:
        return {}
    if SYNC_TAB not in existing_titles:
        return {}
    program = _program_name_from_tab_title(ws.title)
    if not program:
        return {}
    try:
        links = read_links(ss, program)
    except Exception:
        return {}
    return {l.wbs_id: l.linear_url for l in links if l.linear_url}


def _program_name_from_tab_title(tab_title: str) -> str:
    """`P_TPM90` → `TPM90`. Returns "" if the title doesn't carry the
    program-tab prefix (e.g. `_Config`, `_Baselines`, `_LinearSync`)."""
    if tab_title.startswith(schema.PROGRAM_TAB_PREFIX):
        return tab_title[len(schema.PROGRAM_TAB_PREFIX):]
    return ""


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


def migrate_program_tab_v1_to_v2(ss, program_name: str) -> str:
    """Upgrade a v1 program tab (13 data cols) to v2 (14 cols, adds
    Milestone Link between Milestone? and Notes). Idempotent — no-op
    on v2 tabs. Raises ProgramTabSchemaError on unknown shapes.

    Mechanically: insert one column at `COL_MILESTONE_LINK_IDX` via the
    Sheets `insertDimension` API (Notes shifts M→N, timeline cells N+→O+
    automatically), then write the "Milestone Link" header into the new
    col M, row 4. Returns the post-migration schema version ('v2').

    Caveats:
    - CF rules and ARRAYFORMULA references that target absolute column
      letters past col L update implicitly via the insertDimension API;
      formulas anchored at fixed letters (e.g. `$E5`, `$F5` for team /
      start / end columns to the left of the insert) are untouched.
    - Data validation rules (e.g. the checkbox on col L) are
      range-scoped and stay on their original column; the new col M
      doesn't inherit them.
    """
    tab_name = schema.program_tab_name(program_name)
    try:
        ws = ss.worksheet(tab_name)
    except Exception as e:
        raise schema.ProgramTabSchemaError(
            f"program {program_name!r} not found (tab {tab_name!r})"
        ) from e

    header_row = ws.get_values("A4:O4")
    header_cells = header_row[0] if header_row else []
    version = schema.detect_program_tab_schema(header_cells)

    if version == "v2":
        return "v2"

    if version != "v1":
        raise schema.ProgramTabSchemaError(
            f"program {program_name!r} header row {header_cells!r} is not a "
            "known schema (expected v1 or v2). Restore the header row "
            "manually before migrating, or recreate via `gantt program new --force`."
        )

    # v1 → v2: insert column at idx COL_MILESTONE_LINK_IDX.
    ss.batch_update({
        "requests": [{
            "insertDimension": {
                "range": {
                    "sheetId": ws.id,
                    "dimension": "COLUMNS",
                    "startIndex": schema.COL_MILESTONE_LINK_IDX,
                    "endIndex": schema.COL_MILESTONE_LINK_IDX + 1,
                },
                "inheritFromBefore": True,
            }
        }]
    })

    # Label the newly-empty col M of row 4 (the data-header row).
    ws.update(
        range_name=f"{schema.COL_MILESTONE_LINK_LETTER}{schema.DAY_HEADER_ROW}",
        values=[["Milestone Link"]],
        value_input_option="USER_ENTERED",
    )

    # Confirm.
    new_header = ws.get_values("A4:O4")
    new_cells = new_header[0] if new_header else []
    new_version = schema.detect_program_tab_schema(new_cells)
    if new_version != "v2":
        raise schema.ProgramTabSchemaError(
            f"migration completed but tab {tab_name!r} still doesn't validate "
            f"as v2 (detected {new_version!r}, header now {new_cells!r})"
        )
    return "v2"


def apply_milestone_row_grey_out_cf(ss, program_name: str) -> None:
    """Add the `milestone_row_grey_out_cf_request` rule to an existing
    program tab. Greys out fields that don't apply to milestone rows
    (Duration, % Complete, Milestone Link).

    Requires v2 schema (the formula references col M = Milestone Link,
    which only exists post-PR2b). Raises ProgramTabSchemaError on v1
    tabs.

    NOT idempotent: re-running adds duplicate CF rules.
    """
    tab_name = schema.program_tab_name(program_name)
    try:
        ws = ss.worksheet(tab_name)
    except Exception as e:
        raise schema.ProgramTabSchemaError(
            f"program {program_name!r} not found (tab {tab_name!r})"
        ) from e

    header_row = ws.get_values("A4:O4")
    header_cells = header_row[0] if header_row else []
    version = schema.detect_program_tab_schema(header_cells)
    if version != "v2":
        raise schema.ProgramTabSchemaError(
            f"program {program_name!r} is on {version!r} schema; the milestone-row "
            "grey-out formula references col M (Milestone Link), which only exists "
            f"on v2. Run `gantt program migrate-schema {program_name}` first."
        )

    ss.batch_update({
        "requests": [schema.milestone_row_grey_out_cf_request(ws.id)],
    })


def apply_grey_out_cf(ss, program_name: str) -> None:
    """Add the `linked_workbook_only_grey_out_cf_request` rule to an
    existing program tab. New programs already get this rule at create
    time (PR2); this function retro-patches it onto tabs that predate
    PR2 (LINEAR_TEST, TPM90, Tahoma).

    Requires v2 schema — the grey-out formula assumes the post-PR2b
    column layout (% Complete at idx 8, Notes at idx 13). Raises
    ProgramTabSchemaError on v1 tabs so the caller migrates first.

    NOT idempotent: re-running adds duplicate CF rules (cosmetic only —
    they evaluate to the same colour, but they clutter the rule list).
    Designed for one-off retro-patch use.
    """
    tab_name = schema.program_tab_name(program_name)
    try:
        ws = ss.worksheet(tab_name)
    except Exception as e:
        raise schema.ProgramTabSchemaError(
            f"program {program_name!r} not found (tab {tab_name!r})"
        ) from e

    header_row = ws.get_values("A4:O4")
    header_cells = header_row[0] if header_row else []
    version = schema.detect_program_tab_schema(header_cells)
    if version != "v2":
        raise schema.ProgramTabSchemaError(
            f"program {program_name!r} is on {version!r} schema; grey-out CF "
            "assumes v2 column layout. Run `gantt program migrate-schema "
            f"{program_name}` first."
        )

    ss.batch_update({
        "requests": [schema.linked_workbook_only_grey_out_cf_request(ws.id)],
    })


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
