"""Snapshot construction and equality helpers for 3-way merge.

The snapshot captures the last-known Linear state of an issue at the
moment we successfully synced it. On the next sync, comparing
(current workbook value, snapshot, current Linear value) lets us tell
which side changed since last sync and resolve any divergence.

This module is **pure logic** — no MCP calls, no Sheets I/O. The agent
fetches Linear data and hands it here as a normalized dict; the merge
engine (`merge.py`) then compares against the previously-stored
snapshot loaded from `_LinearSync`.

All snapshot fields are stored as strings on the wire (matching the
sheet cell format). Equality helpers normalize representations so
equivalent values (None vs "", trailing whitespace, blockedBy
ordering, etc.) don't trigger false positives in the 3-way merge.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Optional

from gantt_lib.linear.sync_tab import SyncLink


# Field names shared between IssueSnapshot and the snapshot_* columns
# on SyncLink. Source of truth for serialization order.
SNAPSHOT_FIELD_NAMES: tuple[str, ...] = (
    "title",
    "state",
    "state_type",
    "assignee",
    "due_date",
    "estimate",
    "blockedby",
    "parent",
    "milestone",
    "team",
)


@dataclass(frozen=True)
class IssueSnapshot:
    """Last-known Linear state for one issue.

    Mirrors the snapshot_* columns of SyncLink. All str so the round-trip
    to the sheet is lossless; numeric parsing happens at compare time
    via the equality helpers.
    """
    title: str = ""
    state: str = ""
    state_type: str = ""
    assignee: str = ""
    due_date: str = ""
    estimate: str = ""
    blockedby: str = ""  # comma-separated linear_ids in fetch order
    parent: str = ""
    milestone: str = ""
    team: str = ""  # workbook team name derived from labels via the map


# ----- Construction from Linear MCP payload ----------------------------------


def _str_or_empty(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    return str(v)


def derive_team_from_labels(
    label_names: list[str],
    team_label_map: dict[str, str],
) -> str:
    """Return the workbook team name implied by the issue's label set,
    or "" if none of the labels are in the map's value set.

    `team_label_map` is workbook-team-name → Linear-label-name. Multiple
    matches are deterministic-ish via dict insertion order — but a real
    workbook should keep labels mutually-exclusive (one team-label per
    issue) to avoid ambiguity.
    """
    if not team_label_map or not label_names:
        return ""
    label_to_team = {label: team for team, label in team_label_map.items()}
    for name in label_names:
        team = label_to_team.get(name)
        if team:
            return team
    return ""


def build_snapshot_from_linear(
    linear_issue: dict,
    *,
    blockedby_ids: Optional[list[str]] = None,
    team_label_map: Optional[dict[str, str]] = None,
) -> IssueSnapshot:
    """Convert a Linear MCP issue dict (from list_issues / get_issue)
    into a snapshot.

    `blockedby_ids` should come from the issue's
    `relations.blockedBy[*].id` after a `get_issue(includeRelations=true)`
    call; pass `[]` when the issue has no blockers, or `None` to fall
    back to whatever's on `linear_issue.get("relations", {}).get("blockedBy", [])`.
    """
    # Estimate can come back as {"value": 5, "name": "5 Points"} or null.
    raw_est = linear_issue.get("estimate")
    if isinstance(raw_est, dict):
        estimate_str = _str_or_empty(raw_est.get("value"))
    else:
        estimate_str = _str_or_empty(raw_est)

    # State name + type. Phase 1's list_issues returns flat `status` /
    # `statusType` strings; get_issue may return a nested `state` dict.
    state_name = _str_or_empty(linear_issue.get("status"))
    state_type = _str_or_empty(linear_issue.get("statusType"))
    state_field = linear_issue.get("state")
    if isinstance(state_field, dict):
        state_name = state_name or _str_or_empty(state_field.get("name"))
        state_type = state_type or _str_or_empty(state_field.get("type"))

    # Assignee — prefer email, fall back to display name.
    assignee_email = ""
    asg = linear_issue.get("assignee")
    if isinstance(asg, dict):
        assignee_email = _str_or_empty(asg.get("email")) or _str_or_empty(asg.get("displayName"))
    elif isinstance(asg, str):
        assignee_email = _str_or_empty(asg)

    # BlockedBy: prefer explicit caller-supplied list, then dig from relations.
    if blockedby_ids is None:
        relations = linear_issue.get("relations") or {}
        bb = relations.get("blockedBy") or []
        blockedby_ids = [_str_or_empty(b.get("id") if isinstance(b, dict) else b) for b in bb]
    blockedby_clean = [bid for bid in blockedby_ids if bid]
    blockedby_str = ",".join(blockedby_clean)

    # Milestone — may be id or name; standardize to ID-or-empty.
    milestone_id = ""
    ms = linear_issue.get("milestone")
    if isinstance(ms, dict):
        milestone_id = _str_or_empty(ms.get("id"))
    elif isinstance(ms, str):
        milestone_id = _str_or_empty(ms)

    parent = _str_or_empty(linear_issue.get("parentId"))

    # Due date — Linear returns ISO with optional time; keep as-given.
    due_date = _str_or_empty(linear_issue.get("dueDate"))

    # Team: derived from labels via the workbook→label map.
    raw_labels = linear_issue.get("labels") or []
    label_names: list[str] = []
    for entry in raw_labels:
        if isinstance(entry, dict):
            n = _str_or_empty(entry.get("name"))
            if n:
                label_names.append(n)
        elif isinstance(entry, str):
            n = _str_or_empty(entry)
            if n:
                label_names.append(n)
    team = derive_team_from_labels(label_names, team_label_map or {})

    return IssueSnapshot(
        title=_str_or_empty(linear_issue.get("title")),
        state=state_name,
        state_type=state_type,
        assignee=assignee_email,
        due_date=due_date,
        estimate=estimate_str,
        blockedby=blockedby_str,
        parent=parent,
        milestone=milestone_id,
        team=team,
    )


# ----- Round-trip with SyncLink -----------------------------------------------


def snapshot_to_sync_fields(snapshot: IssueSnapshot) -> dict[str, str]:
    """Convert a snapshot into the `snapshot_*` SyncLink-kwarg subset."""
    return {f"snapshot_{name}": getattr(snapshot, name) for name in SNAPSHOT_FIELD_NAMES}


def sync_fields_to_snapshot(link: SyncLink) -> IssueSnapshot:
    """Read the `snapshot_*` columns off a SyncLink into an IssueSnapshot."""
    kwargs = {name: getattr(link, f"snapshot_{name}") for name in SNAPSHOT_FIELD_NAMES}
    return IssueSnapshot(**kwargs)


# ----- Equality helpers ------------------------------------------------------
# Each compares two snapshot-shaped values (strings) and returns True if
# they should be treated as equivalent by the merge engine. Normalizes
# the common silent-mismatch sources (None vs "", whitespace, order).


def _norm(s: Any) -> str:
    if s is None:
        return ""
    return str(s).strip()


def titles_equal(a: Any, b: Any) -> bool:
    return _norm(a) == _norm(b)


def states_equal(a: Any, b: Any) -> bool:
    return _norm(a) == _norm(b)


def state_types_equal(a: Any, b: Any) -> bool:
    return _norm(a) == _norm(b)


def assignees_equal(a: Any, b: Any) -> bool:
    """Both None/"" treated equal; otherwise case-insensitive email match."""
    na, nb = _norm(a), _norm(b)
    return na.lower() == nb.lower()


def due_dates_equal(a: Any, b: Any) -> bool:
    """Strip trailing 'T00:00:00.000Z' style suffixes so a date-only
    ISO matches a midnight-UTC timestamp. Both None/"" treated equal."""
    na = _norm(a).split("T")[0]
    nb = _norm(b).split("T")[0]
    return na == nb


def estimates_equal(a: Any, b: Any) -> bool:
    """Treat 0, "", None, and "0" all as 'no estimate'. Numeric compare
    otherwise — '5' and '5.0' should both match."""
    def _to_float(v: Any) -> Optional[float]:
        n = _norm(v)
        if not n:
            return None
        try:
            return float(n)
        except ValueError:
            return None
    fa, fb = _to_float(a), _to_float(b)
    # Both empty / both None / both 0 → equal.
    if (fa or 0) == (fb or 0):
        return True
    return fa == fb


def blockedby_equal(a: Any, b: Any) -> bool:
    """Compare comma-separated blockedBy lists as sets — order doesn't
    matter, duplicates collapsed, whitespace stripped."""
    def _to_set(v: Any) -> frozenset[str]:
        n = _norm(v)
        if not n:
            return frozenset()
        return frozenset(part.strip() for part in n.split(",") if part.strip())
    return _to_set(a) == _to_set(b)


def parents_equal(a: Any, b: Any) -> bool:
    return _norm(a) == _norm(b)


def milestones_equal(a: Any, b: Any) -> bool:
    return _norm(a) == _norm(b)


def teams_equal(a: Any, b: Any) -> bool:
    """Case-insensitive team name compare; empty/None treated equal."""
    return _norm(a).lower() == _norm(b).lower()


# Field-name → equality function. Used by the merge engine to dispatch
# the right equality semantics per field.
FIELD_EQUALITY: dict[str, callable] = {
    "title": titles_equal,
    "state": states_equal,
    "state_type": state_types_equal,
    "assignee": assignees_equal,
    "due_date": due_dates_equal,
    "estimate": estimates_equal,
    "blockedby": blockedby_equal,
    "parent": parents_equal,
    "milestone": milestones_equal,
    "team": teams_equal,
}


def snapshots_equal(a: IssueSnapshot, b: IssueSnapshot) -> bool:
    """True if every field of both snapshots compares equal under its
    field-specific equality function."""
    for name in SNAPSHOT_FIELD_NAMES:
        eq = FIELD_EQUALITY[name]
        if not eq(getattr(a, name), getattr(b, name)):
            return False
    return True
