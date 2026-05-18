"""3-way merge engine for Linear ↔ workbook synchronization (Phase 2).

For every linked issue and every mergeable field, the engine compares
three values:

    W — current workbook value
    S — stored snapshot value (last known Linear, captured at last sync)
    L — current Linear value

and classifies the field per the spec's truth table:

    | W vs S | L vs S | Classification |
    |--------|--------|----------------|
    | equal  | equal  | NO_OP          |
    | changed| equal  | PUSH (W→L)     |
    | equal  | changed| PULL (L→W)     |
    | changed, W==L  | converged independently → CONVERGED |
    | changed, W≠L   | true conflict → CONFLICT, resolved per default policy |

`compute_sync_diff` walks workbook tasks + existing sync links + current
Linear payload and produces a `SyncDiff` — one `SyncRowDiff` per touched
row, each carrying:

    action ∈ {update, create, pull_new, archive, orphaned_link, unchanged}
    field_changes — per-mergeable-field FieldChange record (only when
                    action == update)
    conflicts    — subset of field_changes flagged CONFLICT (the user
                    may want to override the default-policy winner)

The engine is pure logic — no Sheets I/O, no MCP. It consumes:
    - workbook_tasks    : list[Task] from sheets.read_program_tasks
    - existing_links    : list[SyncLink] from sync_tab.read_links (carries
                          both sidecar + stored snapshot)
    - current_linear    : CpInput (the agent's normalized fetch)

and returns SyncDiff dataclasses for downstream consumption by push.py
+ pull.py / sync.py.
"""
from __future__ import annotations

import operator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from gantt_lib.cp.contracts import CpInput, CpInputIssue
from gantt_lib.dsl import parse_predecessors
from gantt_lib.linear.snapshot import (
    FIELD_EQUALITY,
    IssueSnapshot,
    SNAPSHOT_FIELD_NAMES,
    sync_fields_to_snapshot,
)
from gantt_lib.linear.sync_tab import SyncLink
from gantt_lib.model import Task


class FieldClassification(str, Enum):
    NO_OP = "no_op"
    PUSH = "push"
    PULL = "pull"
    CONVERGED = "converged"
    CONFLICT = "conflict"


# Fields the 3-way merge actually evaluates — both workbook and Linear
# carry a value for these. Sidecar-only fields (sidecar_predecessors,
# sidecar_percent, sidecar_notes, sidecar_team) live workbook-side
# only and never participate in cross-side merge.
#
# NOTE: `due_date` is intentionally absent. Workbook `task.end` is a
# cascade-computed projection that the engine rewrites every recalc;
# Linear `dueDate` is a user-set hard commitment target. Merging
# them in EITHER direction is broken:
#   - Push: writes computed projections to Linear as commitments
#     (loops on every cascade recompute)
#   - Pull: empty Linear dueDate wipes workbook's cascade-computed
#     end (was discovered live during P2-T10 acceptance — broke the
#     gantt bars + default-team CF coloring on LINEAR_TEST)
# Phase 2 v1: due_date is excluded from the cross-side merge entirely.
# See GH issue #7 for the Phase 2.1 revisit (Task gets a
# `manual_end_anchor: bool` distinguishing user-set hard dates from
# cascade outputs; that lets us round-trip dueDate the right way).
MERGEABLE_FIELDS: tuple[str, ...] = (
    "title",
    "state",
    "assignee",
    "estimate",
    "blockedby",
    "parent",
)

# Default-policy field winners on true conflict. Per the spec table:
LINEAR_WINS: frozenset[str] = frozenset({
    "title", "state", "state_type", "assignee", "due_date", "parent", "milestone",
})
WORKBOOK_WINS: frozenset[str] = frozenset({
    "blockedby", "predecessors", "percent", "notes", "team",
})
# `estimate` is special: workbook wins ONLY if the workbook value is
# non-default (non-zero, non-empty) — otherwise Linear wins. See
# `resolve_conflict`.


# ----- Per-field merge primitive ---------------------------------------------


def classify_field(
    workbook_value: Any,
    snapshot_value: Any,
    linear_value: Any,
    *,
    equality_fn: Callable[[Any, Any], bool] = operator.eq,
) -> FieldClassification:
    """The 5-way classification of a single field per the 3-way merge truth
    table. `equality_fn` lets the caller pass a field-specific normalizer
    (e.g. `assignees_equal` for case-insensitive email match)."""
    w_eq_s = equality_fn(workbook_value, snapshot_value)
    l_eq_s = equality_fn(linear_value, snapshot_value)
    if w_eq_s and l_eq_s:
        return FieldClassification.NO_OP
    if not w_eq_s and l_eq_s:
        return FieldClassification.PUSH
    if w_eq_s and not l_eq_s:
        return FieldClassification.PULL
    # Both sides changed since snapshot.
    if equality_fn(workbook_value, linear_value):
        return FieldClassification.CONVERGED
    return FieldClassification.CONFLICT


def resolve_conflict(
    field_name: str,
    workbook_value: Any,
    linear_value: Any,
) -> tuple[Any, str]:
    """Apply the per-field default winner policy. Returns
    (resolved_value, source) where source ∈ {'linear', 'workbook'}."""
    if field_name in LINEAR_WINS:
        return (linear_value, "linear")
    if field_name in WORKBOOK_WINS:
        return (workbook_value, "workbook")
    if field_name == "estimate":
        # Workbook wins if it has a real (non-default) value; the user
        # often refines estimates post-pull and doesn't want them
        # silently clobbered.
        try:
            wf = float(str(workbook_value).strip() or "0")
        except ValueError:
            wf = 0.0
        if wf > 0:
            return (workbook_value, "workbook")
        return (linear_value, "linear")
    # Unknown field — fail safe by letting Linear win (preserves
    # external-source-of-truth bias on uncategorized fields).
    return (linear_value, "linear")


# ----- Diff dataclasses ------------------------------------------------------


@dataclass(frozen=True)
class FieldChange:
    """One field's 3-way merge outcome for one issue."""
    field: str
    workbook_value: Any
    snapshot_value: Any
    linear_value: Any
    classification: FieldClassification
    resolved_to_value: Any  # the post-merge value (None for NO_OP/CONVERGED)
    resolved_to_source: str  # 'linear' | 'workbook' | 'n/a'


@dataclass
class SyncRowDiff:
    """Per-issue summary of what's changing this sync.

    action values:
      update           — at least one mergeable field changed
      create           — workbook has a row with no linear link; needs new Linear issue
      pull_new         — Linear has an issue with no workbook row; needs new workbook row
      archive          — workbook row was deleted; archive corresponding Linear issue
      orphaned_link    — Linear issue gone (deleted/archived/moved); workbook row kept
      unchanged        — linked + 3-way merge found nothing to do
    """
    wbs_id: str  # may be "" for pull_new (to be assigned during apply)
    linear_id: str  # may be "" for create (to be assigned by Linear)
    title: str
    action: str
    field_changes: list[FieldChange] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)  # field names with CONFLICT class


@dataclass
class SyncDiff:
    """Top-level: SyncRowDiff per touched row, plus warnings."""
    program: str
    rows: list[SyncRowDiff]
    warnings: list = field(default_factory=list)

    def by_action(self, action: str) -> list[SyncRowDiff]:
        return [r for r in self.rows if r.action == action]


# ----- Workbook → snapshot translation ---------------------------------------
# Turns a workbook Task (+ context) into a snapshot-shape dict so the
# 3-way merge can compare it to the stored snapshot and current Linear.


def _wbs_to_linear(
    wbs_id: str, linear_by_wbs: dict[str, str]
) -> Optional[str]:
    """Translate a WBS id to its corresponding linear_id via sync links.
    Returns None if no link exists (a new workbook task with no Linear ID yet)."""
    return linear_by_wbs.get(wbs_id)


def _workbook_blockedby(
    task: Task, linear_by_wbs: dict[str, str]
) -> str:
    """Build the Linear-style comma-separated blockedby string from the
    workbook task's predecessor DSL, translated via `linear_by_wbs`.
    Unmapped predecessors (workbook-only tasks) are dropped — they don't
    appear in Linear's blockedBy until they're created."""
    if not task.predecessors:
        return ""
    try:
        preds = parse_predecessors(task.predecessors)
    except Exception:
        return ""  # malformed DSL — treat as no blockers for comparison purposes
    linear_ids = []
    for p in preds:
        lid = _wbs_to_linear(p.id, linear_by_wbs)
        if lid:
            linear_ids.append(lid)
    return ",".join(linear_ids)


def _workbook_parent(
    task: Task, workbook_tasks: list[Task], linear_by_wbs: dict[str, str]
) -> str:
    """Derive the workbook task's Linear parent_id by stripping the last
    WBS segment and looking up the parent's linear_id. Returns "" for
    top-level tasks or when the parent isn't linked."""
    if "." not in task.id:
        return ""
    parent_wbs = ".".join(task.id.split(".")[:-1])
    return _wbs_to_linear(parent_wbs, linear_by_wbs) or ""


def workbook_task_to_snapshot(
    task: Task, *, linear_by_wbs: dict[str, str], workbook_tasks: list[Task]
) -> IssueSnapshot:
    """Materialize the workbook's view of an issue as an IssueSnapshot
    so it's directly comparable to stored/current Linear snapshots."""
    due_date = task.end.isoformat() if task.end else ""
    return IssueSnapshot(
        title=task.name,
        state=task.status,
        state_type="",  # workbook doesn't track type separately
        assignee=task.owner,
        due_date=due_date,
        estimate=str(task.duration) if task.duration else "",
        blockedby=_workbook_blockedby(task, linear_by_wbs),
        parent=_workbook_parent(task, workbook_tasks, linear_by_wbs),
        milestone="",  # workbook doesn't track milestone
    )


def cp_issue_to_snapshot(
    issue: CpInputIssue, *, blockedby_ids: list[str], milestone_id: str = ""
) -> IssueSnapshot:
    """Build a snapshot from the agent's normalized payload (current Linear).
    `blockedby_ids` and `milestone_id` must be supplied by the caller from
    the surrounding payload context (edges + milestone list)."""
    due_date = issue.end_anchor.isoformat() if issue.end_anchor else ""
    return IssueSnapshot(
        title=issue.title,
        state=issue.state,
        state_type="",  # CpInputIssue doesn't carry state_type today
        assignee=issue.assignee,
        due_date=due_date,
        estimate=str(int(issue.estimate_days)) if issue.estimate_days else "",
        blockedby=",".join(blockedby_ids),
        parent=issue.parent_linear_id or "",
        milestone=milestone_id,
    )


# ----- Per-issue 3-way merge -------------------------------------------------


def _merge_row(
    *,
    wbs_id: str,
    linear_id: str,
    workbook_snapshot: IssueSnapshot,
    stored_snapshot: IssueSnapshot,
    linear_snapshot: IssueSnapshot,
    title_for_display: str,
) -> SyncRowDiff:
    """Run the 3-way merge over MERGEABLE_FIELDS for one linked issue.
    Returns a SyncRowDiff with action=update if anything changed, else
    action=unchanged."""
    field_changes: list[FieldChange] = []
    conflicts: list[str] = []

    for fname in MERGEABLE_FIELDS:
        equality_fn = FIELD_EQUALITY.get(fname, operator.eq)
        W = getattr(workbook_snapshot, fname)
        S = getattr(stored_snapshot, fname)
        L = getattr(linear_snapshot, fname)
        cls = classify_field(W, S, L, equality_fn=equality_fn)

        if cls == FieldClassification.NO_OP:
            continue

        if cls == FieldClassification.PUSH:
            resolved, src = W, "workbook"
        elif cls == FieldClassification.PULL:
            resolved, src = L, "linear"
        elif cls == FieldClassification.CONVERGED:
            # Both sides reached the same new value — adopt it; no
            # cross-write needed, just snapshot refresh.
            resolved, src = W, "n/a"
        else:  # CONFLICT
            resolved, src = resolve_conflict(fname, W, L)
            conflicts.append(fname)

        field_changes.append(
            FieldChange(
                field=fname,
                workbook_value=W,
                snapshot_value=S,
                linear_value=L,
                classification=cls,
                resolved_to_value=resolved,
                resolved_to_source=src,
            )
        )

    if field_changes:
        return SyncRowDiff(
            wbs_id=wbs_id,
            linear_id=linear_id,
            title=title_for_display,
            action="update",
            field_changes=field_changes,
            conflicts=conflicts,
        )
    return SyncRowDiff(
        wbs_id=wbs_id,
        linear_id=linear_id,
        title=title_for_display,
        action="unchanged",
    )


# ----- Top-level orchestrator ------------------------------------------------


def compute_sync_diff(
    *,
    program: str,
    workbook_tasks: list[Task],
    existing_links: list[SyncLink],
    current_linear: CpInput,
) -> SyncDiff:
    """Compute the full SyncDiff for one program.

    Classifies every row touched by the sync into one of six actions:
    update / create / pull_new / archive / orphaned_link / unchanged.
    Per-row field_changes carry the 3-way merge outcome for each
    mergeable field, with the default-policy winner pre-resolved and any
    true conflicts flagged for optional user override.
    """
    # Index everything by primary key for fast lookup.
    task_by_wbs: dict[str, Task] = {t.id: t for t in workbook_tasks}
    issue_by_linear: dict[str, CpInputIssue] = {
        iss.linear_id: iss for iss in current_linear.issues
    }

    # blockedBy edges grouped per `to_linear_id`.
    blockers_by_linear: dict[str, list[str]] = {}
    for edge in current_linear.edges:
        blockers_by_linear.setdefault(edge.to_linear_id, []).append(edge.from_linear_id)

    # Linkage indices.
    link_by_wbs: dict[str, SyncLink] = {l.wbs_id: l for l in existing_links}
    link_by_linear: dict[str, SyncLink] = {l.linear_id: l for l in existing_links}
    linear_by_wbs: dict[str, str] = {l.wbs_id: l.linear_id for l in existing_links}

    rows: list[SyncRowDiff] = []
    handled_wbs: set[str] = set()
    handled_linear: set[str] = set()

    # 1) Walk existing links — update / archive / orphaned_link / unchanged.
    for link in existing_links:
        handled_wbs.add(link.wbs_id)
        handled_linear.add(link.linear_id)

        task = task_by_wbs.get(link.wbs_id)
        issue = issue_by_linear.get(link.linear_id)

        if task is None and issue is None:
            # Both sides gone — stale link, no row diff (sync will just
            # drop it from _LinearSync on next upsert).
            continue
        if task is None:
            # Workbook deleted, Linear still has the issue → archive.
            rows.append(SyncRowDiff(
                wbs_id=link.wbs_id,
                linear_id=link.linear_id,
                title=issue.title,
                action="archive",
            ))
            continue
        if issue is None:
            # Linear gone, workbook kept → orphan; preserve workbook,
            # leave link in tombstone state for user awareness.
            rows.append(SyncRowDiff(
                wbs_id=link.wbs_id,
                linear_id=link.linear_id,
                title=task.name,
                action="orphaned_link",
            ))
            continue

        # Both sides present — 3-way merge.
        workbook_snap = workbook_task_to_snapshot(
            task,
            linear_by_wbs=linear_by_wbs,
            workbook_tasks=workbook_tasks,
        )
        linear_snap = cp_issue_to_snapshot(
            issue,
            blockedby_ids=blockers_by_linear.get(link.linear_id, []),
        )
        stored_snap = sync_fields_to_snapshot(link)
        rows.append(_merge_row(
            wbs_id=link.wbs_id,
            linear_id=link.linear_id,
            workbook_snapshot=workbook_snap,
            stored_snapshot=stored_snap,
            linear_snapshot=linear_snap,
            title_for_display=task.name,
        ))

    # 2) Workbook tasks without a sync link — create in Linear.
    for task in workbook_tasks:
        if task.id in handled_wbs:
            continue
        rows.append(SyncRowDiff(
            wbs_id=task.id,
            linear_id="",
            title=task.name,
            action="create",
        ))

    # 3) Linear issues not yet linked — pull-new (workbook gets a new row).
    for issue in current_linear.issues:
        if issue.linear_id in handled_linear:
            continue
        rows.append(SyncRowDiff(
            wbs_id="",
            linear_id=issue.linear_id,
            title=issue.title,
            action="pull_new",
        ))

    return SyncDiff(program=program, rows=rows)
