"""CLI handler for `gantt deck`.

Lives in gantt_lib so tests can import the handler directly via
FakeSlidesService / FakeDriveService mocks (the gantt script's venv-trampoline
prevents straight `from gantt import cmd_deck` in tests). The gantt script
wires argparse + auth and delegates to `cmd_deck` with services already built.
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from datetime import date as _date
from typing import Optional


def _ok(line: str) -> None:
    print(f"gantt: {line} ✓")


def _die(msg: str, code: int = 1) -> None:
    print(f"gantt: error: {msg}", file=sys.stderr)
    sys.exit(code)


def _resolve_targets(ss, args: argparse.Namespace) -> tuple[list[str], str]:
    """Return (program_names_to_render, scope_label_for_section_divider).

    Audience defaults:
      tactical → requires --program=<X> or --all
      strategic → defaults to portfolio (all programs); --program=<X> for
                  a single-program leadership view; --all-programs is
                  equivalent to the default and exists for explicitness
    """
    from . import baseline_io

    audience = args.audience
    program = getattr(args, "program", None)
    do_all = getattr(args, "all", False)

    if audience == "tactical":
        if program:
            if not baseline_io.program_tab_exists(ss, program):
                _die(
                    f"program {program!r} not found. "
                    f"Run: gantt program new {program}"
                )
            return [program], program
        if do_all:
            return baseline_io.list_program_names(ss), "all"
        _die("tactical decks require --program=<name> or --all")
        return [], ""  # unreachable; satisfies type checker

    # strategic
    if program:
        if not baseline_io.program_tab_exists(ss, program):
            _die(
                f"program {program!r} not found. "
                f"Run: gantt program new {program}"
            )
        return [program], program
    return baseline_io.list_program_names(ss), "Portfolio"


def _build_tactical_section(program, today, actor, drive_svc):
    """Render the tactical section for one program: data + chart + template requests.

    Includes a Drive image upload for the gantt-zoom slide (T5). Returns
    `(requests, divider_slide_id)` for the caller to execute via batchUpdate.
    """
    from .critical_path import critical_path
    from .deck import data as deck_data
    from .deck import slides_io, templates
    from .deck.charts import render_gantt_zoom_png

    cp_ids = set(critical_path(program))
    this_week = deck_data.this_week_and_next(program, today)
    block_data = deck_data.blockers(program)
    cp_due = deck_data.critical_path_due_soon(program, today)
    recent = deck_data.recently_completed(program, today)
    gantt_tasks = deck_data.gantt_zoom_window(program, today)

    png = render_gantt_zoom_png(
        gantt_tasks, today,
        critical_ids=cp_ids,
        title=f"{program.name} — 30-Day Window",
    )
    image_name = f"gantt-deck-image-{uuid.uuid4()}.png"
    _file_id, image_url = slides_io.upload_image_to_drive(
        drive_svc, png, image_name,
    )

    return templates.tactical_section_requests(
        program.name, today, actor,
        this_week_tasks=this_week,
        blockers_data=block_data,
        cp_due_tasks=cp_due,
        recent_tasks=recent,
        gantt_image_url=image_url,
    )


def _build_strategic_section(programs, scope_label, today, actor, baseline_rows):
    """Render one strategic section across the given programs (no chart slide)."""
    from .deck import data as deck_data
    from .deck import templates

    portfolio = deck_data.portfolio_status(programs, baseline_rows)
    milestones = deck_data.milestone_slip_summary(programs, baseline_rows)
    cp = deck_data.critical_path_by_program(programs, baseline_rows)
    risks = deck_data.top_risks(programs, baseline_rows)
    forward = deck_data.forward_look_30d(programs, today)

    return templates.strategic_section_requests(
        scope_label, today, actor,
        portfolio_rows=portfolio,
        milestone_rows=milestones,
        cp_rows=cp,
        risk_rows=risks,
        forward_rows=forward,
    )


def cmd_deck(args: argparse.Namespace, ss, slides_svc, drive_svc) -> int:
    """Generate one or more deck sections appended to the yearly file.

    Tactical: one section per program (--program=X writes one; --all fans out).
    Strategic: one section across all targeted programs (default = portfolio).
    """
    from . import baseline_io, schema
    from . import sheets as gs_sheets
    from .cascade import (
        CycleError, MissingPredecessorError, UnanchoredError, cascade,
    )
    from .deck import slides_io
    from .model import Program

    audience = args.audience
    today = _date.today()
    actor = os.environ.get("USER", "unknown")

    target_program_names, scope_label = _resolve_targets(ss, args)
    if not target_program_names:
        _die("no programs in workbook — nothing to put in a deck")

    # Load shared data once
    config_ws = ss.worksheet("_Config")
    holidays = schema.read_holidays_from_config(config_ws)
    all_baseline_rows = baseline_io.read_baselines(ss)

    programs: list[Program] = []
    for name in target_program_names:
        try:
            ws = ss.worksheet(schema.program_tab_name(name))
        except Exception:
            print(f"gantt: deck — {name} not found, skipping", file=sys.stderr)
            continue
        tasks = gs_sheets.read_program_tasks(ws)
        p = Program(name=name, tasks=tasks, holidays=holidays)
        try:
            cascade(p)
        except (CycleError, UnanchoredError, MissingPredecessorError) as e:
            print(
                f"gantt: cascade failed for {name} — {e}, skipping",
                file=sys.stderr,
            )
            continue
        programs.append(p)

    if not programs:
        _die("no usable programs after cascade — nothing to put in a deck")

    file_id, url = slides_io.find_or_bootstrap_yearly_file(
        slides_svc, audience, today.year,
    )

    if audience == "tactical":
        for p in programs:
            requests, divider_id = _build_tactical_section(
                p, today, actor, drive_svc,
            )
            slides_io.execute_section_append(slides_svc, file_id, requests)
            _ok(f"deck appended — {url}#slide=id.{divider_id}")
    else:
        requests, divider_id = _build_strategic_section(
            programs, scope_label, today, actor, all_baseline_rows,
        )
        slides_io.execute_section_append(slides_svc, file_id, requests)
        _ok(f"deck appended — {url}#slide=id.{divider_id}")

    return 0
