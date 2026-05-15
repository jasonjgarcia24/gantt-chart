# Spec: Baseline Tracking (Phase 1 of Deck Generation)

**Status:** Draft, awaiting Jason approval
**Date:** 2026-05-14
**Prerequisite for:** Phase 2 (audience-targeted slide deck generation)

## Objective

Capture program plans-of-record at points in time so we can answer: *how much
have tasks slipped vs. the plan we committed to?* Without this, the strategic
deck (EM/TPM/Leadership audience) cannot honestly show milestone slip — it can
only show "current end date," which is a moving target with no anchor.

A baseline is a frozen snapshot of `(start, end, duration)` for every task in a
program, taken at the moment leadership says "we're committed to this plan."
Every snapshot is logged to an append-only history. The most recent snapshot
per task is the **active baseline** that all slip math compares against.

### Success criteria

This phase is done when **all** of the following hold:

1. A user can run `gantt baseline snapshot --program=TPM90` and see a
   `gantt: baseline snapshot — TPM90, N tasks, YYYY-MM-DD ✓` result line, and
   a `_Baselines` tab in the workbook contains N new rows.
2. Re-running the same snapshot command on a program with existing baselines
   **refuses** with a clear error pointing at the `--rebaseline` flag.
   Re-running with `--rebaseline` succeeds, appends N more rows to
   `_Baselines`, and the active baseline (latest by snapshot_date) for each
   task moves to the new row.
3. `gantt baseline show --program=TPM90` prints a **summary block** (counts,
   max/mean slip, critical-path delta) followed by a **top-10 worst-slippers
   table** sorted by abs end-slip desc, plus a result line
   `gantt: baseline show — TPM90, N tasks, max slip +Xd, M ahead ✓`. The
   full per-task table is opt-in via `--all`.
4. `gantt baseline clear --program=TPM90 --force` removes every `_Baselines`
   row matching that program; without `--force` it refuses.
5. All new behavior has unit-test coverage in `tests/test_baseline.py`. The
   pure-logic module `gantt_lib/baseline.py` has 100% line coverage; the
   Sheets I/O paths in `cmd_baseline_*` are tested via `tests/test_baseline_io.py`
   using the same Sheets-mock pattern as `tests/test_sheets_helpers.py`.
6. `gantt info` is updated to report baseline coverage per program ("TPM90:
   12/12 tasks baselined, last snapshot 2026-04-30").

## Tech Stack

No new dependencies. Same stack as the existing CLI:
- Python 3.10+
- `gspread` for Sheets I/O
- `google-auth-oauthlib` for OAuth
- `pytest` for tests
- Argparse for CLI

## Schema

### New tab: `_Baselines`

Workbook-level tab (underscore prefix matches the existing `_Config` convention,
keeps it out of program-specific views). Append-only.

| Col | Header               | Type   | Required | Notes                                                                              |
|-----|----------------------|--------|----------|------------------------------------------------------------------------------------|
| A   | program              | str    | yes      | Program name (matches `P_<name>` tab without the prefix)                           |
| B   | wbs                  | str    | yes      | Task WBS id, e.g. "1", "2.3"                                                       |
| C   | task_name            | str    | yes      | Snapshot of the task name at baseline time (informational, not lookup)             |
| D   | snapshot_date        | date   | yes      | ISO date the snapshot was taken                                                    |
| E   | baseline_start       | date   | yes      | The task's start date AT snapshot time                                             |
| F   | baseline_end         | date   | yes      | The task's end date AT snapshot time                                               |
| G   | baseline_duration    | int    | yes      | Working days at snapshot time                                                      |
| H   | baseline_predecessors| str    | yes      | Raw column-K predecessor string at snapshot time (e.g. `1FS,2SS+3`); enables baseline critical-path recomputation |
| I   | snapshot_label       | str    | no       | Optional human label, e.g. "Q2 plan freeze"                                        |
| J   | snapshot_actor       | str    | no       | Defaults to `$USER`; can be overridden via `--actor`                               |

**Active-baseline rule:** for any (program, wbs), the *active baseline* is the
row with the most recent `snapshot_date`. Ties are broken by row order (later
row wins). All slip math reads the active baseline only; older rows are
preserved purely for audit.

**Tab styling:** bold + frozen header row, freeze first 3 columns
(program, wbs, task_name), date columns formatted ISO. Created lazily on first
`gantt baseline snapshot` — bootstrap doesn't pre-create it.

### No changes to per-program tabs (`P_<name>`)

Crucial constraint: **do not touch the 13-column per-program tab schema.** The
column letters, ARRAYFORMULA, CF rules, and column-index constants in
`gantt_lib/schema.py` stay exactly as they are. Baseline data lives entirely in
`_Baselines`.

This is a deliberate decision over alternative schemas (extending per-program
tabs to cols N-Q, or per-program `B_<name>` tabs). The single-tab design
trades a small lookup cost (filter `_Baselines` by program name) for zero
disruption to the existing rendering pipeline and zero drift risk between a
"current columns" + "history tab" pair.

## Commands

All three are subcommands of a new `gantt baseline` parser group (matches the
existing `gantt task` / `gantt program` pattern).

### `gantt baseline snapshot`

```
gantt baseline snapshot --program=TPM90
gantt baseline snapshot --all
gantt baseline snapshot --program=TPM90 --rebaseline
gantt baseline snapshot --program=TPM90 --label="Q2 plan freeze"
```

**Behavior:**

1. Resolve target programs: `--program=X` → just X; `--all` → every
   `P_*` tab. Exactly one of `--program` or `--all` is required.
2. For each target program, read all tasks from `P_<name>`.
3. For each task, check `_Baselines` for an existing row with this `(program,
   wbs)`. If any row exists for **any** task in the program, refuse the entire
   program unless `--rebaseline` is set. Refusal is per-program — partial-snapshot
   states are not allowed (a program is fully baselined or not).
4. On success, append one row per task to `_Baselines` with today's date as
   `snapshot_date` and `$USER` (or `--actor`) as `snapshot_actor`.
5. Print `gantt: baseline snapshot — <program>, <N> tasks, <date> ✓` per
   program (one line per program when `--all`).
6. On rebaseline, the message becomes `gantt: baseline rebaselined — ...`.

**Refusal message** (when active baseline exists, no `--rebaseline`):
```
gantt: baseline already exists for TPM90 (last snapshot 2026-04-12).
       Use --rebaseline to replace the slip-comparison anchor.
       History is always preserved. ✗
```

**Edge cases:**
- Program has zero tasks → emit `gantt: baseline snapshot — <program>, 0 tasks (skipped) ⚠` and continue.
- Program tab doesn't exist → `gantt: baseline snapshot — <program> not found ✗` (exit 1).
- Task has empty start/end → snapshot the empty values (downstream `show`
  will display "—" and skip slip math for that task).

**`--rebaseline` with task-set changes:** when the program's task set has
changed since the last snapshot, `--rebaseline` writes fresh rows for the
**current** task set:
- New tasks (not present at last snapshot) get their first baseline row.
- Existing tasks get their active baseline anchor moved to the new snapshot.
- Tasks that existed at last snapshot but no longer exist in the program have
  their old rows preserved in `_Baselines` as inert history — they don't
  appear in `show` (no current task to compare against), and they're not
  cleaned up automatically. Use `gantt baseline clear` if you want them gone.

This matches replanning reality: scope expands and contracts between
snapshots, and we want the new baseline to reflect *the plan as it stands*
without requiring extra ceremony.

### `gantt baseline show`

```
gantt baseline show --program=TPM90                    # summary + top 10 slippers
gantt baseline show --program=TPM90 --top=5            # summary + top 5 slippers
gantt baseline show --program=TPM90 --all              # summary + full per-task table
gantt baseline show --program=TPM90 --slipping-only    # summary + only tasks with end-slip > 0
gantt baseline show --program=TPM90 --critical-path-only
gantt baseline show --program=TPM90 --milestones-only
gantt baseline show --all-programs                     # rolls up all programs (summary block per program, no detail table)
```

**Behavior:**

1. For each target program, build the active baseline (latest snapshot per
   task) from `_Baselines`.
2. Read current `(start, end, duration)` from `P_<name>`.
3. Compute slip per task: `Δstart = current_start - baseline_start` (in
   calendar days, signed), same for end.
4. **Print summary block** — always shown, regardless of detail flags:
   ```
   TPM90 — last baseline 2026-04-12 (32d ago, by jason.garcia)
   Tasks:    47 total | 12 done | 28 in progress | 7 not started
   Slip:     +6d max end-slip | +2.1d mean | 18 slipping | 4 ahead | 25 on-baseline
   Critical: 60d → 66d (+6d)
   ```
   The `Critical:` line recomputes the critical path **against baseline
   `(start, end, predecessors)`** using the existing `gantt_lib/critical_path.py`
   module, then compares to the current critical path. The delta is honest
   even when the CP itself shifted to different tasks since baseline (the
   comparison is path-length to path-length, not task-by-task).
5. **Print detail table** — content depends on flags:
   - **Default** → top 10 slippers by abs end-slip desc (omit if zero slipping
     tasks; show "All tasks tracking to baseline ✓" instead).
   - **`--top=N`** → top N slippers (N capped at total task count).
   - **`--slipping-only`** → all tasks with end-slip > 0, sorted by abs end-slip desc.
   - **`--all`** → every task with an active baseline, sorted by WBS.
   - **`--critical-path-only` / `--milestones-only`** → filters applied first,
     then the same default-vs-flag display logic. Combinable: `--milestones-only
     --slipping-only` shows only slipping milestones.

   Table columns (column-aligned, matches `gantt critical-path` style):
   ```
   wbs   name                            baseline_start  current_start  Δstart  baseline_end  current_end  Δend
   2     Eyepiece fab                    2026-04-09      2026-04-15          +6  2026-04-22    2026-04-30     +8
   1     Concept                         2026-04-01      2026-04-01           0  2026-04-08    2026-04-10     +2
   ```
6. Print result line: `gantt: baseline show — <program>, <N> tasks, max slip +<X>d, <M> ahead ✓`.
   (`<M> ahead` counts tasks with negative Δend. Counts are computed across
   the *entire* program, not the filtered detail view, so the result line is
   consistent regardless of which flags were used.)
7. Tasks without an active baseline are excluded from the detail table by
   default (would only fill space with `—`); they're noted in the summary as
   "<K> tasks not yet baselined" if K > 0.

**`--all-programs` mode:** prints one summary block per program, separated by
blank lines, with no detail tables. Useful for the strategic deck's portfolio
status slide. The result line aggregates across programs:
`gantt: baseline show — N programs, max slip +Xd in <worst-program> ✓`.

### `gantt baseline clear`

```
gantt baseline clear --program=TPM90 --force
gantt baseline clear --all --force
```

**Behavior:**

1. Without `--force`, refuse: `gantt: baseline clear refused — pass --force
   (this deletes <N> historical snapshots) ✗`.
2. With `--force`, delete all `_Baselines` rows matching the target program(s).
3. Print `gantt: baseline cleared — <program>, <N> historical snapshots removed ✓`.

**No undo.** Clear is destructive on purpose. There's no "soft delete" because
`_Baselines` is already the audit log; there's no log behind the log.

## Project Structure

```
gantt_lib/
  baseline.py        # NEW: pure-logic — read/diff/filter, no Sheets dep
  schema.py          # extend with _Baselines tab constants + create-tab request
  ...                # everything else unchanged

gantt                # extend with cmd_baseline_snapshot / show / clear handlers
                     # + argparse wiring under a new `baseline` subparser group

tests/
  test_baseline.py   # NEW: pure-logic tests for baseline.py
  test_baseline_io.py # NEW: Sheets-mock tests for cmd_baseline_* handlers
  fixtures/
    baselines.py     # NEW: factory helpers (build_baseline_row, build_program_with_baselines)
```

## Code Style

Match existing conventions exactly. Example of a baseline-module function:

```python
# gantt_lib/baseline.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from .model import Task

BASELINE_TAB = "_Baselines"
BASELINE_HEADERS = [
    "program", "wbs", "task_name", "snapshot_date",
    "baseline_start", "baseline_end", "baseline_duration",
    "baseline_predecessors", "snapshot_label", "snapshot_actor",
]


@dataclass(frozen=True)
class BaselineRow:
    program: str
    wbs: str
    task_name: str
    snapshot_date: date
    baseline_start: Optional[date]
    baseline_end: Optional[date]
    baseline_duration: int
    baseline_predecessors: str = ""
    snapshot_label: str = ""
    snapshot_actor: str = ""


def active_baselines(rows: Iterable[BaselineRow], program: str) -> dict[str, BaselineRow]:
    """Return {wbs: latest BaselineRow} for `program`, latest by snapshot_date.

    Ties on snapshot_date are broken by iteration order — later rows win. This
    matches the sheet semantic that newer rows append to the bottom.
    """
    out: dict[str, BaselineRow] = {}
    for r in rows:
        if r.program != program:
            continue
        prev = out.get(r.wbs)
        if prev is None or r.snapshot_date >= prev.snapshot_date:
            out[r.wbs] = r
    return out
```

CLI handler pattern (matches `cmd_recalc`, `cmd_critical_path`):

```python
def cmd_baseline_snapshot(args: argparse.Namespace) -> int:
    ss = _open_workbook()
    programs = _resolve_target_programs(ss, args)
    for program_name in programs:
        # ... read tasks, check existing, append rows, emit ok/die
    return 0
```

## Testing Strategy

- **Framework:** pytest (already in use). Run with `pytest -q` from repo root.
- **Pure logic** (`gantt_lib/baseline.py`): unit tests in `tests/test_baseline.py`
  cover `active_baselines`, slip computation, and edge cases (empty program,
  task with no start/end, baseline-but-no-current, current-but-no-baseline,
  ties on snapshot_date).
- **Sheets I/O** (`cmd_baseline_*` in `gantt`): tests in
  `tests/test_baseline_io.py` use the same gspread-mock pattern as
  `tests/test_sheets_helpers.py`. Verify: refusal path emits the right
  message, rebaseline appends rather than replaces, clear removes the right
  rows, missing tab is created on first snapshot.
- **No coverage thresholds enforced**, but new pure-logic module should hit
  100% lines (it's small and easy).
- **No live-Sheets integration test** — same boundary as the rest of the
  codebase. The CLI handlers are thin orchestration over `gantt_lib`; the
  unit tests on the lib + the mock tests on the handlers are sufficient.

## Boundaries

**Always:**
- Append to `_Baselines` on every successful snapshot (never overwrite a row).
- Show a clear refusal message + the next-step flag when refusing.
- Use `_resolve_target_programs(ss, args)` to handle `--program` / `--all`
  uniformly across all three subcommands.

**Ask first** (in this case = require an explicit flag):
- Replacing the active baseline → require `--rebaseline`.
- Deleting historical snapshots → require `--force`.

**Never:**
- Modify the 13-column per-program tab (`P_<name>`) schema.
- Modify or delete a row in `_Baselines` other than via `gantt baseline clear`.
- Snapshot a program partially (all-or-nothing per program; if any task fails
  to read, abort the program).
- Compute slip in working days. Slip is reported in calendar days for
  intuition (a 5-day slip across a weekend reads as `+5`, not `+3`). The
  duration column is in working days because cascade math uses working days,
  but slip is a human-facing metric and should be plain-English calendar.

## Open Questions

None blocking. Two minor calls I'm making without asking:

- **`snapshot_actor` defaults to `os.environ.get("USER", "unknown")`**, not
  `git config user.name`. Reason: the CLI runs in many contexts (Claude Code,
  bare shell, future scheduled routine) and `$USER` is reliably set in all of
  them; git config is sometimes absent.
- **Slip column header is `Δstart` / `Δend`** (Greek delta). If a terminal
  renders it as `?`, fall back to ASCII `dStart`/`dEnd`. I'll detect via
  `sys.stdout.encoding` at print time.

If either is wrong, flag and I'll change before the Plan phase.
