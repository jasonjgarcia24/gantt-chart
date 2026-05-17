"""Pull orchestrator: ingest a normalized Linear payload, reconcile against
the existing program tab + `_LinearSync`, apply the conflict policy, and
write the program tab.

Phase 1: pull only. No writes back to Linear. See
`docs/specs/linear-integration.md` for the conflict-policy table and
overall design.

Architecture: the agent fetches Linear via MCP and pipes a `CpInput`
JSON document (extended with per-issue `linear_url`) to the CLI. This
module is the CLI-side orchestrator; the CLI thin wrapper in `gantt`
just parses argv + stdin and calls `pull(...)`.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Optional

from gantt_lib import schema, sheets
from gantt_lib.cascade import (
    CycleError,
    MissingPredecessorError,
    UnanchoredError,
    cascade,
)
from gantt_lib.cp.adapter import cp_input_to_program
from gantt_lib.cp.contracts import (
    CpInput,
    CpInputIssue,
    CpOutputWarning,
)
from gantt_lib.linear.sync_tab import (
    SyncLink,
    delete_links,
    ensure_sync_tab,
    read_links,
    upsert_links,
)
from gantt_lib.model import NUM_COLUMNS, Program, Task, wbs_sort_key


# ----- Diff dataclasses ------------------------------------------------------


@dataclass
class FieldChange:
    field: str
    from_value: Any
    to_value: Any
    source: str  # "linear" (Linear overrode workbook) or "workbook" (kept)


@dataclass
class RowDiff:
    linear_id: str
    wbs_id: str
    action: str  # "added" | "updated" | "unchanged" | "workbook_only_preserved" | "orphaned_link"
    title: str
    changes: list[FieldChange] = field(default_factory=list)


@dataclass
class PullDiff:
    program: str
    rows: list[RowDiff]
    warnings: list[CpOutputWarning]

    def by_action(self, action: str) -> list[RowDiff]:
        return [r for r in self.rows if r.action == action]


@dataclass
class PullResult:
    ok: bool
    program: str
    applied_at: str
    dry_run: bool
    summary: dict[str, int]
    diffs: list[dict]
    warnings: list[dict]


class ProgramTabMissingError(RuntimeError):
    """Raised when the program tab doesn't exist yet. Phase 1 requires the
    user (or agent) to create the tab via `gantt program new <name>` first."""


# ----- Public API ------------------------------------------------------------


def pull(
    ss,
    inp: CpInput,
    program: str,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> PullResult | dict:
    """Top-level: pull `inp` into program tab `program`.

    `force=True` drops all `_LinearSync` entries for this program first,
    so every Linear issue is treated as new (existing program rows still
    survive as workbook_only_preserved unless their linear_id reappears
    in the fresh pull).

    `dry_run=True` returns the would-apply PullResult without writing.

    Returns either a PullResult (success or dry-run) or a dict with shape
    `{ok: False, error: ..., detail: ...}` for hard failures.
    """
    tab_name = schema.program_tab_name(program)
    try:
        program_ws = ss.worksheet(tab_name)
    except Exception:
        raise ProgramTabMissingError(
            f"program tab {tab_name!r} does not exist. "
            f"Run `gantt program new {program}` first, then re-run the pull."
        )

    existing_tasks = sheets.read_program_tasks(program_ws)

    if force:
        delete_links(ss, program)
        existing_links: list[SyncLink] = []
    else:
        existing_links = read_links(ss, program)

    wbs_assignments = assign_wbs_ids(inp, existing_links)

    diff, fresh_program = compute_diff(
        inp=inp,
        existing_tasks=existing_tasks,
        existing_links=existing_links,
        wbs_assignments=wbs_assignments,
        program=program,
    )

    # Try cascading the fresh program. If cascade fails (cycle, etc.) the
    # pull is rejected — we don't write a half-good plan.
    try:
        cascade(fresh_program)
    except CycleError as e:
        return {"ok": False, "error": "cycle_detected", "detail": str(e)}
    except UnanchoredError as e:
        return {"ok": False, "error": "unanchored_task", "detail": str(e)}
    except MissingPredecessorError as e:
        return {"ok": False, "error": "missing_predecessor", "detail": str(e)}

    return apply_diff(
        ss=ss,
        program_ws=program_ws,
        program=program,
        diff=diff,
        fresh_program=fresh_program,
        existing_tasks=existing_tasks,
        wbs_assignments=wbs_assignments,
        inp=inp,
        dry_run=dry_run,
    )


# ----- WBS-id assignment -----------------------------------------------------


def assign_wbs_ids(
    inp: CpInput, existing_links: list[SyncLink]
) -> dict[str, str]:
    """Assign WBS ids to every issue in `inp`, preserving existing links.

    Existing issues keep their assigned WBS id from `existing_links`.
    New issues get the next free sibling number under their parent (or
    next free top-level integer if no parent). Order within siblings
    follows the order issues appear in `inp.issues` (caller's
    responsibility to upstream-sort, e.g. by Linear `sortOrder`).
    """
    existing_by_linear = {l.linear_id: l.wbs_id for l in existing_links}
    children: dict[Optional[str], list[CpInputIssue]] = {}
    for iss in inp.issues:
        children.setdefault(iss.parent_linear_id, []).append(iss)

    assignments: dict[str, str] = {}

    def used_under(parent_wbs: Optional[str]) -> set[int]:
        """Return the set of integer suffixes already used under parent_wbs."""
        prefix = "" if parent_wbs is None else f"{parent_wbs}."
        used: set[int] = set()
        for wbs in assignments.values():
            if not wbs.startswith(prefix):
                continue
            rest = wbs[len(prefix):]
            if "." in rest:
                continue  # grandchild
            try:
                used.add(int(rest))
            except ValueError:
                continue
        # Also seed from existing_links not yet assigned — they're going to
        # claim their numbers shortly.
        for linear_id, wbs in existing_by_linear.items():
            if linear_id in assignments:
                continue
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
        kids = children.get(parent_linear, [])
        for iss in kids:
            if iss.linear_id in existing_by_linear:
                wbs = existing_by_linear[iss.linear_id]
            else:
                used = used_under(parent_wbs)
                next_n = (max(used) + 1) if used else 1
                wbs = str(next_n) if parent_wbs is None else f"{parent_wbs}.{next_n}"
            assignments[iss.linear_id] = wbs
            assign(iss.linear_id, wbs)

    assign(None, None)
    return assignments


# ----- Diff computation ------------------------------------------------------


# Field-name → conflict-policy mapping. "linear" means Linear's value
# overrides; "workbook" means the existing workbook value is preserved.
_LINEAR_WINS_FIELDS = ("name", "status", "owner", "milestone")


def _record_linear_changes(
    existing: Task, fresh: Task
) -> list[FieldChange]:
    """Compare Linear-sourced fields between an existing workbook Task and
    the freshly-built Task from the Linear payload. Returns changes for
    reporting; the caller decides whether to apply them (per conflict
    policy)."""
    out: list[FieldChange] = []
    label_for = {
        "name": "title",
        "status": "state",
        "owner": "assignee",
        "milestone": "milestone",
    }
    for fname in _LINEAR_WINS_FIELDS:
        existing_v = getattr(existing, fname)
        fresh_v = getattr(fresh, fname)
        if existing_v != fresh_v:
            out.append(
                FieldChange(
                    field=label_for[fname],
                    from_value=existing_v,
                    to_value=fresh_v,
                    source="linear",
                )
            )
    return out


def _merge_task(existing: Task, fresh: Task) -> tuple[Task, list[FieldChange]]:
    """Apply the per-field conflict policy.

    Linear wins: name, status, owner, milestone (always overwritten).
    Workbook wins: predecessors, duration, percent_complete, notes, team
    (always preserved if workbook has any value; falls back to fresh when
    workbook's value is empty/zero — that's the "first-pull seeding"
    case where workbook is empty).
    """
    changes = _record_linear_changes(existing, fresh)
    merged = replace(fresh)  # start with fresh; selectively override below

    # Workbook-wins fields. Preserve workbook value when it's been edited
    # (non-default); accept fresh value only when workbook never had one.
    if existing.predecessors:
        merged.predecessors = existing.predecessors
    if existing.duration:
        merged.duration = existing.duration
    if existing.percent_complete:
        merged.percent_complete = existing.percent_complete
    if existing.notes:
        merged.notes = existing.notes
    if existing.team:
        merged.team = existing.team

    return merged, changes


def compute_diff(
    *,
    inp: CpInput,
    existing_tasks: list[Task],
    existing_links: list[SyncLink],
    wbs_assignments: dict[str, str],
    program: str,
) -> tuple[PullDiff, Program]:
    """Classify every issue + workbook row, build the post-pull Program in
    memory. The Program returned is ready for cascade(); caller invokes
    cascade then apply_diff."""

    # Build fresh Tasks from inp (Linear-sourced view).
    build = cp_input_to_program(inp, wbs_assignments)
    fresh_by_wbs = {t.id: t for t in build.program.tasks}
    issue_by_linear = build.issue_by_linear

    existing_by_wbs = {t.id: t for t in existing_tasks}
    link_wbs_by_linear = {l.linear_id: l.wbs_id for l in existing_links}
    linear_by_existing_wbs = {l.wbs_id: l.linear_id for l in existing_links}

    rows: list[RowDiff] = []
    merged_tasks_by_wbs: dict[str, Task] = {}

    # 1) Walk Linear issues — classify as added/updated/unchanged.
    for iss in inp.issues:
        wbs = wbs_assignments[iss.linear_id]
        fresh_task = fresh_by_wbs[wbs]

        existing = existing_by_wbs.get(wbs)
        if existing is None:
            merged_tasks_by_wbs[wbs] = fresh_task
            rows.append(
                RowDiff(
                    linear_id=iss.linear_id,
                    wbs_id=wbs,
                    action="added",
                    title=iss.title,
                    changes=[],
                )
            )
            continue

        merged, changes = _merge_task(existing, fresh_task)
        merged_tasks_by_wbs[wbs] = merged
        rows.append(
            RowDiff(
                linear_id=iss.linear_id,
                wbs_id=wbs,
                action="updated" if changes else "unchanged",
                title=iss.title,
                changes=changes,
            )
        )

    # 2) Walk existing workbook rows — find workbook-only-preserved + orphans.
    pulled_wbs = {wbs_assignments[iss.linear_id] for iss in inp.issues}
    for existing in existing_tasks:
        if existing.id in pulled_wbs:
            continue  # already classified above
        merged_tasks_by_wbs[existing.id] = existing
        linked_linear_id = linear_by_existing_wbs.get(existing.id)
        if linked_linear_id and linked_linear_id not in issue_by_linear:
            # Was linked to a Linear issue, but that issue is no longer in
            # the Linear payload (deleted, archived, or moved out of project).
            # Keep the workbook row, drop the link.
            rows.append(
                RowDiff(
                    linear_id=linked_linear_id,
                    wbs_id=existing.id,
                    action="orphaned_link",
                    title=existing.name,
                    changes=[],
                )
            )
        else:
            rows.append(
                RowDiff(
                    linear_id="",
                    wbs_id=existing.id,
                    action="workbook_only_preserved",
                    title=existing.name,
                    changes=[],
                )
            )

    # Assemble the post-pull Program in WBS-sorted order.
    sorted_wbs = sorted(merged_tasks_by_wbs.keys(), key=wbs_sort_key)
    final_tasks = [merged_tasks_by_wbs[w] for w in sorted_wbs]
    # Re-compute level from wbs depth so it stays consistent after re-parenting.
    for t in final_tasks:
        t.level = t.id.count(".") + 1

    fresh_program = Program(name=inp.project.name, tasks=final_tasks, holidays=set())

    # Promote engine warnings (from cp/adapter) into the pull warnings.
    warnings = list(build.warnings)

    return PullDiff(program=program, rows=rows, warnings=warnings), fresh_program


# ----- Apply -----------------------------------------------------------------


def _summary_counts(diff: PullDiff) -> dict[str, int]:
    return {
        "added": len(diff.by_action("added")),
        "updated": len(diff.by_action("updated")),
        "kept_unchanged": len(diff.by_action("unchanged")),
        "workbook_only_preserved": len(diff.by_action("workbook_only_preserved")),
        "orphaned_link": len(diff.by_action("orphaned_link")),
    }


def _row_diff_to_dict(rd: RowDiff) -> dict:
    return {
        "linear_id": rd.linear_id,
        "wbs_id": rd.wbs_id,
        "title": rd.title,
        "action": rd.action,
        "changes": [
            {
                "field": c.field,
                "from": _serialize_value(c.from_value),
                "to": _serialize_value(c.to_value),
                "source": c.source,
            }
            for c in rd.changes
        ],
    }


def _serialize_value(v: Any) -> Any:
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def _warning_to_dict(w: CpOutputWarning) -> dict:
    return {"linear_id": w.linear_id, "kind": w.kind, "message": w.message}


def apply_diff(
    *,
    ss,
    program_ws,
    program: str,
    diff: PullDiff,
    fresh_program: Program,
    existing_tasks: list[Task],
    wbs_assignments: dict[str, str],
    inp: CpInput,
    dry_run: bool,
) -> PullResult:
    """Write the post-pull program tab + sync tab. Returns the PullResult.

    On `dry_run=True`, returns the same shape with `dry_run: true` and no
    Sheet writes happen.
    """
    applied_at = datetime.now(timezone.utc).isoformat()

    result = PullResult(
        ok=True,
        program=program,
        applied_at=applied_at,
        dry_run=dry_run,
        summary=_summary_counts(diff),
        diffs=[_row_diff_to_dict(rd) for rd in diff.rows],
        warnings=[_warning_to_dict(w) for w in diff.warnings],
    )

    if dry_run:
        return result

    # 1) Rewrite the program tab data region with the cascaded tasks.
    _replace_program_data_region(program_ws, fresh_program.tasks)

    # 2) Update the _LinearSync tab. Build one SyncLink per Linear issue
    #    that survived into the final program (i.e. is in wbs_assignments
    #    AND has a corresponding fresh Task). Orphaned links are dropped
    #    by not including them.
    new_links: list[SyncLink] = []
    for iss in inp.issues:
        wbs = wbs_assignments.get(iss.linear_id)
        if wbs is None:
            continue
        new_links.append(
            SyncLink(
                program=program,
                wbs_id=wbs,
                linear_id=iss.linear_id,
                last_synced=applied_at,
                linear_url=iss.linear_url or "",
            )
        )
    upsert_links(ss, program, new_links)

    return result


def _replace_program_data_region(ws, tasks: list[Task]) -> None:
    """Clear the existing data region (rows FIRST_DATA_ROW..end of col A) and
    write `tasks` from FIRST_DATA_ROW down. Cols N+ (timeline) are
    untouched — the ARRAYFORMULA at N{FIRST_DATA_ROW} renders bars based on
    A-M values."""
    first_row = sheets.FIRST_DATA_ROW
    last_col = schema.col_letter(schema.NUM_DATA_COLS)
    existing_a = ws.col_values(1)
    last_existing_row = len(existing_a)

    new_rows = [sheets._indented_row(t) for t in tasks]

    # Clear any data rows that aren't being overwritten by new content.
    new_count = len(new_rows)
    last_new_row = first_row + new_count - 1
    if last_existing_row > last_new_row and last_existing_row >= first_row:
        clear_start = max(first_row, last_new_row + 1)
        if clear_start <= last_existing_row:
            blank_rows = [[""] * NUM_COLUMNS] * (last_existing_row - clear_start + 1)
            ws.update(
                range_name=f"A{clear_start}:{last_col}{last_existing_row}",
                values=blank_rows,
                value_input_option="USER_ENTERED",
            )

    if not new_rows:
        return

    ws.update(
        range_name=f"A{first_row}:{last_col}{last_new_row}",
        values=new_rows,
        value_input_option="USER_ENTERED",
    )
