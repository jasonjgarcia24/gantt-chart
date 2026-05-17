"""Sheets I/O for the hidden `_LinearSync` workbook tab.

The tab holds the WBS-id ↔ Linear-id linkage so a re-pull can match
issues to their existing rows in a program tab. Schema:

    A: program        (str, e.g. "TPM90")
    B: wbs_id         (str, e.g. "1", "1.2")
    C: linear_id      (str, e.g. "TPM-42")
    D: last_synced    (ISO timestamp)
    E: linear_url     (deep-link to the Linear issue)

The tab is created hidden so it doesn't clutter the workbook UI. A
DO-NOT-EDIT warning row sits below the header so users who stumble on
it via Sheets' "Show hidden tabs" menu know it's machine-managed.

Module-level operations:
- `ensure_sync_tab(ss)` — bootstrap if absent, return the worksheet
- `read_links(ss, program)` — list[SyncLink] for one program
- `upsert_links(ss, program, links)` — replace this program's rows
- `delete_links(ss, program)` — drop this program's rows (for --force)

Schema validation: on read, if the header row drifts (someone renamed
or reordered columns), raise `SyncTabSchemaError` with a recovery hint
rather than silently misinterpreting columns.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from gantt_lib import schema

SYNC_TAB = "_LinearSync"
SYNC_HEADERS = ["program", "wbs_id", "linear_id", "last_synced", "linear_url"]
SYNC_WARNING_ROW = [
    "DO NOT EDIT — Managed by `gantt linear-pull`. "
    "Manual edits will be overwritten on next pull.",
    "",
    "",
    "",
    "",
]
_DATA_FIRST_ROW = 3  # row 1 = headers, row 2 = warning, row 3+ = data


class SyncTabSchemaError(ValueError):
    """Raised when the `_LinearSync` tab exists but its header row doesn't
    match the expected schema (typically because someone hand-edited it).
    Includes a recovery hint in the message."""


@dataclass(frozen=True)
class SyncLink:
    program: str
    wbs_id: str
    linear_id: str
    last_synced: str  # ISO timestamp; opaque to this layer
    linear_url: str = ""


def _link_to_row(link: SyncLink) -> list[str]:
    return [
        link.program,
        link.wbs_id,
        link.linear_id,
        link.last_synced,
        link.linear_url,
    ]


def _row_to_link(cells: list[str]) -> Optional[SyncLink]:
    """Parse one raw row. Returns None for blank or malformed rows so
    the caller can skip them without crashing on hand-edits."""
    padded = list(cells) + [""] * (len(SYNC_HEADERS) - len(cells))
    program = padded[0].strip()
    wbs_id = padded[1].strip()
    linear_id = padded[2].strip()
    if not program or not wbs_id or not linear_id:
        return None
    return SyncLink(
        program=program,
        wbs_id=wbs_id,
        linear_id=linear_id,
        last_synced=padded[3].strip(),
        linear_url=padded[4].strip(),
    )


def _validate_header(header_row: list[str]) -> None:
    """Raise SyncTabSchemaError if the header row doesn't match SYNC_HEADERS.

    Tolerant of trailing blank columns (caller may have added new ones)
    but strict on the prefix that already exists.
    """
    expected = SYNC_HEADERS
    have = [c.strip() for c in (header_row or [])]
    if have[: len(expected)] != expected:
        raise SyncTabSchemaError(
            f"`_LinearSync` tab header row {have!r} does not match "
            f"expected {expected!r}. To recover: delete the `_LinearSync` "
            "tab (or rebuild it) and re-run `gantt linear-pull --force`."
        )


def ensure_sync_tab(ss):
    """Return the `_LinearSync` worksheet; create it (hidden) if missing.

    Idempotent. On first creation the tab is initialized with the header
    row + a DO-NOT-EDIT warning row, bolded header, frozen header+warning
    rows, and the entire tab marked hidden so it doesn't appear in the
    user's normal tab strip.
    """
    try:
        ws = ss.worksheet(SYNC_TAB)
        # Validate the existing header so a later read can't silently
        # misinterpret columns.
        header = ws.get_values("A1:E1")
        if header and header[0]:
            _validate_header(header[0])
        return ws
    except SyncTabSchemaError:
        raise
    except Exception:
        pass

    try:
        ws = ss.add_worksheet(
            title=SYNC_TAB, rows=500, cols=len(SYNC_HEADERS),
        )
    except Exception:
        # Race: another caller created it between our check and our create.
        return ss.worksheet(SYNC_TAB)

    ws.update(
        range_name="A1:E2",
        values=[SYNC_HEADERS, SYNC_WARNING_ROW],
        value_input_option="USER_ENTERED",
    )

    n_cols = len(SYNC_HEADERS)
    requests = [
        # Bold the header row.
        {
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": n_cols,
                },
                "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                "fields": "userEnteredFormat.textFormat.bold",
            }
        },
        # Italicize + color the DO-NOT-EDIT warning row.
        {
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 1,
                    "endRowIndex": 2,
                    "startColumnIndex": 0,
                    "endColumnIndex": n_cols,
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
                    "sheetId": ws.id,
                    "gridProperties": {"frozenRowCount": 2},
                },
                "fields": "gridProperties.frozenRowCount",
            }
        },
        # Hide the tab from the default tab strip.
        {
            "updateSheetProperties": {
                "properties": {"sheetId": ws.id, "hidden": True},
                "fields": "hidden",
            }
        },
        # Force WBS column (B) to TEXT so '4.10' isn't truncated to '4.1'
        # — same fix as _Baselines / program tabs.
        {
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
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
    ss.batch_update({"requests": requests})
    return ws


def read_links(ss, program: Optional[str] = None) -> list[SyncLink]:
    """Read SyncLink rows from `_LinearSync`.

    If `program` is None, returns links for every program. Otherwise filters
    to just the rows whose `program` column matches.

    Returns [] if the tab doesn't exist. Skips blank/malformed rows
    silently — a corrupt row is preferable to crashing the pull.
    """
    try:
        ws = ss.worksheet(SYNC_TAB)
    except Exception:
        return []
    header = ws.get_values("A1:E1")
    if header and header[0]:
        _validate_header(header[0])

    rng = ws.get_values(f"A{_DATA_FIRST_ROW}:E")
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
    all_rows = ws.get_values(f"A{_DATA_FIRST_ROW}:E")
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
        combined = combined + [[""] * len(SYNC_HEADERS)] * (old_count - new_count)

    if not combined:
        return
    ws.update(
        range_name=f"A{_DATA_FIRST_ROW}",
        values=combined,
        value_input_option="USER_ENTERED",
    )


def delete_links(ss, program: str) -> int:
    """Remove all `_LinearSync` rows for `program`. Returns count deleted.

    Used by `gantt linear-pull --force` to reset linkage for a program.
    Safe when the tab is absent (returns 0) or when no rows match.
    """
    try:
        ws = ss.worksheet(SYNC_TAB)
    except Exception:
        return 0
    existing = read_links(ss, program)
    if not existing:
        return 0
    upsert_links(ss, program, [])
    return len(existing)
