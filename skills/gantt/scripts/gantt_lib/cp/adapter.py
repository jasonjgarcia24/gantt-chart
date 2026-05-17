"""Adapter between the cp JSON contract and the existing cascade engine.

`cp_input_to_program` builds a `Program` (with `Task`s carrying caller-
assigned WBS ids) from a normalized `CpInput`. Predecessor edges are
translated into the existing predecessor-DSL string Task.predecessors
expects ("1FS+3, 2SS"). Issues without predecessors and without a
manual start_anchor are anchored to `config.today` so the cascade
engine has something to walk from.

`program_results_to_cp_output` produces the wire output after the
caller has invoked `cascade()` and `compute_slack()` on the Program.
Each CpOutputItem carries both the Linear id (for the agent's
rendering) and the WBS id (for cross-reference into the Sheet).

`run_cp` is the top-level orchestrator that catches engine exceptions
and translates them to `CpError` shapes. The agent or the pull
orchestrator typically calls this entry point.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from gantt_lib.cascade import (
    CycleError,
    MissingPredecessorError,
    UnanchoredError,
    cascade,
)
from gantt_lib.critical_path import compute_slack
from gantt_lib.cp.contracts import (
    CpError,
    CpInput,
    CpInputIssue,
    CpOutput,
    CpOutputItem,
    CpOutputWarning,
)
from gantt_lib.dsl import Predecessor, format_predecessors
from gantt_lib.model import Program, Task


@dataclass
class _BuildResult:
    program: Program
    warnings: list[CpOutputWarning]
    wbs_to_linear: dict[str, str]
    issue_by_linear: dict[str, CpInputIssue]


def default_wbs_assignments(inp: CpInput) -> dict[str, str]:
    """Assign sequential WBS ids by walking the parent hierarchy.

    Top-level issues (parent_linear_id is None) get `1`, `2`, ...
    Sub-issues get `<parent_wbs>.1`, `<parent_wbs>.2`, ... recursively.
    Order follows the order issues appear in `inp.issues` — the caller
    is responsible for any upstream sort (e.g. Linear's `sortOrder`).
    Used as a convenience for tests + ad-hoc invocations; T4's pull
    orchestrator computes its own assignments to preserve cross-pull
    stability.
    """
    children: dict[str | None, list[CpInputIssue]] = {}
    for iss in inp.issues:
        children.setdefault(iss.parent_linear_id, []).append(iss)

    assignments: dict[str, str] = {}

    def assign(parent_linear: str | None, parent_wbs: str | None) -> None:
        kids = children.get(parent_linear, [])
        for i, iss in enumerate(kids, start=1):
            wbs = str(i) if parent_wbs is None else f"{parent_wbs}.{i}"
            assignments[iss.linear_id] = wbs
            assign(iss.linear_id, wbs)

    assign(None, None)
    return assignments


def cp_input_to_program(
    inp: CpInput,
    wbs_assignments: dict[str, str],
) -> _BuildResult:
    """Translate a CpInput into a Program ready for cascade().

    Raises ContractValidationError if an issue's linear_id is missing
    from `wbs_assignments`, or if an edge references a linear_id that
    isn't in the issues list.
    """
    issue_by_linear = {iss.linear_id: iss for iss in inp.issues}
    wbs_to_linear: dict[str, str] = {}
    warnings: list[CpOutputWarning] = []

    # Validate assignments cover every issue.
    for iss in inp.issues:
        if iss.linear_id not in wbs_assignments:
            raise KeyError(
                f"wbs_assignments missing linear_id {iss.linear_id!r}"
            )
        wbs_to_linear[wbs_assignments[iss.linear_id]] = iss.linear_id

    # Group edges by `to` so we can build one predecessor DSL string per task.
    preds_by_to_linear: dict[str, list[Predecessor]] = {}
    for edge in inp.edges:
        if edge.to_linear_id not in issue_by_linear:
            raise KeyError(
                f"edge references unknown to_linear_id {edge.to_linear_id!r}"
            )
        if edge.from_linear_id not in issue_by_linear:
            raise KeyError(
                f"edge references unknown from_linear_id {edge.from_linear_id!r}"
            )
        from_wbs = wbs_assignments[edge.from_linear_id]
        preds_by_to_linear.setdefault(edge.to_linear_id, []).append(
            Predecessor(id=from_wbs, rel=edge.type, lag=edge.lag_days)
        )

    tasks: list[Task] = []
    for iss in inp.issues:
        wbs = wbs_assignments[iss.linear_id]
        level = wbs.count(".") + 1

        # Duration: prefer the issue's estimate; fall back to default with warning.
        if iss.estimate_days is None or iss.estimate_days == 0:
            duration = inp.config.default_duration_days
            if not iss.is_milestone:
                warnings.append(
                    CpOutputWarning(
                        linear_id=iss.linear_id,
                        kind="missing_estimate",
                        message=(
                            f"no estimate; using default "
                            f"{inp.config.default_duration_days}d"
                        ),
                    )
                )
        else:
            duration = int(iss.estimate_days)

        # Milestones always have duration 0 regardless of estimate.
        if iss.is_milestone:
            duration = 0

        # Anchor: if no predecessors AND no start_anchor, default to today
        # so the cascade engine has something to walk from.
        preds = preds_by_to_linear.get(iss.linear_id, [])
        start = iss.start_anchor
        if not preds and start is None:
            start = inp.config.today
            warnings.append(
                CpOutputWarning(
                    linear_id=iss.linear_id,
                    kind="defaulted_start",
                    message=(
                        f"no predecessors and no start_anchor; "
                        f"defaulted start to today ({inp.config.today.isoformat()})"
                    ),
                )
            )

        task = Task(
            id=wbs,
            level=level,
            name=iss.title,
            owner=iss.assignee,
            team="",
            start=start,
            end=iss.end_anchor,
            duration=duration,
            percent_complete=iss.percent,
            status=iss.state,
            predecessors=format_predecessors(preds),
            milestone=iss.is_milestone,
            notes="",
        )
        task.linear_url = iss.linear_url or None
        tasks.append(task)

    program = Program(name=inp.project.name, tasks=tasks, holidays=set())
    return _BuildResult(
        program=program,
        warnings=warnings,
        wbs_to_linear=wbs_to_linear,
        issue_by_linear=issue_by_linear,
    )


def program_results_to_cp_output(
    inp: CpInput,
    build: _BuildResult,
    slack_map: dict[str, int],
) -> CpOutput:
    """Build the CpOutput after cascade + slack have been computed."""
    cascade_items: list[CpOutputItem] = []
    for t in build.program.tasks:
        linear_id = build.wbs_to_linear.get(t.id, "")
        slack = slack_map.get(t.id, 0)
        cascade_items.append(
            CpOutputItem(
                linear_id=linear_id,
                wbs_id=t.id,
                title=t.name,
                start=t.start,
                end=t.end,
                slack_days=slack,
                on_critical_path=(slack == 0),
            )
        )

    # Critical-path slice: filter-then-order by start (stable for same-day starts).
    critical_path = [item for item in cascade_items if item.on_critical_path]
    critical_path.sort(key=lambda i: (i.start or inp.config.today, i.wbs_id))

    return CpOutput(
        project={"name": inp.project.name},
        computed_at=datetime.now(timezone.utc).isoformat(),
        critical_path=critical_path,
        cascade=cascade_items,
        warnings=build.warnings,
    )


def run_cp(
    inp: CpInput,
    wbs_assignments: dict[str, str] | None = None,
) -> CpOutput | CpError:
    """Top-level: build Program → cascade → slack → CpOutput.

    On cascade-engine exceptions, returns a CpError with the appropriate
    `error` slug and `trace`.
    """
    if not inp.issues:
        return CpOutput(
            project={"name": inp.project.name},
            computed_at=datetime.now(timezone.utc).isoformat(),
            critical_path=[],
            cascade=[],
            warnings=[],
        )

    if wbs_assignments is None:
        wbs_assignments = default_wbs_assignments(inp)

    try:
        build = cp_input_to_program(inp, wbs_assignments)
    except KeyError as e:
        return CpError(error="missing_reference", detail=str(e))

    try:
        cascade(build.program)
    except CycleError as e:
        return CpError(error="cycle_detected", detail=str(e))
    except UnanchoredError as e:
        return CpError(error="unanchored_task", detail=str(e))
    except MissingPredecessorError as e:
        return CpError(error="missing_predecessor", detail=str(e))

    slack_map = compute_slack(build.program)
    return program_results_to_cp_output(inp, build, slack_map)
