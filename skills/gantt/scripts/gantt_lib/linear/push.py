"""Push orchestrator: SyncDiff → MCPRequest descriptors.

Pure logic. Converts the merge engine's SyncDiff into a list of
`MCPRequest` descriptors that the agent dispatches via the Linear MCP
in parallel batches. Two-pass structure handles the create-then-
reconcile-blockedBy dependency:

  Pass 1 — independent writes (can all dispatch in parallel):
    * update_push for existing issues (one save_issue per changed row)
    * create for workbook-only tasks (save_issue without blockedBy)
    * archive for workbook-deleted rows (save_issue with state=Cancelled)

  Pass 2 — blockedBy reconciliation (needs pass-1 IDs):
    * For any row whose blockedBy graph references newly-created issues
      (which only have placeholder IDs during pass 1), patch the
      blockedBy AFTER pass 1's responses are in. Placeholder format:
      `__NEW_<wbs_id>__`. The agent substitutes the real linear_id
      from pass-1's save_issue response into pass-2's kwargs before
      dispatching.

`build_push_requests` is the single entry point. Returns a list of
MCPRequest in dispatch order (pass 1 first, then pass 2). The agent
groups by `pass_number` and dispatches each pass as one Claude turn's
worth of parallel tool calls.

Pull-direction writes (apply pulled changes to the workbook tab) are
NOT in this module — they happen in `sync.py` via direct Sheets I/O
since they don't involve MCP. push.py only emits MCP descriptors.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from gantt_lib.linear.merge import (
    FieldChange,
    FieldClassification,
    SyncDiff,
    SyncRowDiff,
)


# Full MCP tool path so the agent's dispatcher can route directly.
SAVE_ISSUE_TOOL = "mcp__claude_ai_Linear__save_issue"


@dataclass(frozen=True)
class MCPRequest:
    """One Linear MCP call to dispatch from the agent.

    `pass_number` tells the agent which dispatch batch this belongs to —
    pass 1 requests are all independent and can fire in parallel; pass 2
    requests reference pass-1 results via `pass_1_placeholders`.

    `pass_1_placeholders` lists every `__NEW_<wbs>__` token that appears
    anywhere in `kwargs` (typically inside the `blockedBy` array). The
    agent substitutes each placeholder with the matching pass-1
    response's `id` before dispatching the request.
    """
    tool: str
    kwargs: dict[str, Any]
    description: str  # human-readable one-liner for rendering
    pass_number: int  # 1 = independent; 2 = needs pass-1 results
    # Bookkeeping for the post-dispatch sync_tab snapshot refresh.
    # `pass_1_create_for_wbs` is set on a create's pass-1 request so
    # the agent + CLI can match the response back to the workbook row.
    pass_1_create_for_wbs: str = ""
    # Tokens appearing in kwargs that need pass-1 result substitution
    # (only present on pass-2 requests).
    pass_1_placeholders: tuple[str, ...] = ()


def _placeholder_for(wbs_id: str) -> str:
    """Token an agent substitutes with the linear_id returned by pass 1."""
    return f"__NEW_{wbs_id}__"


# ----- Per-row request builders ----------------------------------------------


def _push_field_kwargs(field_changes: list[FieldChange]) -> dict[str, Any]:
    """Translate the field_changes the workbook is asserting (PUSH or
    workbook-winning CONFLICT) into save_issue kwargs.

    Maps internal snapshot field names → Linear save_issue param names.
    Skips PULL / CONVERGED / NO_OP (those don't need a Linear write)
    and any conflict where the winner is Linear.
    """
    # Internal snapshot name → Linear MCP save_issue param name.
    field_to_mcp = {
        "title": "title",
        "state": "state",
        "assignee": "assignee",
        "estimate": "estimate",
        "due_date": "dueDate",
        "parent": "parentId",
    }
    kwargs: dict[str, Any] = {}
    for fc in field_changes:
        # Only push fields the workbook is asserting:
        if fc.classification == FieldClassification.PUSH:
            should_write = True
        elif fc.classification == FieldClassification.CONFLICT and fc.resolved_to_source == "workbook":
            should_write = True
        else:
            should_write = False
        if not should_write:
            continue

        # blockedby is handled separately (pass 2) since it may reference
        # newly-created issues; skip here.
        if fc.field == "blockedby":
            continue

        mcp_key = field_to_mcp.get(fc.field)
        if mcp_key is None:
            continue  # field we don't know how to push (e.g., milestone)

        value = fc.resolved_to_value
        # Coerce empty/None to the right Linear shape.
        if mcp_key == "estimate":
            try:
                value = float(value) if value not in (None, "") else None
            except (TypeError, ValueError):
                value = None
            if not value:
                # Skip both None AND 0 — workbook default is 0 (milestone
                # or unestimated), and we don't want to clobber Linear's
                # estimate just because the workbook's at default. Users
                # who really want to remove an estimate should do so in
                # Linear directly.
                continue
        elif mcp_key == "dueDate":
            value = value or None  # null clears the dueDate in Linear
        elif mcp_key == "parentId":
            value = value or None
        else:
            value = value or ""
        kwargs[mcp_key] = value
    return kwargs


def _has_blockedby_change(row: SyncRowDiff) -> bool:
    """True if any field_change touches blockedby in a way that needs a Linear write."""
    for fc in row.field_changes:
        if fc.field != "blockedby":
            continue
        if fc.classification == FieldClassification.PUSH:
            return True
        if fc.classification == FieldClassification.CONFLICT and fc.resolved_to_source == "workbook":
            return True
        # CONVERGED, PULL, NO_OP, or Linear-wins conflict → no write needed.
    return False


def _blockedby_change_for_row(row: SyncRowDiff) -> Optional[FieldChange]:
    """Return the row's blockedby FieldChange if it needs a push, else None."""
    for fc in row.field_changes:
        if fc.field == "blockedby" and _has_blockedby_change(row):
            return fc
    return None


def _resolve_blockedby_to_linear_ids(
    blockedby_str: str,
    new_linear_id_by_wbs: dict[str, str],
) -> tuple[list[str], list[str]]:
    """Split a comma-separated blockedby_str into (concrete_linear_ids,
    placeholder_tokens). Existing Linear IDs pass through; references to
    newly-created workbook tasks (no linear_id yet) become placeholders.
    """
    concrete: list[str] = []
    placeholders: list[str] = []
    for raw in blockedby_str.split(","):
        token = raw.strip()
        if not token:
            continue
        # If a wbs_id is in the new-creates map, emit its placeholder.
        if token in new_linear_id_by_wbs:
            placeholders.append(_placeholder_for(token))
        else:
            concrete.append(token)
    return concrete, placeholders


def _build_update_request(row: SyncRowDiff) -> Optional[MCPRequest]:
    """Build the pass-1 save_issue request for an `update` row, or None
    if no field needs pushing (everything was PULL/CONVERGED/Linear-wins)."""
    kwargs = _push_field_kwargs(row.field_changes)
    if not kwargs:
        return None
    kwargs = {"id": row.linear_id, **kwargs}
    return MCPRequest(
        tool=SAVE_ISSUE_TOOL,
        kwargs=kwargs,
        description=f"update {row.linear_id} ({row.title})",
        pass_number=1,
    )


def _build_create_request(
    row: SyncRowDiff,
    task,  # gantt_lib.model.Task — passed by caller for create
    linear_team: str,
    linear_project: str,
) -> MCPRequest:
    """Build the pass-1 save_issue create request. blockedBy intentionally
    omitted — pass 2 reconciles it once new linear_ids are known."""
    kwargs: dict[str, Any] = {
        "team": linear_team,
        "project": linear_project,
        "title": task.name,
    }
    if task.owner:
        kwargs["assignee"] = task.owner
    if task.duration:
        kwargs["estimate"] = float(task.duration)
    if task.end:
        kwargs["dueDate"] = task.end.isoformat()
    return MCPRequest(
        tool=SAVE_ISSUE_TOOL,
        kwargs=kwargs,
        description=f"create new Linear issue for wbs={row.wbs_id} ({row.title})",
        pass_number=1,
        pass_1_create_for_wbs=row.wbs_id,
    )


def _build_archive_request(row: SyncRowDiff, archive_state: str) -> MCPRequest:
    """Build the pass-1 save_issue request that flips an issue to a
    canceled-type state."""
    return MCPRequest(
        tool=SAVE_ISSUE_TOOL,
        kwargs={"id": row.linear_id, "state": archive_state},
        description=f"archive {row.linear_id} ({row.title}) — workbook row deleted",
        pass_number=1,
    )


def _build_blockedby_request(
    row: SyncRowDiff,
    new_linear_id_by_wbs: dict[str, str],
) -> Optional[MCPRequest]:
    """Pass-2 request for blockedby reconciliation. Returns None when
    no blockedby change is needed for this row."""
    fc = _blockedby_change_for_row(row)
    if fc is None:
        return None

    # Compute the target set (workbook's desired blockedby) and the
    # current Linear set (linear_value from the merge). Diff → add/remove.
    target_str = fc.resolved_to_value or ""
    current_str = fc.linear_value or ""

    target_concrete, target_placeholders = _resolve_blockedby_to_linear_ids(
        target_str, new_linear_id_by_wbs
    )
    target_set = set(target_concrete)
    placeholder_tuples = tuple(target_placeholders)

    current_set = set(t.strip() for t in current_str.split(",") if t.strip())

    to_add = list(target_set - current_set)
    to_add_with_placeholders = to_add + target_placeholders
    to_remove = list(current_set - target_set)

    if not to_add_with_placeholders and not to_remove:
        return None

    kwargs: dict[str, Any] = {"id": row.linear_id}
    if to_add_with_placeholders:
        kwargs["blockedBy"] = to_add_with_placeholders
    if to_remove:
        kwargs["removeBlockedBy"] = to_remove

    return MCPRequest(
        tool=SAVE_ISSUE_TOOL,
        kwargs=kwargs,
        description=(
            f"reconcile blockedBy on {row.linear_id} "
            f"(+{len(to_add_with_placeholders)} −{len(to_remove)})"
        ),
        pass_number=2,
        pass_1_placeholders=placeholder_tuples,
    )


# ----- Top-level entry point -------------------------------------------------


def build_push_requests(
    diff: SyncDiff,
    *,
    workbook_tasks_by_wbs: dict[str, Any],  # gantt_lib.model.Task by wbs_id
    linear_team: str,
    linear_project: str,
    linear_archive_state: str,
) -> list[MCPRequest]:
    """Convert a SyncDiff into ordered MCP request descriptors for the
    agent to dispatch.

    Pass 1 collects all independent writes (updates, creates without
    blockedBy, archives). Pass 2 collects blockedBy reconciliation for
    any row whose blockedby graph references freshly-created issues
    (or whose Linear blockedby set diverged from the workbook's target).

    Caller should dispatch all pass-1 requests in parallel, collect
    `id`s from create responses, then substitute the `__NEW_<wbs>__`
    placeholders in pass-2 requests before dispatching pass 2.
    """
    if linear_archive_state and not isinstance(linear_archive_state, str):
        raise TypeError("linear_archive_state must be a string")

    pass1: list[MCPRequest] = []
    pass2: list[MCPRequest] = []

    # Walk rows, building per-action pass-1 requests.
    new_linear_id_by_wbs: dict[str, str] = {}
    for row in diff.rows:
        if row.action == "update":
            req = _build_update_request(row)
            if req is not None:
                pass1.append(req)
        elif row.action == "create":
            task = workbook_tasks_by_wbs.get(row.wbs_id)
            if task is None:
                continue  # defensive — caller should always supply
            req = _build_create_request(
                row, task,
                linear_team=linear_team,
                linear_project=linear_project,
            )
            pass1.append(req)
            new_linear_id_by_wbs[row.wbs_id] = ""  # placeholder, filled by agent later
        elif row.action == "archive":
            if not linear_archive_state:
                # Agent hasn't supplied a canceled state — skip silently;
                # the caller will surface this as a warning.
                continue
            pass1.append(_build_archive_request(row, linear_archive_state))
        # "pull_new", "orphaned_link", "unchanged" → no MCP write needed.

    # Pass 2: blockedBy reconciliation. Walk every row whose blockedby
    # changed (push-direction).
    for row in diff.rows:
        if row.action != "update":
            continue
        req = _build_blockedby_request(row, new_linear_id_by_wbs)
        if req is not None:
            pass2.append(req)

    # Also reconcile blockedBy for create rows whose workbook task has
    # predecessors — the create itself omits blockedBy so pass 2 fills it.
    for row in diff.rows:
        if row.action != "create":
            continue
        task = workbook_tasks_by_wbs.get(row.wbs_id)
        if task is None or not task.predecessors:
            continue
        # Synthesize a blockedby string from the task's predecessors,
        # using the diff's linkage knowledge. Since this row is a
        # create, its predecessors are workbook WBS ids — we need to
        # translate via existing links AND new-creates.
        from gantt_lib.dsl import parse_predecessors
        try:
            preds = parse_predecessors(task.predecessors)
        except Exception:
            preds = []
        if not preds:
            continue
        # Build placeholder + concrete blockedby list.
        concrete: list[str] = []
        placeholders: list[str] = []
        for p in preds:
            if p.id in new_linear_id_by_wbs:
                placeholders.append(_placeholder_for(p.id))
            # else: predecessor maps to a wbs that already has a
            # linear_id; resolve via diff context. We need linear_by_wbs
            # — pass it in.
            # For now, the merge already produced a blockedby field_change
            # for the row's existing-link case; the create case is handled
            # only via placeholders (workbook tasks with all-new
            # predecessors). If the create blocks an existing linked task,
            # the existing-link's blockedby update pass-2 catches it.
        if not (concrete or placeholders):
            continue
        kwargs: dict[str, Any] = {
            "id": _placeholder_for(row.wbs_id),  # the create's new linear_id
        }
        all_blockers = concrete + placeholders
        if all_blockers:
            kwargs["blockedBy"] = all_blockers
        pass2.append(MCPRequest(
            tool=SAVE_ISSUE_TOOL,
            kwargs=kwargs,
            description=(
                f"set blockedBy on newly-created issue for wbs={row.wbs_id}"
            ),
            pass_number=2,
            pass_1_placeholders=tuple([_placeholder_for(row.wbs_id), *placeholders]),
        ))

    return pass1 + pass2
