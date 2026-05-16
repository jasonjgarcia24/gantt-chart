"""CLI handlers for `gantt baseline {snapshot,show,clear}`.

Lives in gantt_lib (not the gantt script) so tests can import the handlers
directly without tripping the venv-trampoline at the script's module-import
time. The gantt script wires argparse and calls these functions with an
already-open workbook.

Result-line conventions:
- Success → "gantt: <verb> — <details> ✓" on stdout via _ok()
- Per-program failure (continue on --all) → "gantt: <verb> — <details> ✗" on
  stderr via _fail(); caller increments exit code but doesn't abort the loop
- Setup error (missing program tab, etc.) → die() — sys.exit(1) with a
  "gantt: error: ..." line on stderr
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date as _date
from typing import Optional


def _ok(line: str) -> None:
    print(f"gantt: {line} ✓")


def _fail(line: str) -> None:
    """Spec-shaped non-fatal failure line (caller decides whether to continue)."""
    print(f"gantt: {line} ✗", file=sys.stderr)


def _die(msg: str, code: int = 1) -> None:
    print(f"gantt: error: {msg}", file=sys.stderr)
    sys.exit(code)


def resolve_target_programs(
    ss, args: argparse.Namespace, *, all_attr: str = "all"
) -> list[str]:
    """Expand --program=X to [X] (after existence check) or --all to every program.

    Argparse's mutually_exclusive_group enforces the xor; this just dispatches
    and validates. `all_attr` accommodates `--all` (snapshot/clear) vs
    `--all-programs` (show).
    """
    from . import baseline_io

    program = getattr(args, "program", None)
    do_all = getattr(args, all_attr, False)
    if program:
        if not baseline_io.program_tab_exists(ss, program):
            _die(
                f"program {program!r} not found. "
                f"Run: gantt program new {program}"
            )
        return [program]
    return baseline_io.list_program_names(ss)


# ---------- snapshot ----------

def cmd_baseline_snapshot(args: argparse.Namespace, ss) -> int:
    """Freeze current dates as the baseline for one or all programs.

    Refuses per-program if an active baseline already exists, unless
    `--rebaseline` is set. History is appended to `_Baselines` regardless;
    the refusal is about moving the slip-comparison anchor, not preserving
    snapshots.
    """
    from . import baseline_io, schema, sheets as gs_sheets
    from .baseline import BaselineRow, active_baselines
    from .cascade import (
        CycleError, MissingPredecessorError, UnanchoredError, cascade,
    )
    from .model import Program

    programs = resolve_target_programs(ss, args)
    all_rows = baseline_io.read_baselines(ss)
    today = _date.today()
    actor = args.actor or os.environ.get("USER", "unknown")
    label = args.label or ""

    config_ws = ss.worksheet("_Config")
    holidays = schema.read_holidays_from_config(config_ws)

    exit_code = 0
    for program in programs:
        try:
            ws = ss.worksheet(schema.program_tab_name(program))
        except Exception:
            _fail(f"baseline snapshot — {program} not found")
            exit_code = 1
            continue

        tasks = gs_sheets.read_program_tasks(ws)
        if not tasks:
            print(f"gantt: baseline snapshot — {program}, 0 tasks (skipped) ⚠")
            continue

        existing_active = active_baselines(all_rows, program)
        is_rebaseline = bool(existing_active)
        if is_rebaseline and not args.rebaseline:
            program_rows = [r for r in all_rows if r.program == program]
            last_snap = max(r.snapshot_date for r in program_rows)
            print(
                f"gantt: baseline already exists for {program} "
                f"(last snapshot {last_snap.isoformat()}).\n"
                f"       Use --rebaseline to replace the slip-comparison anchor.\n"
                f"       History is always preserved. ✗",
                file=sys.stderr,
            )
            exit_code = 1
            continue

        # Cascade in-memory so the baseline captures the plan-of-record dates
        # (predecessors + durations + holidays), not whatever stale values
        # happen to be in the sheet's Start/End columns. Without this, a sheet
        # that hasn't been `gantt recalc`'d since the last edit produces a
        # baseline that locks in stale dates and causes phantom slip in
        # downstream views (decks, baseline show).
        sheet_dates = {t.id: (t.start, t.end) for t in tasks}
        program_obj = Program(name=program, tasks=tasks, holidays=holidays)
        try:
            cascade(program_obj)
        except (CycleError, UnanchoredError, MissingPredecessorError) as e:
            _fail(f"baseline snapshot — {program} cascade failed — {e}")
            exit_code = 1
            continue
        stale_ids = [
            t.id for t in tasks if (t.start, t.end) != sheet_dates[t.id]
        ]

        new_rows = [
            BaselineRow(
                program=program,
                wbs=t.id,
                task_name=t.name,
                snapshot_date=today,
                baseline_start=t.start,
                baseline_end=t.end,
                baseline_duration=t.duration,
                baseline_predecessors=t.predecessors,
                snapshot_label=label,
                snapshot_actor=actor,
            )
            for t in tasks
        ]
        baseline_io.append_baselines(ss, new_rows)
        all_rows.extend(new_rows)

        verb = "rebaselined" if is_rebaseline else "snapshot"
        if stale_ids:
            print(
                f"gantt: baseline snapshot — {program}: {len(stale_ids)} sheet "
                f"date(s) were stale; baseline captured cascade output. "
                f"Run `gantt recalc {program}` to sync the sheet."
            )
        _ok(f"baseline {verb} — {program}, {len(tasks)} tasks, {today.isoformat()}")

    return exit_code


# ---------- show ----------

def _slip_header_chars() -> tuple[str, str]:
    enc = (sys.stdout.encoding or "").lower()
    if "utf" in enc:
        return ("Δstart", "Δend")
    return ("dStart", "dEnd")


def _fmt_slip_cell(s: Optional[int]) -> str:
    if s is None:
        return "—"
    if s > 0:
        return f"+{s}"
    return str(s)


def _truncate(name: str, n: int = 30) -> str:
    return name if len(name) <= n + 1 else name[:n] + "…"


def _print_summary_block(summary) -> None:
    if summary.last_baseline_date:
        actor = summary.last_baseline_actor or "unknown"
        print(
            f"{summary.program} — last baseline "
            f"{summary.last_baseline_date.isoformat()} "
            f"({summary.days_since_baseline}d ago, by {actor})"
        )
    else:
        print(f"{summary.program} — no baseline yet")

    task_parts = [
        f"{summary.total_tasks} total",
        f"{summary.tasks_done} done",
        f"{summary.tasks_in_progress} in progress",
        f"{summary.tasks_not_started} not started",
    ]
    if summary.tasks_blocked:
        task_parts.append(f"{summary.tasks_blocked} blocked")
    if summary.tasks_at_risk:
        task_parts.append(f"{summary.tasks_at_risk} at risk")
    print("Tasks:    " + " | ".join(task_parts))

    if summary.max_end_slip is not None:
        print(
            f"Slip:     {summary.max_end_slip:+d}d max end-slip | "
            f"{summary.mean_end_slip:+.1f}d mean | "
            f"{summary.slipping_count} slipping | "
            f"{summary.ahead_count} ahead | "
            f"{summary.on_baseline_count} on-baseline"
        )

    if summary.tasks_not_baselined > 0:
        print(f"          {summary.tasks_not_baselined} tasks not yet baselined")

    if summary.current_cp_days is not None:
        if summary.baseline_cp_days is not None and summary.cp_delta is not None:
            sign = "+" if summary.cp_delta >= 0 else ""
            print(
                f"Critical: {summary.baseline_cp_days}d → "
                f"{summary.current_cp_days}d ({sign}{summary.cp_delta}d)"
            )
        else:
            print(f"Critical: {summary.current_cp_days}d (no baseline)")


def _build_detail_rows(
    program_obj, actives, args, cp_ids: set[str]
) -> list[list[str]]:
    """Compute the detail table rows (no header) per the active flags.

    Decision tree:
      1. Apply --milestones-only / --critical-path-only filters first.
      2. Compute (Δstart, Δend) per task.
      3. If --all → sort by WBS, return everything filtered.
      4. Elif --slipping-only → filter to Δend > 0, sort by abs Δend desc.
      5. Else (default or --top=N) → sort by abs Δend desc, take top N. If no
         task has Δend > 0, return empty (caller prints "all tracking" line).
    """
    from .baseline import slip_days
    from .model import wbs_sort_key

    candidates = []
    for t in program_obj.tasks:
        b = actives.get(t.id)
        if b is None:
            continue
        if args.milestones_only and not t.milestone:
            continue
        if args.critical_path_only and t.id not in cp_ids:
            continue
        candidates.append((t, b))

    enriched = [
        (t, b, slip_days(t.start, b.baseline_start), slip_days(t.end, b.baseline_end))
        for t, b in candidates
    ]

    if args.all:
        enriched.sort(key=lambda x: wbs_sort_key(x[0].id))
    elif args.slipping_only:
        enriched = [x for x in enriched if x[3] is not None and x[3] > 0]
        enriched.sort(key=lambda x: abs(x[3]), reverse=True)
    else:
        if not any(x[3] is not None and x[3] > 0 for x in enriched):
            return []
        enriched.sort(
            key=lambda x: abs(x[3]) if x[3] is not None else -1, reverse=True,
        )
        enriched = enriched[: args.top]

    rows: list[list[str]] = []
    for t, b, dstart, dend in enriched:
        rows.append([
            t.id,
            _truncate(t.name),
            b.baseline_start.isoformat() if b.baseline_start else "—",
            t.start.isoformat() if t.start else "—",
            _fmt_slip_cell(dstart),
            b.baseline_end.isoformat() if b.baseline_end else "—",
            t.end.isoformat() if t.end else "—",
            _fmt_slip_cell(dend),
        ])
    return rows


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    if not rows:
        return
    cols = list(zip(headers, *rows))
    widths = [max(len(str(cell)) for cell in col) for col in cols]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    for row in rows:
        print(fmt.format(*[str(c) for c in row]))


def cmd_baseline_show(args: argparse.Namespace, ss) -> int:
    from . import baseline_io, schema, sheets as gs_sheets
    from .baseline import active_baselines, compute_summary
    from .cascade import (
        CycleError, MissingPredecessorError, UnanchoredError, cascade,
    )
    from .critical_path import critical_path
    from .model import Program

    programs = resolve_target_programs(ss, args, all_attr="all_programs")
    if not programs:
        _ok("baseline show — no programs in workbook")
        return 0

    all_rows = baseline_io.read_baselines(ss)
    today = _date.today()
    config_ws = ss.worksheet("_Config")
    holidays = schema.read_holidays_from_config(config_ws)
    is_portfolio = getattr(args, "all_programs", False)

    overall_max_slip: Optional[int] = None
    worst_program: Optional[str] = None

    for i, program in enumerate(programs):
        try:
            ws = ss.worksheet(schema.program_tab_name(program))
        except Exception:
            _fail(f"baseline show — {program} not found")
            continue

        tasks = gs_sheets.read_program_tasks(ws)
        program_obj = Program(name=program, tasks=tasks, holidays=holidays)
        try:
            cascade(program_obj)
        except (CycleError, UnanchoredError, MissingPredecessorError) as e:
            print(f"gantt: cascade failed for {program} — {e}", file=sys.stderr)
            continue

        summary = compute_summary(program, program_obj, all_rows, today)

        if summary.max_end_slip is not None:
            if overall_max_slip is None or summary.max_end_slip > overall_max_slip:
                overall_max_slip = summary.max_end_slip
                worst_program = program

        if i > 0:
            print()
        _print_summary_block(summary)

        if not is_portfolio:
            actives = active_baselines(all_rows, program)
            cp_ids: set[str] = set()
            if args.critical_path_only:
                cp_ids = set(critical_path(program_obj))
            detail_rows = _build_detail_rows(program_obj, actives, args, cp_ids)
            print()
            if detail_rows:
                start_h, end_h = _slip_header_chars()
                _print_table(
                    ["wbs", "name", "baseline_start", "current_start", start_h,
                     "baseline_end", "current_end", end_h],
                    detail_rows,
                )
            elif summary.tasks_baselined > 0 and summary.slipping_count == 0:
                print("All tasks tracking to baseline ✓")
            slip_str = (
                f"max slip {summary.max_end_slip:+d}d"
                if summary.max_end_slip is not None
                else "no slip data"
            )
            _ok(
                f"baseline show — {program}, {summary.total_tasks} tasks, "
                f"{slip_str}, {summary.ahead_count} ahead"
            )

    if is_portfolio:
        n = len(programs)
        plural = "s" if n != 1 else ""
        if overall_max_slip is not None:
            _ok(
                f"baseline show — {n} program{plural}, "
                f"max slip +{overall_max_slip}d in {worst_program}"
            )
        else:
            _ok(f"baseline show — {n} program{plural}, no slip data")

    return 0


# ---------- clear ----------

def cmd_baseline_clear(args: argparse.Namespace, ss) -> int:
    from . import baseline_io

    programs = resolve_target_programs(ss, args)
    if not programs:
        _ok("baseline cleared — no programs in workbook")
        return 0

    all_rows = baseline_io.read_baselines(ss)
    rows_to_remove = [r for r in all_rows if r.program in set(programs)]

    if not args.force:
        n = len(rows_to_remove)
        scope = (
            ", ".join(programs)
            if len(programs) <= 3
            else f"{len(programs)} programs"
        )
        plural = "s" if n != 1 else ""
        print(
            f"gantt: baseline clear refused — pass --force "
            f"(this deletes {n} historical snapshot{plural} from {scope}) ✗",
            file=sys.stderr,
        )
        return 1

    baseline_io.delete_baselines_for_programs(ss, programs)
    for program in programs:
        n = sum(1 for r in rows_to_remove if r.program == program)
        plural = "s" if n != 1 else ""
        _ok(f"baseline cleared — {program}, {n} historical snapshot{plural} removed")
    return 0
