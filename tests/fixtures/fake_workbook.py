"""Minimal in-memory fakes for the gspread Spreadsheet/Worksheet surface
used by gantt_lib.baseline_cmds and gantt_lib.baseline_io.

Implements only the methods we actually call:
- ss.worksheet(name) / .worksheets() / .add_worksheet() / .batch_update()
- ws.update() / .get_values() / .col_values() / .append_rows() / .delete_rows()

Range parsing handles A1, A5, A5:M5 (closed range), A5:M (open-ended end row),
and C2:C50 — the patterns the production code emits. Anything more exotic
raises ValueError so tests fail loud rather than silently doing the wrong
thing.
"""
from __future__ import annotations

import re

_RANGE_RE = re.compile(r"^([A-Z]+)(\d+)(?::([A-Z]+)(\d*))?$")


def _col_idx(letter: str) -> int:
    """A→0, B→1, ..., Z→25, AA→26."""
    n = 0
    for c in letter:
        n = n * 26 + (ord(c) - ord("A") + 1)
    return n - 1


class FakeWorksheet:
    """In-memory grid keyed by (1-based row, 0-based col).

    Storage is `rows: list[list[str]]` — auto-extends on writes past the end.
    Reads of unset cells return empty string.
    """

    def __init__(self, title: str, sheet_id: int = 0):
        self.title = title
        self.id = sheet_id
        self.rows: list[list[str]] = []

    def _ensure_row(self, n_1based: int) -> list[str]:
        while len(self.rows) < n_1based:
            self.rows.append([])
        return self.rows[n_1based - 1]

    def _parse_range(self, range_str: str) -> tuple[int, int, int, int]:
        """Return (col1_0based, row1_1based, col2_0based, row2_1based) inclusive."""
        m = _RANGE_RE.match(range_str)
        if not m:
            raise ValueError(f"FakeWorksheet can't parse range {range_str!r}")
        col1 = _col_idx(m.group(1))
        row1 = int(m.group(2))
        if m.group(3) is None:
            return col1, row1, col1, row1
        col2 = _col_idx(m.group(3))
        if m.group(4):
            row2 = int(m.group(4))
        else:
            row2 = max(len(self.rows), row1)
        return col1, row1, col2, row2

    def update(self, range_name: str, values: list[list], value_input_option=None):
        col1, row1, _col2, _row2 = self._parse_range(range_name)
        for i, value_row in enumerate(values):
            r = row1 + i
            row = self._ensure_row(r)
            for j, val in enumerate(value_row):
                c = col1 + j
                while len(row) <= c:
                    row.append("")
                row[c] = "" if val is None else str(val)

    def get_values(self, range_str: str) -> list[list[str]]:
        col1, row1, col2, row2 = self._parse_range(range_str)
        out: list[list[str]] = []
        for r in range(row1, row2 + 1):
            if r > len(self.rows):
                break
            row = self.rows[r - 1]
            slice_ = list(row[col1:col2 + 1])
            while len(slice_) < (col2 - col1 + 1):
                slice_.append("")
            out.append(slice_)
        return out

    def col_values(self, col_1based: int) -> list[str]:
        out: list[str] = []
        c = col_1based - 1
        for row in self.rows:
            out.append(row[c] if len(row) > c else "")
        return out

    def append_rows(self, rows: list[list], value_input_option=None):
        for row in rows:
            self.rows.append(["" if v is None else str(v) for v in row])

    def delete_rows(self, row_1based: int):
        if 1 <= row_1based <= len(self.rows):
            del self.rows[row_1based - 1]


class FakeSpreadsheet:
    """In-memory workbook holding a name → FakeWorksheet map.

    `add_existing_worksheet(ws)` is a test-only helper that registers a
    pre-built FakeWorksheet (the production code creates new tabs via
    add_worksheet, but tests need to seed pre-existing tabs).
    """

    def __init__(self):
        self._sheets: dict[str, FakeWorksheet] = {}
        self.batch_updates: list[dict] = []
        self._next_id = 1

    def worksheet(self, title: str) -> FakeWorksheet:
        if title not in self._sheets:
            raise Exception(f"FakeSpreadsheet: no sheet named {title!r}")
        return self._sheets[title]

    def worksheets(self) -> list[FakeWorksheet]:
        return list(self._sheets.values())

    def add_worksheet(self, title: str, rows: int, cols: int) -> FakeWorksheet:
        if title in self._sheets:
            raise Exception(f"FakeSpreadsheet: duplicate sheet {title!r}")
        ws = FakeWorksheet(title, sheet_id=self._next_id)
        self._next_id += 1
        self._sheets[title] = ws
        return ws

    def add_existing_worksheet(self, ws: FakeWorksheet):
        if ws.title in self._sheets:
            raise ValueError(f"already have sheet {ws.title!r}")
        self._sheets[ws.title] = ws

    def batch_update(self, body: dict):
        """Record the request body and apply any side-effecting requests we model.

        Currently honors `deleteDimension` for ROWS so the baseline-clear path
        works under tests. Other request types (formatting, mergeCells, etc.)
        are recorded but not applied — production code that depends on those
        having actually been applied should grow a more capable fake when needed.
        """
        self.batch_updates.append(body)
        for req in body.get("requests", []):
            dd = req.get("deleteDimension")
            if not dd:
                continue
            r = dd.get("range", {})
            if r.get("dimension") != "ROWS":
                continue
            sheet_id = r.get("sheetId")
            target = next((w for w in self._sheets.values() if w.id == sheet_id), None)
            if target is None:
                continue
            start = r.get("startIndex", 0)
            end = r.get("endIndex", start + 1)
            for row_1based in range(end, start, -1):
                target.delete_rows(row_1based)
