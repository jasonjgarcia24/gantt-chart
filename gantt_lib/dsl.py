"""Predecessor DSL parser.

Grammar:
    entries := entry ("," entry)*
    entry   := wbs_id relation lag?
    wbs_id  := digit ("." digit)*       (e.g. "1", "1.2", "1.2.3")
    relation := "FS" | "SS" | "FF" | "SF"
    lag     := ("+" | "-") digit+        (signed working-days offset)

Whitespace is ignored anywhere. An empty input yields no predecessors.
Any malformed entry raises PredecessorParseError listing the offending entry.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Relation = Literal["FS", "SS", "FF", "SF"]
_RELATIONS: tuple[str, ...] = ("FS", "SS", "FF", "SF")

# id (digits with dots), relation (2 letters), optional signed lag.
_ENTRY_RE = re.compile(
    r"""
    ^
    (?P<id>\d+(?:\.\d+)*)       # WBS id: 1, 1.2, 1.2.3
    (?P<rel>[A-Z]{2})           # any 2-letter relation; validated below
    (?:                         # optional signed lag
        (?P<sign>[+-])
        (?P<lag>\d+)
    )?
    $
    """,
    re.VERBOSE,
)


class PredecessorParseError(ValueError):
    """Raised when a predecessor entry fails to parse."""


@dataclass(frozen=True)
class Predecessor:
    id: str
    rel: Relation
    lag: int = 0


def parse_predecessors(s: str) -> list[Predecessor]:
    s = s.strip()
    if not s:
        return []
    out: list[Predecessor] = []
    for raw_entry in s.split(","):
        entry = re.sub(r"\s+", "", raw_entry)
        if not entry:
            continue
        m = _ENTRY_RE.match(entry)
        if not m:
            raise PredecessorParseError(
                f"could not parse predecessor entry: {raw_entry!r}"
            )
        rel = m.group("rel")
        if rel not in _RELATIONS:
            raise PredecessorParseError(
                f"unknown relation {rel!r} in entry {raw_entry!r} "
                f"(expected one of {_RELATIONS})"
            )
        lag = 0
        if m.group("lag"):
            lag = int(m.group("lag"))
            if m.group("sign") == "-":
                lag = -lag
        out.append(Predecessor(id=m.group("id"), rel=rel, lag=lag))
    return out
