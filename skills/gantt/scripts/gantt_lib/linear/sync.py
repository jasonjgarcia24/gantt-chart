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
) -> None:
    """Apply pull-direction changes to the program tab. For each
    pull-relevant row, update or append the workbook Task. sync_tab
    refresh is handled separately by `_compute_post_sync_links`."""
    task_by_wbs = {t.id: t for t in workbook_tasks}
    issue_by_linear = {iss.linear_id: iss for iss in payload.issues}

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
            _apply_field_to_task(task, fc.field, fc.resolved_to_value)
        # Write the updated row back to the sheet.
        row_idx = sheets.find_task_row(program_ws, row.wbs_id)
        if row_idx is not None:
            sheets.update_task_data(program_ws, row_idx, task)

    # 2) pull_new: append fresh Task with assigned WBS id.
    for row in diff.rows:
        if row.action != "pull_new":
            continue
        issue = issue_by_linear.get(row.linear_id)
        if issue is None:
            continue
        # Assign next-free top-level WBS id (sub-issues handled via parent
        # in cp/adapter on initial pull, but for mid-sync pull_new we
        # default to top-level).
        new_wbs = next_wbs_id(workbook_tasks)
        # Anchor: mirror cp/adapter behavior — if no Linear startedAt
        # and no blockers, default to today so cascade doesn't refuse
        # the next recalc with UnanchoredError. Issues with blockers
        # in Linear will get their start from cascade once the
        # predecessor reference is wired up.
        start = issue.start_anchor or payload.config.today
        new_task = Task(
            id=new_wbs,
            level=1,
            name=issue.title,
            owner=issue.assignee,
            duration=int(issue.estimate_days) if issue.estimate_days else 0,
            status=issue.state,
            milestone=issue.is_milestone,
            start=start,
            end=issue.end_anchor,
        )
        new_task.linear_url = issue.linear_url or None
        sheets.append_task(program_ws, new_task)
        # Update our in-memory workbook_tasks so subsequent next_wbs_id calls
        # see the new row (matters when multiple pull_news in one sync).
        workbook_tasks.append(new_task)
        # Stash the assigned wbs back onto the SyncRowDiff so the caller
        # can write the corresponding sync_tab row.
        row.wbs_id = new_wbs


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
    # blockedby, parent: skip — these require cross-row translation and
    # are workbook-wins fields anyway, so PULL/Linear-wins shouldn't
    # produce them often. Future enhancement if needed.


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
            linear_issues_by_id=linear_issues_by_id,
            linear_team=payload.config.linear_team,
            linear_project=payload.config.linear_project,
            linear_archive_state=payload.config.linear_archive_state,
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
