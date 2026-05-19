"""Top-level orchestrator for `gantt linear-sync`.

Composes the Phase-1 pull machinery + Phase-2 merge engine + push
descriptor builder into a single function that handles all three
direction modes (pull / push / both).

Flow:
  1. Auto-migrate `_LinearSync` to Phase-2 schema if needed (idempotent)
  2. Open the program tab (raises ProgramTabMissingError if absent)
  3. Read current workbook tasks + existing sync links
  4. If `force=True`: drop existing links so every Linear issue is
     treated as new
  5. Compute SyncDiff via the 3-way merge engine
  6. Build push-side MCP request descriptors (if direction in {push,both})
  7. If not dry_run:
       - Apply pull-side workbook writes (if direction in {pull,both})
       - Optimistically refresh `_LinearSync` snapshot columns for
         non-create rows (push-side mcp_requests are dispatched by the
         agent; we assume they'll succeed and re-resolve next sync if
         they don't)
  8. Return SyncResult — JSON-serializable summary + MCP request list
     for the agent to dispatch

Optimistic-apply caveat: for `update` and `archive` rows, sync_tab
is written assuming the agent's subsequent MCP calls succeed. If they
fail, the next sync's 3-way merge will detect the divergence (Linear
state ≠ stored snapshot) and re-resolve. For `create` rows specifically
the sync_tab can't be written yet (we don't know the new linear_id
until pass-1 returns), so the agent must call back with the create
results — handled in a follow-up CLI call, not this function.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from gantt_lib import schema, sheets
from gantt_lib.cp.contracts import CpInput
from gantt_lib.linear.merge import (
    FieldChange,
    FieldClassification,
    MERGEABLE_FIELDS,
    SyncDiff,
    SyncRowDiff,
    compute_sync_diff,
    cp_issue_to_snapshot,
    workbook_task_to_snapshot,
)
from gantt_lib.linear.push import (
    MCPRequest,
    build_push_requests,
)
from gantt_lib.linear.snapshot import (
    IssueSnapshot,
    SNAPSHOT_FIELD_NAMES,
    snapshot_to_sync_fields,
    sync_fields_to_snapshot,
)
from gantt_lib.linear.sync_tab import (
    SyncLink,
    delete_links,
    migrate_sync_tab,
    read_links,
    upsert_links,
)
from gantt_lib.model import Task, next_wbs_id, wbs_sort_key


class ProgramTabMissingError(RuntimeError):
    """Raised when the program tab doesn't exist. Same semantics as the
    Phase-1 pull error — caller (agent) should ask the user whether to
    run `gantt program new <name>` first."""


# ----- Result shape ----------------------------------------------------------


@dataclass
class SyncResult:
    """Top-level outcome of one sync invocation. JSON-serializable so the
    CLI can emit it to stdout for the agent to render + execute."""
    ok: bool
    program: str
    direction: str
    dry_run: bool
    applied_at: str
    summary: dict[str, int]
    diffs: list[dict]
    mcp_requests: list[dict]
    warnings: list[dict]


# ----- Direction filtering ---------------------------------------------------


def _is_pull_relevant(row: SyncRowDiff) -> bool:
    """True if this row has any change the pull-direction applies."""
    if row.action == "pull_new":
        return True
    if row.action == "orphaned_link":
        return False  # nothing to apply; informational only
    if row.action != "update":
        return False
    for fc in row.field_changes:
        if fc.classification == FieldClassification.PULL:
            return True
        if fc.classification == FieldClassification.CONFLICT and fc.resolved_to_source == "linear":
            return True
        # CONVERGED also needs a workbook-side adjustment if the workbook's
        # current value differs from the resolved (which it might if W==L
        # was reached but workbook still holds the pre-change snapshot —
        # rare; safe to fall through and not apply).
    return False


def _is_push_relevant(row: SyncRowDiff) -> bool:
    """True if this row generates an MCP write (push, create, archive)."""
    if row.action in ("create", "archive"):
        return True
    if row.action != "update":
        return False
    for fc in row.field_changes:
        if fc.classification == FieldClassification.PUSH:
            return True
        if fc.classification == FieldClassification.CONFLICT and fc.resolved_to_source == "workbook":
            return True
    return False


# ----- Summary counts --------------------------------------------------------


def _summary_counts(diff: SyncDiff, mcp_requests: list[MCPRequest]) -> dict[str, int]:
    """Action-level counts for the result line."""
    counts = {
        "pushed": 0,
        "pulled": 0,
        "created": 0,
        "archived": 0,
        "pull_new": 0,
        "orphaned_link": 0,
        "unchanged": 0,
        "conflicts_resolved": 0,
    }
    for row in diff.rows:
        if row.action == "unchanged":
            counts["unchanged"] += 1
        elif row.action == "pull_new":
            counts["pull_new"] += 1
        elif row.action == "orphaned_link":
            counts["orphaned_link"] += 1
        elif row.action == "archive":
            counts["archived"] += 1
        elif row.action == "create":
            counts["created"] += 1
        elif row.action == "update":
            # Bucket the per-field directions.
            has_push = any(
                fc.classification == FieldClassification.PUSH
                or (fc.classification == FieldClassification.CONFLICT and fc.resolved_to_source == "workbook")
                for fc in row.field_changes
            )
            has_pull = any(
                fc.classification == FieldClassification.PULL
                or (fc.classification == FieldClassification.CONFLICT and fc.resolved_to_source == "linear")
                for fc in row.field_changes
            )
            if has_push:
                counts["pushed"] += 1
            if has_pull:
                counts["pulled"] += 1
            counts["conflicts_resolved"] += len(row.conflicts)
    return counts


# ----- Serialization helpers -------------------------------------------------


def _serialize_value(v: Any) -> Any:
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def _row_diff_to_dict(row: SyncRowDiff) -> dict:
    return {
        "wbs_id": row.wbs_id,
        "linear_id": row.linear_id,
        "title": row.title,
        "action": row.action,
        "conflicts": list(row.conflicts),
        "field_changes": [
            {
                "field": fc.field,
                "workbook": _serialize_value(fc.workbook_value),
                "snapshot": _serialize_value(fc.snapshot_value),
                "linear": _serialize_value(fc.linear_value),
                "classification": fc.classification.value,
                "resolved_to": _serialize_value(fc.resolved_to_value),
                "resolved_source": fc.resolved_to_source,
            }
            for fc in row.field_changes
        ],
    }


def _mcp_request_to_dict(req: MCPRequest) -> dict:
    return {
        "tool": req.tool,
        "kwargs": req.kwargs,
        "description": req.description,
        "pass_number": req.pass_number,
        "pass_1_create_for_wbs": req.pass_1_create_for_wbs,
        "pass_1_placeholders": list(req.pass_1_placeholders),
    }


# ----- Pull-side application -------------------------------------------------


def _apply_pull_writes(
    *,
    program_ws,
    diff: SyncDiff,
    payload: CpInput,
    workbook_tasks: list[Task],
    existing_links: list[SyncLink],
) -> None:
    """Apply pull-direction changes to the program tab. For each
    pull-relevant row, update or append the workbook Task. sync_tab
    refresh is handled separately by `_compute_post_sync_links`."""
    task_by_wbs = {t.id: t for t in workbook_tasks}
    issue_by_linear = {iss.linear_id: iss for iss in payload.issues}
    wbs_by_linear: dict[str, str] = {l.linear_id: l.wbs_id for l in existing_links}

    # 1) Updates: per-field PULL or CONFLICT-linear-wins writes.
    for row in diff.rows:
        if row.action != "update" or not _is_pull_relevant(row):
            continue
        task = task_by_wbs.get(row.wbs_id)
        if task is None:
            continue
        for fc in row.field_changes:
            apply_to_workbook = (
                fc.classification == FieldClassification.PULL
                or (fc.classification == FieldClassification.CONFLICT and fc.resolved_to_source == "linear")
            )
            if not apply_to_workbook:
                continue
            if fc.field == "milestone":
                _apply_milestone_to_task(task, fc.resolved_to_value, wbs_by_linear)
            else:
                _apply_field_to_task(task, fc.field, fc.resolved_to_value)

        # blockedby: augment workbook predecessors with any new Linear
        # blockers, never overwrite. Preserves user-managed DSL richness
        # (lags, SS/SF, parent-refs) while still picking up newly-added
        # Linear blockers on the next sync.
        _augment_predecessors_from_linear(task, row.field_changes, wbs_by_linear)

        # Write the updated row back to the sheet.
        row_idx = sheets.find_task_row(program_ws, row.wbs_id)
        if row_idx is not None:
            sheets.update_task_data(program_ws, row_idx, task)

    # 2) pull_new: build all new rows through cp/adapter + cascade so the
    # output is internally coherent (Start+Duration=End in working days,
    # predecessor DSL strings populated from Linear's blockedBy edges).
    # The deleted pull.py (P2-T7) routed through cp/adapter for the same
    # reason; bypassing it leaves Start/End/Duration mutually inconsistent
    # and Predecessors empty.
    pull_new_wbs = _assign_wbs_for_pull_new(payload, existing_links)
    pull_new_tasks_by_linear = _build_pull_new_tasks(
        payload=payload,
        pull_new_wbs=pull_new_wbs,
        existing_links=existing_links,
    )
    # Sort pull_new rows by WBS so sub-issues physically appear below
    # their parent on the sheet (e.g. WBS "2.1" right after "2", before
    # "3"). Without this, sheets.append_task writes in diff order — which
    # follows Linear's list_issues ordering, not WBS hierarchy.
    pull_new_rows_sorted = sorted(
        (r for r in diff.rows if r.action == "pull_new"),
        key=lambda r: wbs_sort_key(
            (pull_new_tasks_by_linear.get(r.linear_id).id
             if pull_new_tasks_by_linear.get(r.linear_id) is not None
             else pull_new_wbs.get(r.linear_id, "zzz"))
        ),
    )
    for row in pull_new_rows_sorted:
        issue = issue_by_linear.get(row.linear_id)
        if issue is None:
            continue
        new_task = pull_new_tasks_by_linear.get(issue.linear_id)
        if new_task is None:
            # Defensive fallback when cp/adapter rejected the issue (e.g.
            # malformed edge). Emit a minimal Task so we don't lose the row.
            fallback_wbs = pull_new_wbs.get(issue.linear_id) or next_wbs_id(workbook_tasks)
            new_task = Task(
                id=fallback_wbs,
                level=1 + fallback_wbs.count("."),
                name=issue.title,
                owner=issue.assignee,
                duration=int(issue.estimate_days) if issue.estimate_days else 0,
                status=issue.state,
                milestone=issue.is_milestone,
                start=issue.start_anchor or payload.config.today,
                end=issue.end_anchor,
            )
        new_task.linear_url = issue.linear_url or None
        sheets.append_task(program_ws, new_task)
        # Update our in-memory workbook_tasks so subsequent next_wbs_id calls
        # see the new row (matters when multiple pull_news in one sync).
        workbook_tasks.append(new_task)
        # Stash the assigned wbs back onto the SyncRowDiff so the caller
        # can write the corresponding sync_tab row.
        row.wbs_id = new_task.id

    # 3) Augment milestone-row Predecessors with member-issue references.
    # Linear's ProjectMilestone has no blockedBy, but it semantically
    # "depends on" its member issues. Walk issue.milestone_id, invert to
    # milestone → [members], and write `<wbs>FS` entries on each
    # milestone row's Predecessors (additive — preserves user edits).
    # Runs AFTER pull_new so freshly-pulled members have assigned WBS ids
    # in the wbs_by_linear index.
    wbs_by_linear_after_pull = dict(wbs_by_linear)
    for r in pull_new_rows_sorted:
        if r.linear_id and r.wbs_id:
            wbs_by_linear_after_pull[r.linear_id] = r.wbs_id
    modified_milestones = _augment_milestone_predecessors_from_linear(
        workbook_tasks=workbook_tasks,
        payload=payload,
        wbs_by_linear=wbs_by_linear_after_pull,
    )
    for ms_task in modified_milestones:
        row_idx = sheets.find_task_row(program_ws, ms_task.id)
        if row_idx is not None:
            sheets.update_task_data(program_ws, row_idx, ms_task)

    # 4) Refresh Sheets row-grouping (+/- gutter) so sub-issues are
    # collapsible under their parent. Only fires if any pull_new ran;
    # update-only syncs leave existing groups intact. Best-effort —
    # silently skips on API errors so a transient Sheets glitch doesn't
    # abort an otherwise-successful sync.
    if pull_new_rows_sorted:
        try:
            ss = getattr(program_ws, "spreadsheet", None)
            if ss is not None:
                sheets.refresh_row_groups(ss, program_ws)
        except Exception:
            pass


def _build_pull_new_tasks(
    *,
    payload: CpInput,
    pull_new_wbs: dict[str, str],
    existing_links: list[SyncLink],
) -> dict[str, Task]:
    """Run cp/adapter + cascade over the pulled CpInput so the new Tasks
    are internally coherent: Start + Duration = End (in working days),
    predecessors strung from Linear's blockedBy edges, level derived
    from the WBS hierarchy. Returns `{linear_id: Task}` covering only
    the pull_new issues (existing-link issues are ignored).

    Edges that reference an existing-link issue from a pull_new issue
    (or vice versa) get translated via the existing_links mapping, so a
    new task blocked by an already-linked one gets a predecessor DSL
    pointing at the existing workbook WBS. Cascade is best-effort — on
    cycle / unanchored / missing-predecessor errors we return the
    pre-cascade tasks (which still carry coherent Linear values).
    """
    from gantt_lib.cascade import (
        cascade,
        CycleError,
        UnanchoredError,
        MissingPredecessorError,
    )
    from gantt_lib.cp.adapter import cp_input_to_program

    if not pull_new_wbs:
        return {}

    existing_by_linear = {l.linear_id: l.wbs_id for l in existing_links}
    full_wbs: dict[str, str] = {**existing_by_linear, **pull_new_wbs}
    # Ensure every CpInput issue has an assignment — defensive: missing
    # ones get a fresh top-level slot to avoid cp/adapter raising.
    for iss in payload.issues:
        if iss.linear_id not in full_wbs:
            taken = set(full_wbs.values())
            n = 1
            while str(n) in taken:
                n += 1
            full_wbs[iss.linear_id] = str(n)

    try:
        build = cp_input_to_program(payload, wbs_assignments=full_wbs)
    except Exception:
        return {}

    # Cascade is best-effort. On failure (cycle, unanchored, missing
    # predecessor) the tasks still carry sensible defaults from the
    # adapter (start anchored to today, duration from estimate or
    # default, predecessors populated). The downstream recalc will
    # report the same error if it persists.
    try:
        cascade(build.program)
    except (CycleError, UnanchoredError, MissingPredecessorError):
        pass

    pull_new_linears = set(pull_new_wbs.keys())
    out: dict[str, Task] = {}
    for task in build.program.tasks:
        lid = build.wbs_to_linear.get(task.id)
        if lid in pull_new_linears:
            out[lid] = task
    return out


def _assign_wbs_for_pull_new(
    payload: CpInput, existing_links: list[SyncLink]
) -> dict[str, str]:
    """For each pull_new candidate (Linear issue with no existing link),
    assign a WBS id that respects parent_linear_id. Existing-linked issues
    keep their assigned WBS — new sub-issues nest underneath as
    `<parent_wbs>.<next_sibling_int>`.

    Returns `{linear_id: wbs_id}` covering only NEW assignments; callers
    should fall back to `next_wbs_id` for any issue not in the map.
    """
    existing_by_linear = {l.linear_id: l.wbs_id for l in existing_links}
    children: dict[Optional[str], list[CpInputIssue]] = {}
    for iss in payload.issues:
        children.setdefault(iss.parent_linear_id, []).append(iss)

    assignments: dict[str, str] = dict(existing_by_linear)

    def used_under(parent_wbs: Optional[str]) -> set[int]:
        prefix = "" if parent_wbs is None else f"{parent_wbs}."
        used: set[int] = set()
        for wbs in assignments.values():
            if not wbs.startswith(prefix):
                continue
            rest = wbs[len(prefix):]
            if "." in rest:
                continue
            try:
                used.add(int(rest))
            except ValueError:
                continue
        return used

    def assign(parent_linear: Optional[str], parent_wbs: Optional[str]) -> None:
        for iss in children.get(parent_linear, []):
            if iss.linear_id in existing_by_linear:
                wbs = existing_by_linear[iss.linear_id]
            else:
                used = used_under(parent_wbs)
                next_n = (max(used) + 1) if used else 1
                wbs = str(next_n) if parent_wbs is None else f"{parent_wbs}.{next_n}"
                assignments[iss.linear_id] = wbs
            assign(iss.linear_id, wbs)

    assign(None, None)
    return {
        lid: wbs for lid, wbs in assignments.items()
        if lid not in existing_by_linear
    }


def _apply_field_to_task(task: Task, field_name: str, value: Any) -> None:
    """Mutate `task` to set the given snapshot-field value in workbook-native form."""
    if field_name == "title":
        task.name = str(value or "")
    elif field_name == "state":
        task.status = str(value or "")
    elif field_name == "assignee":
        task.owner = str(value or "")
    elif field_name == "due_date":
        from datetime import date as _date
        if not value:
            task.end = None
        elif isinstance(value, _date):
            task.end = value
        else:
            try:
                task.end = _date.fromisoformat(str(value).split("T")[0])
            except ValueError:
                pass  # leave as-is on bad input
    elif field_name == "estimate":
        try:
            task.duration = int(float(value)) if value not in (None, "") else 0
        except (TypeError, ValueError):
            pass
    elif field_name == "team":
        task.team = str(value or "")
    # blockedby, milestone: handled outside this function — they need
    # link-table context to translate linear_ids → workbook WBS ids.
    # See `_augment_predecessors_from_linear` and `_apply_milestone_to_task`.
    # parent: skip — would require WBS-hierarchy restructuring on pull,
    # out of scope.


def _apply_milestone_to_task(
    task: Task,
    linear_milestone_id: Any,
    wbs_by_linear: dict[str, str],
) -> None:
    """Set `task.milestone_link` to the workbook WBS id of the milestone
    row whose linear_id matches the supplied "MS-<uuid>" value.

    Empty value clears the link. Unresolvable values (Linear milestone
    isn't materialized as a workbook row yet) leave the existing link
    intact — next sync after the milestone row is created will resolve.
    """
    value = str(linear_milestone_id or "").strip()
    if not value:
        task.milestone_link = ""
        return
    wbs = wbs_by_linear.get(value)
    if wbs:
        task.milestone_link = wbs
    # else: silently keep the existing link; will resolve later.


def _augment_predecessors_from_linear(
    task: Task,
    field_changes: list[FieldChange],
    wbs_by_linear: dict[str, str],
) -> None:
    """If Linear's current blockedby includes blockers not represented in
    the workbook task's predecessor DSL, append them as bare `FS+0`
    entries — preserving any lags / SS / SF relations the user wrote in
    the workbook.

    Behavior:
    - Augment only on PULL or CONFLICT (workbook-wins) classifications
    - Always APPEND, never remove — Linear-side removals are ignored on
      pull to protect user-managed DSL
    - New entries default to Finish-to-Start with zero lag (the lossy
      subset Linear's blockedBy can carry); user can edit them later

    Translates Linear ids → WBS ids via `wbs_by_linear`. Unresolvable
    Linear ids (e.g. issues not yet linked to a workbook row) are
    silently skipped — they'll surface on a future sync once linked.
    """
    from gantt_lib.dsl import (
        Predecessor,
        format_predecessors,
        parse_predecessors,
    )

    fc = next((c for c in field_changes if c.field == "blockedby"), None)
    if fc is None:
        return
    if fc.classification not in (FieldClassification.PULL, FieldClassification.CONFLICT):
        return
    if fc.classification == FieldClassification.CONFLICT and fc.resolved_to_source != "workbook":
        # Linear-wins conflict already handled by _apply_field_to_task — but
        # blockedby never goes that way today (it's WORKBOOK_WINS). Defensive.
        return

    linear_str = fc.linear_value or ""
    linear_set = {t.strip() for t in str(linear_str).split(",") if t.strip()}
    if not linear_set:
        return

    # Translate Linear ids → WBS ids; drop unresolvable ones.
    new_wbs_ids: list[str] = []
    for lid in linear_set:
        wbs = wbs_by_linear.get(lid)
        if wbs:
            new_wbs_ids.append(wbs)

    if not new_wbs_ids:
        return

    try:
        current_preds = parse_predecessors(task.predecessors or "")
    except Exception:
        current_preds = []
    have = {p.id for p in current_preds}

    additions = [
        Predecessor(id=wbs, rel="FS", lag=0)
        for wbs in new_wbs_ids
        if wbs not in have
    ]
    if not additions:
        return

    task.predecessors = format_predecessors(current_preds + additions)


def _augment_milestone_predecessors_from_linear(
    *,
    workbook_tasks: list[Task],
    payload: CpInput,
    wbs_by_linear: dict[str, str],
) -> list[Task]:
    """For each milestone row already linked to a Linear milestone,
    augment its Predecessors with `<wbs>FS` entries for every member
    issue (the ones where `issue.milestone_id` points at this milestone).

    Linear's `ProjectMilestone` has no `blockedBy` field — but a milestone
    is conceptually "achieved when its constituent issues are complete,"
    so the predecessors *are* the members. The data lives per-issue in
    `issue.milestone_id`; this function inverts it to `milestone →
    [members]` and writes them as additive workbook predecessors.

    Additive only — preserves user-added entries (lags, SS/FF/SF, refs
    to non-member tasks). Removals on the Linear side (member's
    `milestone_id` cleared) are NOT reflected in the workbook, same
    conservative policy as `_augment_predecessors_from_linear`.

    Returns the list of milestone tasks that were modified. Caller is
    responsible for writing them back to the sheet.
    """
    from gantt_lib.dsl import (
        Predecessor,
        format_predecessors,
        parse_predecessors,
    )

    # Invert: milestone_id → [member linear_ids]
    members_by_milestone: dict[str, list[str]] = {}
    for issue in payload.issues:
        if issue.is_milestone:
            continue  # milestone rows aren't members of themselves
        if not issue.milestone_id:
            continue
        members_by_milestone.setdefault(issue.milestone_id, []).append(issue.linear_id)

    if not members_by_milestone:
        return []

    task_by_wbs = {t.id: t for t in workbook_tasks}
    modified: list[Task] = []

    for ms_linear_id, member_lids in members_by_milestone.items():
        ms_wbs = wbs_by_linear.get(ms_linear_id)
        if not ms_wbs:
            continue  # milestone row not in workbook yet
        ms_task = task_by_wbs.get(ms_wbs)
        if ms_task is None:
            continue

        member_wbs_ids = [w for w in (wbs_by_linear.get(lid) for lid in member_lids) if w]
        if not member_wbs_ids:
            continue

        try:
            current_preds = parse_predecessors(ms_task.predecessors or "")
        except Exception:
            current_preds = []
        have = {p.id for p in current_preds}

        additions = [
            Predecessor(id=wbs, rel="FS", lag=0)
            for wbs in member_wbs_ids
            if wbs not in have
        ]
        if not additions:
            continue

        ms_task.predecessors = format_predecessors(current_preds + additions)
        modified.append(ms_task)

    return modified


# ----- Post-sync sync_tab links ----------------------------------------------


def _compute_post_sync_links(
    *,
    diff: SyncDiff,
    payload: CpInput,
    workbook_tasks: list[Task],
    existing_links: list[SyncLink],
    program: str,
    applied_at: str,
) -> list[SyncLink]:
    """Build the SyncLink list reflecting the expected post-sync state.

    For `update` rows: snapshot refreshes to the post-merge resolved values.
    For `pull_new` rows: new link with snapshot = current Linear state.
    For `archive` rows: leave link as tombstone (keep but update timestamp).
    For `create` rows: SKIPPED — sync_tab can't be written until the agent
    reports back the new linear_id from pass-1 dispatch.
    For `orphaned_link` rows: leave as tombstone (link stays, snapshot stale).
    """
    issue_by_linear = {iss.linear_id: iss for iss in payload.issues}
    blockers_by_linear: dict[str, list[str]] = {}
    for edge in payload.edges:
        blockers_by_linear.setdefault(edge.to_linear_id, []).append(edge.from_linear_id)
    link_by_linear = {l.linear_id: l for l in existing_links}
    linear_by_wbs = {l.wbs_id: l.linear_id for l in existing_links}
    task_by_wbs = {t.id: t for t in workbook_tasks}

    new_links: list[SyncLink] = []
    handled_linear: set[str] = set()

    for row in diff.rows:
        if row.action == "create":
            # Skip — needs follow-up apply-create-results call.
            continue

        if row.action == "pull_new":
            # New workbook row was appended; build a link from current Linear.
            if not row.linear_id:
                continue
            issue = issue_by_linear.get(row.linear_id)
            if issue is None:
                continue
            linear_snap = cp_issue_to_snapshot(
                issue,
                blockedby_ids=blockers_by_linear.get(row.linear_id, []),
            )
            new_links.append(SyncLink(
                program=program,
                wbs_id=row.wbs_id,
                linear_id=row.linear_id,
                last_synced=applied_at,
                linear_url=issue.linear_url or "",
                **snapshot_to_sync_fields(linear_snap),
            ))
            handled_linear.add(row.linear_id)
            continue

        # For update / archive / orphaned_link: existing link
        link = link_by_linear.get(row.linear_id)
        if link is None:
            continue
        handled_linear.add(row.linear_id)

        if row.action == "orphaned_link":
            # Linear gone; keep the link as a tombstone but refresh timestamp.
            new_links.append(SyncLink(
                **{k: getattr(link, k) for k in [
                    "program", "wbs_id", "linear_id", "linear_url",
                    "sidecar_predecessors", "sidecar_percent",
                    "sidecar_notes", "sidecar_team",
                ]},
                last_synced=applied_at,
                **snapshot_to_sync_fields(sync_fields_to_snapshot(link)),
            ))
            continue

        if row.action == "archive":
            # Workbook deleted the row; we're archiving in Linear. Keep
            # the link as a tombstone — useful if the workbook user
            # un-deletes later (no easy detection but the sync_tab row
            # serves as audit).
            new_links.append(SyncLink(
                **{k: getattr(link, k) for k in [
                    "program", "wbs_id", "linear_id", "linear_url",
                    "sidecar_predecessors", "sidecar_percent",
                    "sidecar_notes", "sidecar_team",
                ]},
                last_synced=applied_at,
                **snapshot_to_sync_fields(sync_fields_to_snapshot(link)),
            ))
            continue

        if row.action == "update":
            # Build the post-merge snapshot: for each MERGEABLE_FIELD,
            # use the row's resolved_to_value if it changed; else use
            # the current Linear value (which is also the snapshot).
            issue = issue_by_linear.get(row.linear_id)
            if issue is None:
                continue
            linear_snap = cp_issue_to_snapshot(
                issue,
                blockedby_ids=blockers_by_linear.get(row.linear_id, []),
            )
            post_fields: dict[str, str] = {
                name: getattr(linear_snap, name)
                for name in SNAPSHOT_FIELD_NAMES
            }
            for fc in row.field_changes:
                if fc.field in SNAPSHOT_FIELD_NAMES:
                    post_fields[fc.field] = str(fc.resolved_to_value or "")
            post_snap = IssueSnapshot(**post_fields)

            # Sidecar refresh from workbook task (always — workbook-only fields).
            task = task_by_wbs.get(row.wbs_id)
            sidecar_kwargs = _sidecar_from_task(task) if task else {}

            new_links.append(SyncLink(
                program=program,
                wbs_id=link.wbs_id,
                linear_id=link.linear_id,
                last_synced=applied_at,
                linear_url=link.linear_url,
                **sidecar_kwargs,
                **snapshot_to_sync_fields(post_snap),
            ))
            continue

        if row.action == "unchanged":
            # Refresh timestamp + sidecar to current workbook; snapshot unchanged.
            task = task_by_wbs.get(row.wbs_id)
            sidecar_kwargs = _sidecar_from_task(task) if task else {
                k: getattr(link, k) for k in [
                    "sidecar_predecessors", "sidecar_percent",
                    "sidecar_notes", "sidecar_team",
                ]
            }
            new_links.append(SyncLink(
                program=program,
                wbs_id=link.wbs_id,
                linear_id=link.linear_id,
                last_synced=applied_at,
                linear_url=link.linear_url,
                **sidecar_kwargs,
                **snapshot_to_sync_fields(sync_fields_to_snapshot(link)),
            ))

    # Preserve any links whose row was NOT in the diff (defensive — shouldn't happen).
    for link in existing_links:
        if link.linear_id in handled_linear:
            continue
        new_links.append(link)

    return new_links


def _sidecar_from_task(task: Task) -> dict[str, str]:
    """Build the sidecar_* SyncLink-kwarg dict from a workbook Task."""
    return {
        "sidecar_predecessors": task.predecessors or "",
        "sidecar_percent": str(task.percent_complete) if task.percent_complete else "",
        "sidecar_notes": task.notes or "",
        "sidecar_team": task.team or "",
    }


# ----- Public API ------------------------------------------------------------


def sync(
    ss,
    payload: CpInput,
    program: str,
    *,
    dry_run: bool = False,
    direction: str = "both",
    force: bool = False,
) -> SyncResult:
    """Top-level sync orchestrator.

    Direction modes:
      pull  — only apply Linear→workbook changes (no MCP requests emitted)
      push  — only emit workbook→Linear MCP requests (no workbook writes)
      both  — apply pull-side directly AND emit push-side MCP requests

    On `dry_run=True`, computes everything but skips workbook + sync_tab
    writes. The returned SyncResult is identical in shape — the caller
    can render the diff for user preview.

    On `force=True`, drops existing sync links before computing the
    diff. Every linked Linear issue becomes effectively new (action
    `pull_new`).
    """
    if direction not in ("pull", "push", "both"):
        raise ValueError(f"direction must be pull, push, or both; got {direction!r}")

    # 1. Auto-migrate sync_tab (idempotent; no-op if already Phase-2 or absent).
    migrate_sync_tab(ss)

    # 2. Open program tab; bail loudly if missing.
    tab_name = schema.program_tab_name(program)
    try:
        program_ws = ss.worksheet(tab_name)
    except Exception:
        raise ProgramTabMissingError(
            f"program tab {tab_name!r} does not exist. "
            f"Run `gantt program new {program}` first, then re-run the sync."
        )

    # 3. Read current state.
    workbook_tasks = sheets.read_program_tasks(program_ws)
    if force:
        delete_links(ss, program)
        existing_links: list[SyncLink] = []
    else:
        existing_links = read_links(ss, program)

    # 4. Compute SyncDiff.
    diff = compute_sync_diff(
        program=program,
        workbook_tasks=workbook_tasks,
        existing_links=existing_links,
        current_linear=payload,
    )

    # 5. Build push-side MCP requests (always — for dry-run preview too).
    mcp_requests: list[MCPRequest]
    if direction in ("push", "both"):
        workbook_tasks_by_wbs = {t.id: t for t in workbook_tasks}
        linear_issues_by_id = {iss.linear_id: iss for iss in payload.issues}
        mcp_requests = build_push_requests(
            diff,
            workbook_tasks_by_wbs=workbook_tasks_by_wbs,
            linear_team=payload.config.linear_team,
            linear_project=payload.config.linear_project,
            linear_archive_state=payload.config.linear_archive_state,
            linear_issues_by_id=linear_issues_by_id,
            linear_team_label_map=payload.config.linear_team_label_map,
            workbook_tasks=workbook_tasks,
            payload=payload,
            existing_links=existing_links,
        )
    else:
        mcp_requests = []

    applied_at = datetime.now(timezone.utc).isoformat()
    summary = _summary_counts(diff, mcp_requests)

    if not dry_run:
        # 6. Pull-side workbook writes.
        if direction in ("pull", "both"):
            _apply_pull_writes(
                program_ws=program_ws,
                diff=diff,
                payload=payload,
                workbook_tasks=workbook_tasks,
                existing_links=existing_links,
            )

        # 7. Optimistic sync_tab refresh (skips `create` rows).
        new_links = _compute_post_sync_links(
            diff=diff,
            payload=payload,
            workbook_tasks=workbook_tasks,
            existing_links=existing_links,
            program=program,
            applied_at=applied_at,
        )
        upsert_links(ss, program, new_links)

    return SyncResult(
        ok=True,
        program=program,
        direction=direction,
        dry_run=dry_run,
        applied_at=applied_at,
        summary=summary,
        diffs=[_row_diff_to_dict(r) for r in diff.rows],
        mcp_requests=[_mcp_request_to_dict(r) for r in mcp_requests],
        warnings=[],
    )
