"""Sheets I/O for the hidden `_LinearSync` workbook tab.

The tab holds the WBS-id ↔ Linear-id linkage and (in Phase 2) the
workbook-only sidecar fields plus the last-known Linear snapshot for
3-way merge. Schema:

    A: program              (str, e.g. "TPM90")
    B: wbs_id               (str, e.g. "1", "1.2")
    C: linear_id            (str, e.g. "TPM-42")
    D: last_synced          (ISO timestamp)
    E: linear_url           (deep-link to the Linear issue)
    --- sidecar (workbook-only fields Linear can't carry) ---
    F: sidecar_predecessors (workbook predecessor DSL w/ relation type + lag)
    G: sidecar_percent      (workbook %complete as string, e.g. "50")
    H: sidecar_notes        (workbook Notes column)
    I: sidecar_team         (workbook Team column)
    --- snapshot (last-known Linear state for 3-way merge) ---
    J: snapshot_title
    K: snapshot_state
    L: snapshot_state_type   (one of backlog/unstarted/started/completed/canceled)
    M: snapshot_assignee     (email)
    N: snapshot_due_date     (ISO date or "")
    O: snapshot_estimate     (raw number-as-string, e.g. "5")
    P: snapshot_blockedby    (comma-separated linear_ids)
    Q: snapshot_parent       (parent linear_id)
    R: snapshot_milestone    (milestone id)

The tab is created hidden so it doesn't clutter the workbook UI. A
DO-NOT-EDIT warning row sits below the header so users who stumble on
it via Sheets' "Show hidden tabs" menu know it's machine-managed.

Module-level operations:
- `ensure_sync_tab(ss)` — bootstrap if absent (Phase-2 18-col), auto-
  migrate Phase-1 5-col tabs, return the worksheet
- `migrate_sync_tab(ss)` — explicit upgrade-only entry point; returns
  count of rows migrated, 0 if already at Phase-2 schema or tab absent
- `read_links(ss, program)` — list[SyncLink] for one program
- `upsert_links(ss, program, links)` — replace this program's rows
- `delete_links(ss, program)` — drop this program's rows (for --force)

Schema validation: on read, if the header row drifts (someone renamed
or reordered columns), raise `SyncTabSchemaError` with a recovery hint
rather than silently misinterpreting columns.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Iterable, Optional

from gantt_lib import schema

SYNC_TAB = "_LinearSync"

# Phase-1 headers (cols A-E). Kept as a constant so the migration path
# can detect Phase-1 tabs unambiguously.
PHASE1_HEADERS = ["program", "wbs_id", "linear_id", "last_synced", "linear_url"]

# Phase-2 headers (cols A-R). Adds 4 sidecar cols + 9 snapshot cols.
SYNC_HEADERS = PHASE1_HEADERS + [
    "sidecar_predecessors",
    "sidecar_percent",
    "sidecar_notes",
    "sidecar_team",
    "snapshot_title",
    "snapshot_state",
    "snapshot_state_type",
    "snapshot_assignee",
    "snapshot_due_date",
    "snapshot_estimate",
    "snapshot_blockedby",
    "snapshot_parent",
    "snapshot_milestone",
]

_N_COLS = len(SYNC_HEADERS)  # 18
_LAST_COL_LETTER = schema.col_letter(_N_COLS)  # "R"
_DATA_FIRST_ROW = 3  # row 1 = headers, row 2 = warning, row 3+ = data

SYNC_WARNING_ROW = [
    "DO NOT EDIT — Managed by `gantt linear-sync`. "
    "Manual edits will be overwritten on next sync.",
] + [""] * (_N_COLS - 1)


class SyncTabSchemaError(ValueError):
    """Raised when the `_LinearSync` tab exists but its header row doesn't
    match either the Phase-1 or Phase-2 schema (typically because someone
    hand-edited it). Includes a recovery hint in the message."""


@dataclass(frozen=True)
class SyncLink:
    # Identity (Phase 1 cols A-E)
    program: str
    wbs_id: str
    linear_id: str
    last_synced: str = ""  # ISO timestamp; opaque to this layer
    linear_url: str = ""
    # Sidecar — workbook-only fields Linear can't carry (Phase 2 cols F-I)
    sidecar_predecessors: str = ""
    sidecar_percent: str = ""
    sidecar_notes: str = ""
    sidecar_team: str = ""
    # Snapshot — last-known Linear state for 3-way merge (Phase 2 cols J-R)
    snapshot_title: str = ""
    snapshot_state: str = ""
    snapshot_state_type: str = ""
    snapshot_assignee: str = ""
    snapshot_due_date: str = ""
    snapshot_estimate: str = ""
    snapshot_blockedby: str = ""
    snapshot_parent: str = ""
    snapshot_milestone: str = ""


# Tuple of dataclass field names in column order. Built once so
# (de)serialization is purely positional.
_FIELD_ORDER: tuple[str, ...] = tuple(f.name for f in fields(SyncLink))
assert len(_FIELD_ORDER) == _N_COLS, (
    f"SyncLink field count {len(_FIELD_ORDER)} != SYNC_HEADERS count {_N_COLS}"
)


def _link_to_row(link: SyncLink) -> list[str]:
    return [getattr(link, name) for name in _FIELD_ORDER]


def _row_to_link(cells: list[str]) -> Optional[SyncLink]:
    """Parse one raw row. Returns None for blank or malformed rows so
    the caller can skip them without crashing on hand-edits."""
    padded = list(cells) + [""] * (_N_COLS - len(cells))
    program = padded[0].strip()
    wbs_id = padded[1].strip()
    linear_id = padded[2].strip()
    if not program or not wbs_id or not linear_id:
        return None
    kwargs = {
        name: (padded[idx] if padded[idx] is None else padded[idx].strip())
        for idx, name in enumerate(_FIELD_ORDER)
    }
    return SyncLink(**kwargs)


def _read_header_row(ws) -> list[str]:
    """Read the header row (any width) and trim trailing empties."""
    header = ws.get_values(f"A1:{_LAST_COL_LETTER}1")
    if not header or not header[0]:
        return []
    # Strip trailing empties so a 5-col Phase-1 tab returns ['program', ..., 'linear_url']
    # rather than ['program', ..., 'linear_url', '', '', ...].
    row = [c.strip() for c in header[0]]
    while row and not row[-1]:
        row.pop()
    return row


def _detect_schema_version(header_row: list[str]) -> str:
    """Return 'v2' if the header matches Phase 2, 'v1' if Phase 1, else 'unknown'."""
    if header_row == SYNC_HEADERS:
        return "v2"
    if header_row == PHASE1_HEADERS:
        return "v1"
    return "unknown"


def _validate_header(header_row: list[str]) -> None:
    """Raise SyncTabSchemaError if the header isn't a known schema version.

    Both Phase-1 and Phase-2 layouts are accepted here; callers that
    need Phase-2 strictly should call `ensure_sync_tab` first (which
    auto-migrates Phase-1 tabs in place).
    """
    version = _detect_schema_version(header_row)
    if version == "unknown":
        raise SyncTabSchemaError(
            f"`_LinearSync` tab header row {header_row!r} does not match "
            f"any known schema. Expected either Phase-1 ({PHASE1_HEADERS!r}) "
            f"or Phase-2 ({SYNC_HEADERS!r}). To recover: delete the tab "
            "(or rebuild it) and re-run `gantt linear-sync --force`."
        )


def ensure_sync_tab(ss):
    """Return the `_LinearSync` worksheet; create it (hidden, Phase-2
    schema) if missing. If an existing tab is at Phase-1 schema, migrate
    it in place. Idempotent.
    """
    try:
        ws = ss.worksheet(SYNC_TAB)
    except SyncTabSchemaError:
        raise
    except Exception:
        ws = None

    if ws is not None:
        header = _read_header_row(ws)
        version = _detect_schema_version(header)
        if version == "v2":
            return ws
        if version == "v1":
            _migrate_v1_to_v2(ss, ws)
            return ws
        # Empty (rare race) or unknown — raise to surface to caller.
        if header:
            raise SyncTabSchemaError(
                f"`_LinearSync` tab header row {header!r} does not match "
                f"any known schema. Expected either Phase-1 ({PHASE1_HEADERS!r}) "
                f"or Phase-2 ({SYNC_HEADERS!r}). To recover: delete the tab "
                "(or rebuild it) and re-run `gantt linear-sync --force`."
            )
        # Header is empty — fall through and re-bootstrap.

    try:
        ws = ss.add_worksheet(
            title=SYNC_TAB, rows=500, cols=_N_COLS,
        )
    except Exception:
        # Race: another caller created it between our check and our create.
        return ss.worksheet(SYNC_TAB)

    ws.update(
        range_name=f"A1:{_LAST_COL_LETTER}2",
        values=[SYNC_HEADERS, SYNC_WARNING_ROW],
        value_input_option="USER_ENTERED",
    )

    requests = _format_requests(ws.id)
    ss.batch_update({"requests": requests})
    return ws


def migrate_sync_tab(ss) -> int:
    """Upgrade an existing Phase-1 (5-col) `_LinearSync` tab to Phase-2
    (18-col). Returns the count of data rows migrated.

    Idempotent: a no-op (returns 0) if the tab is already Phase-2 or
    absent. Raises SyncTabSchemaError if the header is in an unknown
    shape (caller must reset manually before re-running).
    """
    try:
        ws = ss.worksheet(SYNC_TAB)
    except Exception:
        return 0

    header = _read_header_row(ws)
    version = _detect_schema_version(header)
    if version == "v2":
        return 0
    if version != "v1":
        raise SyncTabSchemaError(
            f"`_LinearSync` tab header row {header!r} does not match "
            f"Phase-1 schema; cannot auto-migrate. Expected "
            f"{PHASE1_HEADERS!r}. To recover: delete the tab and re-run "
            "`gantt linear-sync --force`."
        )

    return _migrate_v1_to_v2(ss, ws)


def _migrate_v1_to_v2(ss, ws) -> int:
    """Perform the Phase-1 → Phase-2 schema upgrade in place. Returns
    data-row count migrated. Caller has already verified the existing
    header is Phase-1."""
    # Capture existing Phase-1 data rows (cols A-E only).
    existing = ws.get_values(f"A{_DATA_FIRST_ROW}:E")
    data_count = sum(1 for row in existing if _row_to_link(row + [""] * (_N_COLS - len(row))) is not None)

    # Rewrite headers + warning row to the Phase-2 18-col shape.
    ws.update(
        range_name=f"A1:{_LAST_COL_LETTER}2",
        values=[SYNC_HEADERS, SYNC_WARNING_ROW],
        value_input_option="USER_ENTERED",
    )

    # Re-emit existing data rows expanded to 18 cols (new cols default
    # to ""). Snapshot cols stay blank — the next sync's pull pass
    # populates them from current Linear state.
    if existing:
        expanded: list[list[str]] = []
        for row in existing:
            padded = list(row) + [""] * (_N_COLS - len(row))
            expanded.append(padded[:_N_COLS])
        ws.update(
            range_name=f"A{_DATA_FIRST_ROW}",
            values=expanded,
            value_input_option="USER_ENTERED",
        )

    # Re-apply formatting (header bold, warning row colored, freeze,
    # hidden, WBS col TEXT) — safe to re-issue; idempotent on Sheets side.
    ss.batch_update({"requests": _format_requests(ws.id)})

    return data_count


def _format_requests(sheet_id: int) -> list[dict]:
    """Return the batch_update requests for Phase-2 sync-tab formatting.
    Idempotent — safe to re-issue during migration."""
    return [
        # Bold the header row.
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": _N_COLS,
                },
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.textFormat.bold",
            }
        },
        # Italicize + color the DO-NOT-EDIT warning row.
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": 2,
                    "startColumnIndex": 0,
                    "endColumnIndex": _N_COLS,
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {
                            "italic": True,
                            "foregroundColor": {
                                "red": 0.7, "green": 0.1, "blue": 0.1,
                            },
                        }
                    }
                },
                "fields": "userEnteredFormat.textFormat",
            }
        },
        # Freeze header + warning rows so they're always visible if the
        # user does open the tab.
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": 2},
                },
                "fields": "gridProperties.frozenRowCount",
            }
        },
        # Hide the tab from the default tab strip.
        {
            "updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "hidden": True},
                "fields": "hidden",
            }
        },
        # Force WBS column (B) to TEXT so '4.10' isn't truncated to '4.1'.
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startColumnIndex": 1,
                    "endColumnIndex": 2,
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


def read_links(ss, program: Optional[str] = None) -> list[SyncLink]:
    """Read SyncLink rows from `_LinearSync`.

    If `program` is None, returns links for every program. Otherwise filters
    to just the rows whose `program` column matches.

    Returns [] if the tab doesn't exist. Auto-migrates a Phase-1 tab to
    Phase-2 in place before reading so callers always see the full
    SyncLink shape. Skips blank/malformed rows silently — a corrupt row
    is preferable to crashing the sync.
    """
    try:
        ws = ss.worksheet(SYNC_TAB)
    except Exception:
        return []
    header = _read_header_row(ws)
    version = _detect_schema_version(header)
    if version == "v1":
        _migrate_v1_to_v2(ss, ws)
    elif version == "unknown" and header:
        _validate_header(header)  # raises

    rng = ws.get_values(f"A{_DATA_FIRST_ROW}:{_LAST_COL_LETTER}")
    out: list[SyncLink] = []
    for row in rng:
        link = _row_to_link(row)
        if link is None:
            continue
        if program is not None and link.program != program:
            continue
        out.append(link)
    return out


def upsert_links(ss, program: str, links: Iterable[SyncLink]) -> None:
    """Replace all `_LinearSync` rows for `program` with `links`.

    Other programs' rows are left untouched. Order within `links` is
    preserved on write.
    """
    links = list(links)
    # Validate every supplied link is for the right program — guards
    # against caller bugs that would otherwise quietly write to the wrong
    # program's section.
    for link in links:
        if link.program != program:
            raise ValueError(
                f"upsert_links: link for program {link.program!r} passed to "
                f"upsert for program {program!r}"
            )

    ws = ensure_sync_tab(ss)

    # Re-fetch every existing data row, drop the ones for `program`, then
    # rewrite the data region with (kept) + (new). Simpler than partial
    # in-place edits and matches the row-count typically expected for
    # _LinearSync (low hundreds at worst).
    all_rows = ws.get_values(f"A{_DATA_FIRST_ROW}:{_LAST_COL_LETTER}")
    kept: list[list[str]] = []
    for row in all_rows:
        parsed = _row_to_link(row)
        if parsed is None:
            continue
        if parsed.program == program:
            continue
        kept.append(_link_to_row(parsed))

    new_rows = [_link_to_row(link) for link in links]
    combined = kept + new_rows

    # Clear the existing data region by writing blanks over the old span,
    # then write the new combined data starting at _DATA_FIRST_ROW.
    old_count = len(all_rows)
    new_count = len(combined)
    if old_count > new_count:
        # Pad with blank rows to clear the tail.
        combined = combined + [[""] * _N_COLS] * (old_count - new_count)

    if not combined:
        return
    ws.update(
        range_name=f"A{_DATA_FIRST_ROW}",
        values=combined,
        value_input_option="USER_ENTERED",
    )


def delete_links(ss, program: str) -> int:
    """Remove all `_LinearSync` rows for `program`. Returns count deleted.

    Used by `gantt linear-sync --force` to reset linkage for a program.
    Safe when the tab is absent (returns 0) or when no rows match.
    """
    try:
        ss.worksheet(SYNC_TAB)
    except Exception:
        return 0
    existing = read_links(ss, program)
    if not existing:
        return 0
    upsert_links(ss, program, [])
    return len(existing)
