"""CLI handler for `gantt linear-pull --stdin --as <program>`.

Thin glue between argparse + stdin and the `linear.pull` orchestrator.
The handler reads the agent-supplied JSON payload from stdin, parses it
via the `cp.contracts` layer, calls `pull(...)`, and prints:

  - stdout: a JSON summary of the result (the agent renders this)
  - stderr: the verified `gantt: linear-pull <program> — ... ✓` line
    (the agent surfaces verbatim per the existing skill convention)

Exit codes:
  0 — pull (or dry-run) succeeded
  1 — input JSON failed contract validation
  2 — pull-time error (program tab missing, cycle, etc.)
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
