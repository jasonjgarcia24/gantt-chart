# Plan: Baseline Tracking Implementation

**Spec:** [baseline-tracking.md](../specs/baseline-tracking.md)
**Status:** Awaiting approval before Tasks breakdown
**Date:** 2026-05-14

## Component map

| # | Component | Type | Lines (est.) | Purpose |
|---|-----------|------|--------------|---------|
| 1 | `gantt_lib/baseline.py`            | new module        | ~150 | Pure logic — `BaselineRow` dataclass, `active_baselines()`, slip math, summary computation |
| 2 | `gantt_lib/schema.py` additions     | extension          | ~20  | (Optional) `_Baselines` tab formatting requests if we want bold/freeze/date-format on first create |
| 3 | `gantt` script: I/O helpers         | new functions      | ~120 | `_read_baselines_tab`, `_ensure_baselines_tab`, `_append_baseline_rows`, `_delete_baseline_rows`, `_resolve_target_programs` |
| 4 | `gantt` script: CLI handlers        | new functions      | ~180 | `cmd_baseline_snapshot`, `cmd_baseline_show`, `cmd_baseline_clear` |
| 5 | `gantt` script: argparse wiring     | extension          | ~40  | New `baseline` subparser with three sub-subparsers |
| 6 | `gantt` script: `cmd_info` patch    | small extension    | ~15  | Add baseline-coverage line per program |
| 7 | `tests/test_baseline.py`            | new test file      | ~250 | Pure-logic tests, target 100% line coverage |
| 8 | `tests/test_baseline_io.py`         | new test file      | ~250 | Sheets-mock handler tests (refusal, rebaseline, clear, missing tab) |
| 9 | `tests/fixtures/baselines.py`       | new fixture module | ~50  | `make_baseline_row()`, `program_with_baselines()` factories |
| 10 | `README.md` — baseline section      | doc extension      | ~40  | Document the three commands, link to spec |

**Net estimate:** ~1100 lines added across 10 files. Pure-logic-to-glue ratio ~25/75, which is healthy.

## Implementation order (sequential)

```
1. baseline.py + tests          ──→  pure logic, no I/O, easy to validate
       │
       ▼
2. I/O helpers (script-private) ──→  read/write _Baselines tab via gspread
       │
       ▼
3. cmd_baseline_snapshot        ──→  first user-visible cmd; exercises I/O end-to-end
       │
       ▼
4. cmd_baseline_show            ──→  consumes snapshots; needs nothing new from sheets
       │
       ▼
5. cmd_baseline_clear           ──→  destructive cmd; needs delete-row helper
       │
       ▼
6. cmd_info patch               ──→  trivial; uses helpers from steps 2-3
       │
       ▼
7. handler-level tests          ──→  mock gspread, verify refusal/rebaseline/clear paths
       │
       ▼
8. README                       ──→  documentation, no code
```

Steps 1, 2, 6, 8 are independently committable. Steps 3-5 are coupled (each
adds an argparse command + a handler) and could be one commit per command or
one bundled commit — leaning toward **one commit per command** for clean
history (matches how `gantt task add/update/delete` were committed historically).

## Risks and mitigations

### High

**R1. Critical-path delta in summary is unimplementable as spec'd.** The spec
shows `Critical: 60d → 66d (+6d)` in the summary block, implying we know the
baseline critical path. But the baseline schema only stores `(start, end,
duration)` — no `predecessors`. Without predecessors we can't recompute CP
against baseline dates.

Three options, ranked:
- **R1.A (recommended):** Add `predecessors` as col J in `_Baselines`. One
  extra column, snapshots the *plan* not just the *dates*. Enables proper CP
  delta in the summary, supports future "what changed since baseline?"
  features (added/removed dependencies). Cost: one more column, one more
  field on `BaselineRow`, marginal test additions.
- **R1.B:** Drop CP delta from the summary entirely. Replace with current CP
  length only: `Critical path: 66d`. Simpler, but loses signal that's the
  whole point of the strategic deck.
- **R1.C:** Approximate — sum the baseline durations of tasks currently on
  the CP. Misleading: if the CP itself shifted to different tasks since
  baseline, the comparison is apples-to-oranges. **Don't ship this.**

**Decision needed:** which option?

**R2. `--rebaseline` semantics for added/removed tasks.** Spec doesn't define
behavior when the program's task set has changed since the last snapshot.
Three reasonable interpretations:
- **R2.A (recommended):** `--rebaseline` writes fresh rows for the *current*
  task set. New tasks get their first baseline. Existing tasks get their
  anchor moved. Tasks that existed at last snapshot but no longer exist get
  no new row — their old baseline rows stay in `_Baselines` but are inert
  (no current task to compare against, so they don't appear in `show`).
- **R2.B:** Refuse to `--rebaseline` if the task set has changed; require an
  explicit `--allow-set-change` second flag. Safer but more friction; doesn't
  match how Jason actually replans (tasks come and go between snapshots).
- **R2.C:** Snapshot only tasks that already had a baseline; ignore new ones.
  Bad — leaves new tasks unbaselined silently.

**Decision needed:** confirm R2.A.

### Medium

**R3. Atomic snapshot writes.** Spec says snapshots are all-or-nothing per
program. If we append rows one-at-a-time and the script crashes mid-loop,
`_Baselines` ends up partially populated for that program.

Mitigation: use gspread's `worksheet.append_rows(rows)` (batch write) instead
of `append_row()` per task. The Sheets API processes the batch as a single
request — either all rows land or none do. Verified pattern; already used
elsewhere in the script for bulk task writes.

**R4. Tab-creation race.** If `_Baselines` doesn't exist and two snapshot
commands run concurrently, both could try to create it. In practice this is
a single-user CLI; not engineering for it. Just catch the "duplicate sheet"
error gracefully and proceed to append.

**R5. Malformed historical rows.** A user might hand-edit `_Baselines` and
break a date. `_read_baselines_tab` should skip malformed rows with a
stderr warning, not crash. Active-baseline lookup degrades to the latest
valid row.

### Low

**R6. `_Baselines` unbounded growth.** 50 tasks × weekly snapshots × 1 year =
2600 rows. Sheets handles this fine. No archival needed for Phase 1.

**R7. Program renames orphan baselines.** Out of scope. Document as a known
limitation; programs aren't renamed in practice.

**R8. Unicode `Δ` rendering.** Already addressed in the spec (Open Questions).
Detect via `sys.stdout.encoding` and fall back to `dStart`/`dEnd`. Keep the
detection in one place: a small `_slip_header_chars()` helper in the
gantt script (not in `baseline.py` — keep that pure).

## Verification checkpoints

| After step | Verification | Pass criterion |
|------------|-------------|----------------|
| 1 (baseline.py)        | `pytest tests/test_baseline.py -q`                              | All tests pass; coverage report shows 100% line coverage on `gantt_lib/baseline.py` |
| 2 (I/O helpers)        | Manual smoke: `python -c "from gantt import _read_baselines_tab; ..."` against live workbook | Returns empty list (no `_Baselines` tab yet); no exception |
| 3 (snapshot)           | Manual: `gantt baseline snapshot --program=TPM90` then inspect `_Baselines` tab in the browser | Tab created with header row + N rows matching task count |
| 3 (snapshot, repeat)   | Manual: re-run `gantt baseline snapshot --program=TPM90`        | Refused with the spec'd error message; exit code 1; no rows added |
| 3 (rebaseline)         | Manual: `gantt baseline snapshot --program=TPM90 --rebaseline`  | N more rows appended (now 2N total); active baseline = latest snapshot date |
| 4 (show)               | Edit a task end date +5d, then `gantt baseline show --program=TPM90` | Summary shows max slip +5d; top-slippers table includes that task at top |
| 4 (show, all-programs) | `gantt baseline show --all-programs`                            | One summary block per program; no detail tables; aggregated result line |
| 5 (clear)              | `gantt baseline clear --program=TPM90`                          | Refused without `--force` |
| 5 (clear, force)       | `gantt baseline clear --program=TPM90 --force`                  | All TPM90 rows removed from `_Baselines`; other programs untouched |
| 6 (info)               | `gantt info`                                                    | Each program shows baseline coverage line |
| 7 (handler tests)      | `pytest -q` (full suite)                                        | All tests pass, no regressions in existing modules |
| 8 (README)             | Visual review                                                   | Three commands documented with realistic examples; spec linked |

**Final acceptance gate:** Hand off to Negev (acceptance-exploration agent)
with stage = "MVP" and feature scope = "baseline tracking end-to-end against
the live TPM90 workbook." Negev runs the golden path + a couple of edge cases
(refusal flows, malformed-row tolerance) and returns pass/fail.

## What's NOT in scope (deferred)

- **Baseline visualization in the per-program tab.** No baseline overlay on the
  gantt timeline. The spec deliberately keeps `P_<name>` untouched. If we want
  visual indication of slip in the sheet later, that's a separate spec.
- **Snapshot scheduling.** No cron, no auto-snapshot. Manual invocation only.
  Jason will wire scheduling via a routine if/when wanted (per Phase 2 of the
  deck initiative discussion).
- **Diff between two arbitrary snapshots.** `show` always compares current vs.
  active baseline. No `gantt baseline diff --from=2026-04-01 --to=2026-05-01`.
  Add later if useful.
- **Snapshot deletion of a single row.** `clear` is per-program nuke. No
  surgical delete. Unlikely to need.
- **Per-task baseline (e.g., baseline one task without snapshotting the whole program).** All-or-nothing per program is a load-bearing simplification.

## Decisions needed before Tasks breakdown

1. **R1: critical-path delta** — pick R1.A (add `predecessors` col), R1.B
   (drop delta from summary), or R1.C (approximate — not recommended).
2. **R2: `--rebaseline` with task-set changes** — confirm R2.A or pick a
   different option.

Both are small spec amendments. Once confirmed, I'll update the spec, then
generate the Tasks breakdown.
