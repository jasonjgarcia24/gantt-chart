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

    # Person-chip enrichment: when the Owner cell contains a Google
    # Sheets person chip, replace the plain-text task.owner with the
    # chip's canonical email so the push code can match it cleanly
    # against the Linear workspace user list.
    ss = getattr(ws, "spreadsheet", None)
    if ss is not None:
        try:
            chip_emails = read_owner_chip_emails(ss, ws)
        except Exception:
            chip_emails = {}
        if chip_emails:
            for task, row_idx, _raw in out:
                email = chip_emails.get(row_idx)
                if email:
                    task.owner = email

    return out


def parse_owner_chip_emails(response: dict, first_data_row: int) -> dict[int, str]:
    """Pure parser: extract `{row_idx: email}` from a `spreadsheets.get`
    response that requested the Owner column's `chipRuns`.

    Returns the row indices (1-based, sheet coordinates) and emails for
    cells that actually contain a person chip with an email; skips cells
    with no chip or with chips that have no email.

    Splitting this out from `read_owner_chip_emails` lets tests cover the
    parsing without mocking the entire googleapiclient call chain.
    """
    out: dict[int, str] = {}
    sheets_data = response.get("sheets", [])
    if not sheets_data:
        return out
    data_blocks = sheets_data[0].get("data", [])
    if not data_blocks:
        return out
    row_data = data_blocks[0].get("rowData", [])
    for offset, row in enumerate(row_data):
        values = row.get("values", [])
        if not values:
            continue
        cell = values[0]
        chip_runs = cell.get("chipRuns") or []
        if not chip_runs:
            continue
        # First chip wins — person chips typically occupy the whole cell;
        # if there are multiple, the first is conventionally the owner.
        chip = chip_runs[0].get("chip", {})
        person = chip.get("personProperties") or {}
        email = (person.get("email") or "").strip()
        if email:
            out[first_data_row + offset] = email
    return out


def read_owner_chip_emails(ss, ws) -> dict[int, str]:
    """Return `{row_idx: email}` for cells in the Owner column (col D)
    that contain a Google Sheets person chip. Rows without chips are
    absent from the dict — callers fall back to the cell's plain text.

    Person chips store the canonical Google contact email in their
    metadata; lifting it lets us push it directly to Linear's
    `save_issue.assignee` (which resolves cleanly by email, unlike
    free-text names).

    Uses the lower-level `spreadsheets.get()` API with a `fields`
    projection — gspread's `get_values()` strips chip metadata. Falls
    back to `{}` (best-effort) on any error so a transient API hiccup
    doesn't break the sync.
    """
    try:
        from googleapiclient.discovery import build
    except Exception:
        return {}

    # Resolve credentials from the gspread client.
    try:
        creds = getattr(getattr(ss, "client", None), "auth", None)
        if creds is None:
            return {}
    except Exception:
        return {}

    last_data_row = schema.HEADER_ROWS + schema.DEFAULT_DATA_ROWS  # e.g. 4 + 100 = 104
    owner_range = (
        f"{ws.title}!"
        f"{schema.col_letter(schema.COL_OWNER_IDX + 1)}{FIRST_DATA_ROW}:"
        f"{schema.col_letter(schema.COL_OWNER_IDX + 1)}{last_data_row}"
    )
    try:
        svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
        resp = svc.spreadsheets().get(
            spreadsheetId=ss.id,
            ranges=[owner_range],
            fields=(
                "sheets.data.rowData.values("
                "chipRuns(chip(personProperties(email))),"
                "formattedValue)"
            ),
        ).execute()
    except Exception:
        return {}

    return parse_owner_chip_emails(resp, FIRST_DATA_ROW)


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


def _delete_cf_rules_matching_formula(ss, ws, formula_substring: str) -> int:
    """Delete every conditional-format rule on `ws` whose CUSTOM_FORMULA
    contains `formula_substring`. Returns the count of rules deleted.

    Iterates by rule index; Sheets returns CF rules in declaration order
    and deleting one shifts later indices down by one — so we delete
    from highest index to lowest to keep the indices stable.

    Used to make `apply_*_cf` patches idempotent: re-running a patch
    after the rule shape changes (e.g. new columns added) deletes the
    stale rule before adding the new one, instead of stacking duplicates.
    """
    metadata = ss.fetch_sheet_metadata(params={"includeGridData": False})
    target_sheet = next(
        s for s in metadata["sheets"] if s["properties"]["sheetId"] == ws.id
    )
    cf_rules = target_sheet.get("conditionalFormats", [])
    indices_to_delete: list[int] = []
    for idx, rule in enumerate(cf_rules):
        boolean = rule.get("booleanRule") or {}
        condition = boolean.get("condition") or {}
        if condition.get("type") != "CUSTOM_FORMULA":
            continue
        values = condition.get("values") or []
        formula = values[0].get("userEnteredValue", "") if values else ""
        if formula_substring in formula:
            indices_to_delete.append(idx)
    if not indices_to_delete:
        return 0
    requests = [
        {"deleteConditionalFormatRule": {"sheetId": ws.id, "index": idx}}
        for idx in reversed(indices_to_delete)
    ]
    ss.batch_update({"requests": requests})
    return len(indices_to_delete)


def apply_milestone_row_grey_out_cf(ss, program_name: str) -> None:
    """Apply the `milestone_row_grey_out_cf_request` rule to a program tab.

    Idempotent: deletes any pre-existing CF rule whose trigger formula
    matches the milestone-row pattern (`$L<row>=TRUE`) before adding the
    fresh rule. Lets retro-patches re-run cleanly when the rule shape
    changes (e.g. extended column coverage) without duplicating.

    Requires v2 schema (the formula references col M = Milestone Link,
    which only exists post-PR2b). Raises ProgramTabSchemaError on v1
    tabs.
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

    # Delete any prior milestone-row CF rule; the trigger formula is
    # unique enough ($L<row>=TRUE on the FIRST_TASK_ROW anchor) that
    # substring match safely identifies our rule.
    _delete_cf_rules_matching_formula(
        ss, ws, f"$L{schema.FIRST_TASK_ROW}=TRUE",
    )
    ss.batch_update({
        "requests": [schema.milestone_row_grey_out_cf_request(ws.id)],
    })


def apply_unresolved_owner_marker_cf(ss, program_name: str) -> None:
    """Apply the unresolved-owner CF marker to a program tab. Owner cells
    whose value didn't resolve to a Linear workspace user (per the last
    sync's `sidecar_owner_resolved` flag in `_LinearSync`) get italicized
    + dim-grey text.

    Idempotent: deletes any pre-existing CF rule whose trigger formula
    looks like our marker (contains the literal `sidecar_owner_resolved`
    lookup pattern) before adding the fresh rule. Lets re-runs replace
    older versions cleanly when the rule shape evolves.

    Requires v2 program-tab schema and the v4 `_LinearSync` schema
    (which has `sidecar_owner_resolved` at col T). Run
    `gantt linear-sync` once first to auto-migrate `_LinearSync` to v4,
    or this CF rule will silently never fire (VLOOKUP returns #N/A on
    every row → IFERROR returns FALSE → no formatting applied).
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
            f"program {program_name!r} is on {version!r} schema; the "
            "unresolved-owner CF references col D (Owner) which exists on "
            f"both v1 and v2 but assumes v2 layout. Run `gantt program "
            f"migrate-schema {program_name}` first."
        )

    # Match-by-formula identifier — every variant of this rule uses
    # this lookup column, so the substring is reliable for dedupe.
    _delete_cf_rules_matching_formula(ss, ws, "_LinearSync!$T:$T")
    ss.batch_update({
        "requests": [
            schema.unresolved_owner_marker_cf_request(ws.id, program_name),
        ],
    })


def apply_status_dropdown(ss, program_name: str) -> None:
    """Re-apply the Status column data-validation dropdown on an
    existing program tab. Idempotent: setDataValidation replaces any
    existing rule on the same range.

    Use after adding/removing values from `Status.all()` so existing
    tabs pick up the new dropdown options without recreating the tab.

    Requires v2 schema (the validation range references COL_STATUS_IDX
    which differs between v1 and v2 — though for Status specifically
    the column position didn't shift, the v2 guard keeps this consistent
    with the other patch commands).
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
            f"program {program_name!r} is on {version!r} schema; the status "
            "dropdown patch assumes v2 column layout. Run "
            f"`gantt program migrate-schema {program_name}` first."
        )

    ss.batch_update({
        "requests": [schema.status_validation_request(ws.id)],
    })


def apply_default_team_cf(ss, program_name: str) -> None:
    """Add the `default_team_color_cf_request` rule to an existing program
    tab. Paints a pastel-blue background on timeline cells for any row
    whose Team cell is blank — so rows without an assigned team still
    render a visible bar on the Gantt timeline.

    Requires v2 schema: the CF rule's range + formula reference the
    timeline-first column index (col O on v2). Applied to a v1 tab the
    rule would land on the wrong columns. Raises ProgramTabSchemaError
    on v1 tabs so the caller migrates first.

    NOT idempotent: re-running adds duplicate CF rules (cosmetic only —
    same colour, same trigger).
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
            f"program {program_name!r} is on {version!r} schema; the default-team "
            "CF range references the v2 timeline-first column. Run "
            f"`gantt program migrate-schema {program_name}` first."
        )

    ss.batch_update({
        "requests": [schema.default_team_color_cf_request(ws.id)],
    })


def apply_grey_out_cf(ss, program_name: str) -> None:
    """Apply the `linked_workbook_only_grey_out_cf_request` rule to a
    program tab.

    Idempotent: deletes any pre-existing CF rule whose trigger formula
    matches the linked-row pattern (`ISFORMULA($C<row>)`) before adding
    the fresh rule. Lets retro-patches re-run cleanly when the rule
    shape changes (e.g. extended column coverage) without duplicating.

    Requires v2 schema — the grey-out formula assumes the post-PR2b
    column layout (% Complete at idx 8, Notes at idx 13). Raises
    ProgramTabSchemaError on v1 tabs so the caller migrates first.
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

    # Delete any prior linked-row CF rule; the trigger formula
    # ISFORMULA($C<row>) on the anchor row is unique to our rule.
    _delete_cf_rules_matching_formula(
        ss, ws, f"ISFORMULA(${schema.COL_NAME_LETTER}{schema.FIRST_TASK_ROW})",
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


def refresh_row_groups(ss, ws) -> int:
    """Rebuild Sheets row-grouping (the +/- gutter that lets the user
    collapse children under their parent) from the current task hierarchy
    on the program tab. Returns the number of groups created.

    Reads current tasks (which include the level field), computes the
    nested group ranges via `compute_row_groups`, deletes any existing
    rowGroups on the sheet, and adds the freshly-computed ones in one
    batch_update.

    Assumes rows are already in WBS-sorted order on the sheet (children
    directly follow parents). For an unsorted tab, callers should sort
    rows first.
    """
    pairs = read_program_tasks_with_rows(ws)
    sorted_pairs = [(task, row_idx) for task, row_idx, _raw in pairs]

    metadata = ss.fetch_sheet_metadata(params={"includeGridData": False})
    target_sheet = next(
        s for s in metadata["sheets"] if s["properties"]["sheetId"] == ws.id
    )
    existing_row_groups = target_sheet.get("rowGroups", [])
    new_row_groups = compute_row_groups(sorted_pairs)

    requests = []
    for g in existing_row_groups:
        rng = g["range"]
        requests.append(schema.delete_row_group_request(
            ws.id, rng["startIndex"], rng["endIndex"],
        ))
    for start, end in new_row_groups:
        requests.append(schema.add_row_group_request(ws.id, start, end))
    if requests:
        ss.batch_update({"requests": requests})
    return len(new_row_groups)


def delete_task_row(ws, row: int) -> None:
    """Delete the entire row from the sheet. Sheets auto-shifts row references in formulas."""
    ws.delete_rows(row)
