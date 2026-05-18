"""CLI handlers for `gantt linear-pull` and `gantt linear-sync`.

Thin glue between argparse + stdin and the orchestrators. Each
handler reads the agent-supplied JSON payload from stdin, parses via
`cp.contracts`, calls the appropriate orchestrator, and prints:

  - stdout: a JSON summary of the result (the agent renders this +
    executes any MCP requests on the push side)
  - stderr: the verified `gantt: linear-* <program> — ... ✓` line

Exit codes:
  0 — sync (or dry-run) succeeded
  1 — input JSON failed contract validation
  2 — orchestration error (program tab missing, etc.)
  3 — unexpected exception
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from typing import IO, Optional

from gantt_lib.cp.contracts import (
    ContractValidationError,
    from_json,
    to_json,
)
from gantt_lib.linear.pull import (
    ProgramTabMissingError,
    PullResult,
    pull,
)
from gantt_lib.linear import sync as sync_module
from gantt_lib.linear.sync import (
    ProgramTabMissingError as SyncProgramTabMissingError,
    SyncResult,
)


def _emit(result: PullResult | dict, stdout: IO, stderr: IO) -> None:
    """Write the JSON summary to stdout and the result-line to stderr."""
    if isinstance(result, PullResult):
        stdout.write(json.dumps(asdict(result), indent=2, default=str))
        stdout.write("\n")
        summary = result.summary
        if result.dry_run:
            line = (
                f"gantt: linear-pull {result.program} — DRY RUN: "
                f"{summary['added']} would add, "
                f"{summary['updated']} would update, "
                f"{summary['kept_unchanged']} unchanged "
                "✓"
            )
        else:
            line = (
                f"gantt: linear-pull {result.program} — "
                f"{summary['added']} added, "
                f"{summary['updated']} updated, "
                f"{summary['kept_unchanged']} unchanged "
                "✓"
            )
        stderr.write(line + "\n")
    else:
        # Error dict from pull() — e.g. {ok: False, error: "cycle_detected", ...}
        stdout.write(json.dumps(result, indent=2))
        stdout.write("\n")
        stderr.write(
            f"gantt: linear-pull failed — {result.get('error', 'unknown')} ✗\n"
        )


def cmd_linear_pull(
    args: argparse.Namespace,
    ss,
    *,
    stdin: Optional[IO] = None,
    stdout: Optional[IO] = None,
    stderr: Optional[IO] = None,
) -> int:
    """Handler invoked by the `gantt linear-pull` subcommand wrapper.

    `ss` is the gspread Spreadsheet (or a FakeSpreadsheet in tests).
    The stdin/stdout/stderr params are injectable so tests can drive
    the handler without monkey-patching sys."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr

    payload = stdin.read()
    try:
        inp = from_json(payload)
    except ContractValidationError as e:
        err = {"ok": False, "error": "contract_validation", "detail": str(e)}
        stdout.write(json.dumps(err, indent=2))
        stdout.write("\n")
        stderr.write(f"gantt: linear-pull failed — contract validation: {e} ✗\n")
        return 1
    except json.JSONDecodeError as e:
        err = {"ok": False, "error": "invalid_json", "detail": str(e)}
        stdout.write(json.dumps(err, indent=2))
        stdout.write("\n")
        stderr.write(f"gantt: linear-pull failed — invalid JSON on stdin ✗\n")
        return 1

    try:
        result = pull(
            ss,
            inp,
            args.program,
            dry_run=getattr(args, "dry_run", False),
            force=getattr(args, "force", False),
        )
    except ProgramTabMissingError as e:
        err = {"ok": False, "error": "program_tab_missing", "detail": str(e)}
        stdout.write(json.dumps(err, indent=2))
        stdout.write("\n")
        stderr.write(f"gantt: linear-pull failed — {e} ✗\n")
        return 2
    except Exception as e:  # noqa: BLE001
        err = {
            "ok": False,
            "error": "internal",
            "detail": f"{type(e).__name__}: {e}",
        }
        stdout.write(json.dumps(err, indent=2))
        stdout.write("\n")
        stderr.write(f"gantt: linear-pull failed — internal: {e} ✗\n")
        return 3

    _emit(result, stdout, stderr)

    # If pull() returned an error dict, propagate non-zero exit.
    if not isinstance(result, PullResult):
        return 2
    return 0


# ----- linear-sync (Phase 2) -------------------------------------------------


def _sync_result_line(result: SyncResult) -> str:
    """Build the verified gantt-prefix result line for a SyncResult."""
    s = result.summary
    if result.dry_run:
        return (
            f"gantt: linear-sync {result.program} — DRY RUN: "
            f"{s['pushed']} would push, "
            f"{s['pulled']} would pull, "
            f"{s['created']} would create, "
            f"{s['archived']} would archive, "
            f"{s['unchanged']} unchanged "
            "✓"
        )
    return (
        f"gantt: linear-sync {result.program} — "
        f"{s['pushed']} pushed, "
        f"{s['pulled']} pulled, "
        f"{s['created']} created, "
        f"{s['archived']} archived, "
        f"{s['conflicts_resolved']} conflicts resolved, "
        f"{s['unchanged']} unchanged "
        "✓"
    )


def _emit_sync(result: SyncResult, stdout: IO, stderr: IO) -> None:
    stdout.write(json.dumps(asdict(result), indent=2, default=str))
    stdout.write("\n")
    stderr.write(_sync_result_line(result) + "\n")


def cmd_linear_sync(
    args: argparse.Namespace,
    ss,
    *,
    stdin: Optional[IO] = None,
    stdout: Optional[IO] = None,
    stderr: Optional[IO] = None,
) -> int:
    """Handler for `gantt linear-sync` and aliases (`linear-push`).

    `args.direction` ∈ {pull, push, both}. The `linear-push` CLI alias
    sets direction=push; `linear-pull` keeps its own handler (cmd_linear_pull
    above) for Phase-1 backwards compat — could route through here
    eventually but the legacy path tests against the older PullResult
    shape, so we keep both for now.
    """
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr

    payload = stdin.read()
    try:
        inp = from_json(payload)
    except ContractValidationError as e:
        err = {"ok": False, "error": "contract_validation", "detail": str(e)}
        stdout.write(json.dumps(err, indent=2))
        stdout.write("\n")
        stderr.write(f"gantt: linear-sync failed — contract validation: {e} ✗\n")
        return 1
    except json.JSONDecodeError as e:
        err = {"ok": False, "error": "invalid_json", "detail": str(e)}
        stdout.write(json.dumps(err, indent=2))
        stdout.write("\n")
        stderr.write(f"gantt: linear-sync failed — invalid JSON on stdin ✗\n")
        return 1

    direction = getattr(args, "direction", "both") or "both"

    try:
        result = sync_module.sync(
            ss,
            inp,
            args.program,
            dry_run=getattr(args, "dry_run", False),
            direction=direction,
            force=getattr(args, "force", False),
        )
    except SyncProgramTabMissingError as e:
        err = {"ok": False, "error": "program_tab_missing", "detail": str(e)}
        stdout.write(json.dumps(err, indent=2))
        stdout.write("\n")
        stderr.write(f"gantt: linear-sync failed — {e} ✗\n")
        return 2
    except ValueError as e:
        # e.g. invalid direction
        err = {"ok": False, "error": "invalid_argument", "detail": str(e)}
        stdout.write(json.dumps(err, indent=2))
        stdout.write("\n")
        stderr.write(f"gantt: linear-sync failed — {e} ✗\n")
        return 1
    except Exception as e:  # noqa: BLE001
        err = {
            "ok": False,
            "error": "internal",
            "detail": f"{type(e).__name__}: {e}",
        }
        stdout.write(json.dumps(err, indent=2))
        stdout.write("\n")
        stderr.write(f"gantt: linear-sync failed — internal: {e} ✗\n")
        return 3

    _emit_sync(result, stdout, stderr)
    return 0
