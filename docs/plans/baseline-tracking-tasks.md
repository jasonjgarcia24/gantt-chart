# Tasks: Baseline Tracking Implementation

**Spec:** [baseline-tracking.md](../specs/baseline-tracking.md)
**Plan:** [baseline-tracking-plan.md](baseline-tracking-plan.md)
**Status:** Awaiting approval to begin
**Decisions locked:** R1.A (predecessors snapshotted) · R2.A (rebaseline replans current task set)

Each task is a single focused commit (or two, where noted). Order is strictly
sequential — later tasks depend on earlier ones compiling and passing tests.

---

### T1 — Pure-logic foundation: `BaselineRow`, `active_baselines`, `slip_days`

- **Acceptance:**
  - `gantt_lib/baseline.py` exists with `BaselineRow` dataclass (10 fields per spec)
  - `BASELINE_TAB`, `BASELINE_HEADERS` module-level constants
  - `active_baselines(rows: Iterable[BaselineRow], program: str) -> dict[str, BaselineRow]` returns latest snapshot per WBS
  - `slip_days(current: date | None, baseline: date | None) -> int | None` — handles None on either side gracefully
  - Tie-breaking on `snapshot_date` documented in docstring (later iteration wins)
- **Verify:** `pytest tests/test_baseline.py -q` — all green; `coverage report --include='gantt_lib/baseline.py'` shows 100%
- **Files:** `gantt_lib/baseline.py` (new), `tests/test_baseline.py` (new), `tests/fixtures/baselines.py` (new — `make_baseline_row()` factory)

### T2 — Pure-logic: summary computation + critical-path delta

- **Acceptance:**
  - `compute_summary(program, current_tasks, baselines, today) -> Summary` dataclass returning all fields rendered in the spec's summary block (last_baseline_date, last_baseline_actor, days_since, task counts by status, max/mean end-slip, slipping/ahead/on-baseline counts, baseline_cp_days, current_cp_days, cp_delta)
  - Baseline CP recomputation uses existing `gantt_lib/critical_path.py` against the baseline `(start, end, duration, predecessors)` reconstructed from `BaselineRow`s
  - Edge cases covered in tests: zero tasks, all on-baseline, no baseline rows for program, baseline-only-some-tasks, ties on snapshot_date
- **Verify:** `pytest tests/test_baseline.py -q` — all green; new tests for `compute_summary` and CP-delta edge cases
- **Files:** `gantt_lib/baseline.py`, `tests/test_baseline.py`, `tests/fixtures/baselines.py`

### T3 — Sheets I/O helpers (script-private)

- **Acceptance:**
  - `_resolve_target_programs(ss, args) -> list[str]` — handles `--program=X` / `--all` / `--all-programs` uniformly; refuses if both/neither set
  - `_read_baselines_tab(ss) -> list[BaselineRow]` — returns `[]` if tab missing; skips malformed rows with stderr warning (does not crash)
  - `_ensure_baselines_tab(ss) -> Worksheet` — creates `_Baselines` with header row + freeze + bold + ISO date format on cols D-F if missing; returns existing if present (idempotent, catches duplicate-sheet error)
  - `_append_baseline_rows(ss, rows: list[BaselineRow])` — single batch write via `worksheet.append_rows` (atomic per the plan's R3)
  - `_delete_baseline_rows_for_programs(ss, programs: list[str]) -> int` — returns count of deleted rows
- **Verify:** `python -c "from gantt import _read_baselines_tab, get_client; ss = get_client().open_by_key(...); print(_read_baselines_tab(ss))"` against live workbook returns `[]` cleanly without error
- **Files:** `gantt`

### T4 — Command: `gantt baseline snapshot` + argparse wiring

- **Acceptance:**
  - New `baseline` subparser group with `snapshot` sub-subparser
  - Flags: `--program=X | --all` (mutually exclusive, one required), `--rebaseline`, `--label="..."`, `--actor=...`
  - Refusal path emits the spec'd error message verbatim with exit 1; no rows written
  - Success path writes one row per current task to `_Baselines`, emits `gantt: baseline snapshot — <program>, N tasks, YYYY-MM-DD ✓`
  - Rebaseline path emits `gantt: baseline rebaselined — ...`
  - `--all` iterates over every `P_*` tab; per-program success/refusal lines emitted independently
  - Snapshot writes `baseline_predecessors` from current col K
  - `snapshot_actor` defaults to `os.environ.get("USER", "unknown")`
- **Verify:**
  - `gantt baseline snapshot --program=TPM90` against live workbook → `_Baselines` tab created with header + N rows; visual check in browser
  - Re-run same command → refused with exit 1 and the spec'd error text
  - Run with `--rebaseline` → N more rows appended
  - `gantt baseline snapshot --program=NoSuchProgram` → `gantt: baseline snapshot — NoSuchProgram not found ✗`, exit 1
- **Files:** `gantt`

### T5 — Command: `gantt baseline show` + argparse wiring

- **Acceptance:**
  - `show` sub-subparser with `--program=X | --all-programs` (one required), `--top=N` (default 10), `--all`, `--slipping-only`, `--milestones-only`, `--critical-path-only`
  - Summary block printed first, always, exactly matching spec format
  - Detail table behavior matches spec table (default = top 10 slippers; `--all` = full sorted by WBS; `--slipping-only` = all with end-slip > 0; filters combine)
  - Result line includes program-wide max slip and ahead counts (NOT filtered-view counts)
  - `--all-programs` prints one summary block per program separated by blank line, no detail tables, aggregated result line
  - Δ headers fall back to `dStart`/`dEnd` if `sys.stdout.encoding` can't render Unicode
  - Tasks without active baseline are omitted from detail; counted in summary as "K tasks not yet baselined" if K > 0
- **Verify:**
  - Snapshot TPM90 → edit one task end date +5 days → `gantt baseline show --program=TPM90` → summary shows max slip +5d; that task at top of detail
  - `gantt baseline show --program=TPM90 --slipping-only` → only that one task in detail
  - `gantt baseline show --all-programs` → summary blocks for TPM90, Q3Launch, OK2DC; no tables
- **Files:** `gantt`

### T6 — Command: `gantt baseline clear` + argparse wiring

- **Acceptance:**
  - `clear` sub-subparser with `--program=X | --all`, `--force`
  - Without `--force`: emit spec'd refusal, exit 1, no rows touched
  - With `--force`: delete matching rows, emit `gantt: baseline cleared — <program>, N historical snapshots removed ✓`
  - `--all --force` clears every program's baselines
  - Other programs' rows untouched when scoping to one program
- **Verify:**
  - Snapshot TPM90, then `gantt baseline clear --program=TPM90` → refused
  - `gantt baseline clear --program=TPM90 --force` → all TPM90 rows gone
  - Snapshot TPM90 + Q3Launch, clear TPM90, verify Q3Launch rows still present
- **Files:** `gantt`

### T7 — Patch `cmd_info` to report baseline coverage

- **Acceptance:**
  - For each program in `gantt info` output, add a line: `baseline: K/N tasks baselined, last snapshot YYYY-MM-DD by <actor>` (or `baseline: none` if no rows for the program)
  - No new flags; this is always-on extra info
  - Reads via `_read_baselines_tab` once, computes per-program coverage in memory
- **Verify:** `gantt info` after T4 snapshot shows baseline line for TPM90; before T4 snapshot shows `baseline: none` for all programs
- **Files:** `gantt`

### T8 — Handler tests with gspread mocks

- **Acceptance:**
  - `tests/test_baseline_io.py` covers:
    - `cmd_baseline_snapshot` happy path (writes N rows)
    - `cmd_baseline_snapshot` refusal when active baseline exists (no rows written)
    - `cmd_baseline_snapshot` rebaseline path (N more rows appended)
    - `cmd_baseline_snapshot` missing tab → tab created
    - `cmd_baseline_snapshot` missing program → exit 1, spec'd error message
    - `cmd_baseline_snapshot` zero-task program → warn, skip, exit 0
    - `cmd_baseline_show` summary computation against mocked tab data
    - `cmd_baseline_show` `--all-programs` aggregation
    - `cmd_baseline_clear` refusal without `--force`
    - `cmd_baseline_clear` removes only target program's rows
  - Same gspread-mock pattern as `tests/test_sheets_helpers.py`
  - Malformed-row test: `_read_baselines_tab` skips bad row, emits stderr warning, returns valid rows
- **Verify:** `pytest -q` (full suite) — all green, no regressions in existing modules
- **Files:** `tests/test_baseline_io.py` (new), `tests/fixtures/baselines.py` (extend)

### T9 — README documentation

- **Acceptance:**
  - New `## Baseline tracking` section in README between existing command sections
  - Documents all three commands with realistic example invocations + expected output
  - Links back to `docs/specs/baseline-tracking.md` for the full spec
  - Brief note on the `_Baselines` tab and the active-baseline rule
  - `gantt baseline --help` output matches the documented surface
- **Verify:** Visual review of README; run `gantt baseline --help` and `gantt baseline snapshot --help` and confirm flags match docs
- **Files:** `README.md`

---

## Final acceptance gate

After T9: hand off to **Negev** (acceptance-exploration agent) with:
- Stage = MVP
- Scope = baseline tracking end-to-end against the live TPM90 workbook
- Golden path: snapshot → edit task → show → rebaseline → show → clear --force
- Edge cases: refusal flow, malformed-row tolerance, `--all-programs` aggregation

Negev returns `negev: acceptance passed — MVP, N flows verified ✓` (or fail with evidence).

## Execution notes

- **Atomicity (R3):** every multi-row write goes through `worksheet.append_rows()` in T3, never `append_row()` in a loop.
- **Token budget:** zero LLM calls in this phase — all pure code + manual testing. No subagent spawns until the Negev hand-off at the end.
- **Commit style per task:** match existing repo convention (`feat:`/`test:`/`docs:` prefix, ≤70 char subject, body explains why). T4–T6 may each warrant 2 commits if the diff is large (handler + tests separately), but TDD-discipline keeps them tight.
- **No backwards-compat shims needed** — `_Baselines` tab is new, doesn't exist anywhere, no migration.
