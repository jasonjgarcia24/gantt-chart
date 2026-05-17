# Tasks: Linear Integration — Phase 1

**Spec:** [linear-integration.md](../specs/linear-integration.md)
**Plan:** [linear-integration-plan.md](linear-integration-plan.md)
**Status:** Awaiting approval to begin

Each task = one focused commit (or two where noted). Strictly sequential
— later tasks depend on earlier ones compiling and passing tests.

Phase scope: pull a Linear project into a workbook tab via the Linear MCP.
After Phase 1, existing gantt verbs (`recalc`, `critical-path`, `deck`,
`baseline`, `shift`, etc.) work on the pulled program identically to a
workbook-native one. No Linear writes happen in Phase 1.

---

### T1 — `gantt_lib/cp/` package + JSON contracts

- **Acceptance:**
  - `gantt_lib/cp/__init__.py` (re-exports `CpInput`, `CpOutput`, `CpError`)
  - `gantt_lib/cp/contracts.py` with:
    - `@dataclass CpInputProject` (name, source, source_ref)
    - `@dataclass CpInputConfig` (default_duration_days, estimate_to_days, today)
    - `@dataclass CpInputIssue` (linear_id, title, state, estimate_days,
      percent, assignee, start_anchor, end_anchor, is_milestone, parent_linear_id)
    - `@dataclass CpInputEdge` (from_linear_id, to_linear_id, type, lag_days)
    - `@dataclass CpInput` (project, config, issues, edges)
    - `@dataclass CpOutputItem` (linear_id, wbs_id, title, start, end,
      slack_days, on_critical_path)
    - `@dataclass CpOutputWarning` (linear_id, kind, message)
    - `@dataclass CpOutput` (ok, project, computed_at, critical_path, cascade, warnings)
    - `@dataclass CpError` (ok=False, error, detail, trace)
    - `from_json(s: str) -> CpInput` / `to_json(o) -> str` using stdlib `json` + `dataclasses.asdict`
  - Unknown keys in input JSON ignored (forward-compat); missing required
    fields raise `ContractValidationError` with a clear message
  - Date fields are ISO strings on the wire, `datetime.date` on the dataclass
- **Verify:**
  - `pytest tests/test_cp_contracts.py -q` — all green
  - Round-trip property: `to_json(from_json(s))` equals `s` modulo key ordering
- **Files:** `skills/gantt/scripts/gantt_lib/cp/__init__.py`,
  `skills/gantt/scripts/gantt_lib/cp/contracts.py`,
  `tests/test_cp_contracts.py`

### T2 — `gantt_lib/cp/adapter.py` + fixture-driven tests

- **Acceptance:**
  - `gantt_lib/cp/adapter.py` with:
    - `cp_input_to_program(inp: CpInput, wbs_assignments: dict[str, str]) -> Program`:
      - Takes a Linear-id → WBS-id map (caller supplies it; for the pull
        path, the orchestrator computes it from `_LinearSync` + new
        assignments)
      - Builds `Task` objects mapping `linear_id → wbs_assignments[linear_id]`
        for `Task.id`
      - Maps issue fields to Task fields per the spec's mapping table
      - Builds predecessor DSL strings from edges (grouped by `to_linear_id`,
        translated to WBS ids, formatted as `"1FS+0, 2SS"`)
      - Fallback for missing estimate: use `config.default_duration_days`,
        emit a warning
    - `program_results_to_cp_output(inp, program, slack_map, wbs_assignments) -> CpOutput`:
      - Builds cascade entries with both `linear_id` and `wbs_id`
      - Critical path = filter to slack==0, order by start
      - Warnings include missing-estimate flags
    - `run_cp(inp: CpInput, wbs_assignments: dict[str, str]) -> CpOutput | CpError`:
      - Top-level orchestrator: catches `CycleError`, `UnanchoredError`,
        `MissingPredecessorError`, translates to `CpError` shapes
  - **Cascade engine and slack engine are reused unchanged** — no edits
    to `gantt_lib/cascade.py` or `gantt_lib/critical_path.py`
- **Verify:**
  - `pytest tests/test_cp_adapter.py -q` — all green
  - 6 fixture pairs in `tests/fixtures/cp/` round-trip correctly:
    `linear_chain.json`, `fan_in.json`, `fan_out.json`,
    `missing_estimate.json`, `cycle.json`, `empty.json`
- **Files:** `skills/gantt/scripts/gantt_lib/cp/adapter.py`,
  `tests/test_cp_adapter.py`, `tests/fixtures/cp/*.json`

### T3 — `gantt_lib/linear/sync_tab.py` + tests

- **Acceptance:**
  - `gantt_lib/linear/__init__.py` (package marker)
  - `gantt_lib/linear/sync_tab.py` with:
    - `SYNC_TAB_NAME = "_LinearSync"`
    - `SYNC_TAB_HEADERS = ["program", "wbs_id", "linear_id", "last_synced", "linear_url"]`
    - `ensure_sync_tab(spreadsheet) -> Worksheet`: creates the tab if
      absent, with hidden=True, header row + a DO-NOT-EDIT warning row
    - `read_links(spreadsheet, program: str) -> list[SyncLink]`: returns
      the rows for one program (excluding header + warning rows)
    - `upsert_links(spreadsheet, program: str, links: list[SyncLink]) -> None`:
      replaces all rows for `program` (delete + re-append); other programs untouched
    - `delete_links(spreadsheet, program: str) -> None`: for `--force`
      rebuilds
    - Schema validation on read: if header row drifted, raise
      `SyncTabSchemaError` with recovery hint
- **Verify:**
  - `pytest tests/test_linear_sync_tab.py -q` — all green
  - Tests use a fake spreadsheet (extend the existing
    `tests/fixtures/fake_sheets.py` pattern if present, else add one)
  - Covered: bootstrap-from-empty, read empty program, upsert into new
    program, upsert replaces old links, multi-program isolation, schema
    validation failure
- **Files:** `skills/gantt/scripts/gantt_lib/linear/__init__.py`,
  `skills/gantt/scripts/gantt_lib/linear/sync_tab.py`,
  `tests/test_linear_sync_tab.py`

### T4 — `gantt_lib/linear/pull.py` + fixture-driven tests

- **Acceptance:**
  - `gantt_lib/linear/pull.py` with:
    - `assign_wbs_ids(issues, existing_links, parent_chain) -> dict[str, str]`:
      - Reuse WBS ids from `existing_links` for already-linked issues
      - Assign sequential next-free ids for new issues (top-level: next
        free integer; sub-issues: next free integer under parent)
      - Respect Linear's `sortOrder` for issue ordering within a level
    - `compute_diff(inp, existing_tab_rows, existing_links) -> PullDiff`:
      - For each Linear issue: classify as `add` / `update` / `unchanged`
      - For each existing workbook row with no Linear ID: classify as
        `workbook_only_preserved`
      - For `update`: per-field, apply conflict policy from spec
        (Linear-wins on title/state/assignee/dueDate/start_anchor;
        workbook-wins on predecessors/duration/percent/notes/team)
      - Return `PullDiff` dataclass with action counts + per-row field
        deltas
    - `apply_diff(spreadsheet, program, diff, *, dry_run: bool) -> PullResult`:
      - If `dry_run`: return `PullResult` without writing
      - Else: write program tab (existing sheets I/O), upsert
        `_LinearSync`, return result
      - On write: each pulled task's name cell is wrapped as
        `=HYPERLINK("<linear_url>", "<escaped_title>")` via
        `value_input_option=USER_ENTERED`. Title is quote-escaped
        (`"` → `""`). Workbook-only rows (no Linear ID) keep their
        name cells unchanged
    - `pull(spreadsheet, inp: LinearPullInput, program: str, *, dry_run: bool, force: bool) -> PullResult`:
      - Top-level: load existing tab + links → compute_wbs → compute_diff
        → apply_diff → return
      - `force=True`: delete existing `_LinearSync` rows for this program
        first; treat every Linear issue as new
  - `gantt_lib/model.py`: add `linear_url: Optional[str] = field(default=None, compare=False, repr=False)`
    to `Task` as a non-column metadata field. `to_row` consumes it to
    decide whether to emit a HYPERLINK formula or plain text for the
    name column. Column count (`NUM_COLUMNS`) unchanged at 13.
- **Verify:**
  - `pytest tests/test_linear_pull.py -q` — all green
  - 5 pull-orchestrator fixtures pass:
    - `first_pull.json`: empty workbook → 12 added; name cells written
      as HYPERLINK formulas
    - `repull_no_changes.json`: re-pull with no Linear changes → 0/0/12
    - `repull_with_conflicts.json`: Linear changed state + workbook changed
      duration → state updates from Linear, duration stays from workbook
    - `repull_workbook_only_rows.json`: workbook has 3 rows with no Linear
      ID → preserved untouched (no name cell rewrite)
    - `special_chars_in_title.json`: one issue title contains `"`,
      `—` (em-dash), and an emoji → HYPERLINK formula round-trips
      cleanly (no `#ERROR!` in cell, displayed text matches original)
  - `--dry-run` path: `apply_diff(dry_run=True)` returns a result whose
    summary matches the non-dry-run result, but no Sheet mutation happens
    (verified by fake-spreadsheet recording 0 writes)
- **Files:** `skills/gantt/scripts/gantt_lib/linear/pull.py`,
  `tests/test_linear_pull.py`, `tests/fixtures/linear_pull/*.json`

### T5 — `linear_cmds.py` + CLI subcommand wiring + handler tests

- **Acceptance:**
  - `gantt_lib/linear_cmds.py` with:
    - `cmd_linear_pull(args, spreadsheet) -> int`
    - Reads stdin, parses via `cp/contracts` + (new) `linear/contracts`
      (or extend cp/contracts if minimal additions), calls `linear/pull.pull`,
      emits JSON summary to stdout, result line to stderr
    - Exit codes: 0 on success, 1 on contract validation error, 2 on
      pull error (e.g., schema validation on `_LinearSync`), 3 on unexpected exception
    - Result line shape (stderr, final line):
      `gantt: linear-pull <program> — <A> added, <U> updated, <K> unchanged ✓`
      (or `... — DRY RUN: <A> would add, <U> would update, <K> unchanged ✓`)
  - `gantt` script:
    - New argparse subparser: `linear-pull` with required `--stdin` flag,
      required `--as <program>` flag, optional `--dry-run`, optional `--force`
    - Routes to `cmd_linear_pull`
    - Subcommand requires OAuth (touches Sheets)
- **Verify:**
  - `pytest tests/test_linear_cmds.py -q` — all green
  - Subprocess smoke (uses fake creds + fake spreadsheet — or skipped if
    not feasible inside pytest, in which case manual smoke documented):
    `cat tests/fixtures/linear_pull/first_pull.json | <skill>/scripts/gantt linear-pull --stdin --as TEST --dry-run`
    exits 0, emits valid JSON summary, stderr result line correct
  - `gantt linear-pull --help` shows the right shape
- **Files:** `skills/gantt/scripts/gantt_lib/linear_cmds.py`,
  `skills/gantt/scripts/gantt`, `tests/test_linear_cmds.py`

### T6 — Linear MCP probe (capture real shapes)

**Live MCP exploration, not pure code.** Informs T7.

- **Acceptance:**
  - Before running: agent asks user which Linear project to probe
    against. User picks; agent confirms before fetching.
  - Captures the following MCP responses to fixture files:
    - `mcp__claude_ai_Linear__list_teams()` → `tests/fixtures/linear_mcp/list_teams.json`
    - `mcp__claude_ai_Linear__get_team(<chosen>)` → `tests/fixtures/linear_mcp/get_team.json`
      (so we can see what estimate unit the team uses)
    - `mcp__claude_ai_Linear__list_projects(team=<chosen>)` → `list_projects.json`
    - `mcp__claude_ai_Linear__get_project(<id>)` → `get_project.json`
    - `mcp__claude_ai_Linear__list_issues(project=<id>)` → `list_issues.json`
    - For one issue with blockers: `mcp__claude_ai_Linear__get_issue(<id>)` → `get_issue.json`
    - `mcp__claude_ai_Linear__list_milestones(project=<id>)` → `list_milestones.json`
    - `mcp__claude_ai_Linear__list_issue_statuses(team=<chosen>)` → `list_issue_statuses.json`
  - Captured fixtures sanitized (or kept as-is per user review) before commit
  - `docs/notes/linear-mcp-shapes.md` written (1-2 paragraphs answering):
    - Does `list_issues` include `blockedBy` / `relations` inline?
    - What estimate unit does the probed team use?
    - What states (default + custom) exist?
    - Any surprises affecting normalization?
- **Verify:**
  - All 8 fixture files exist, valid JSON, user-confirmed OK to commit
  - Notes file answers all 4 questions
- **Files:** `tests/fixtures/linear_mcp/*.json`, `docs/notes/linear-mcp-shapes.md`
- **Cost note:** ~8 MCP tool calls against live Linear. Negligible cost,
  flagged for transparency per the user-feedback memory on heavy-op
  notifications.

### T7 — SKILL.md Linear playbook

- **Acceptance:**
  - New section in `skills/gantt/SKILL.md` titled
    `## Linear MCP mode (Phase 1: pull only)` covering:
    - **Trigger language**: explicit example prompts. SKILL description
      block (YAML frontmatter) extended so the model triggers reliably.
    - **Source detection rule**: how to recognize Linear-targeted prompts
      (URL, team-key prefix, "linear" keyword).
    - **Target tab disambiguation**: ask user for `--as <program>` if not obvious.
    - **MCP call sequence**: written against the actual shape captured
      in T6. Includes pagination handling. Caches `list_teams` and
      `list_users` for the session.
    - **Normalization recipe**: explicit mapping from MCP fields to the
      `linear-pull --stdin` JSON contract. State name mapping table.
      Estimate-unit conversion (using the probed team's unit). Edge
      construction from `blockedBy`.
    - **Dry-run-first convention**: agent always runs with `--dry-run`
      first, surfaces the diff via a markdown table, asks "Apply this?
      (y/n)", then runs without `--dry-run` on confirm. Matches the
      skill's existing "confirm-before-side-effect" pattern.
    - **CLI invocation**: exact Bash tool call shape — piping JSON
      into `<skill-base-dir>/scripts/gantt linear-pull --stdin --as <program>`,
      capturing stdout (JSON) separately from stderr (result line).
    - **Rendering**: markdown table with columns
      `Action · WBS · Linear ID · Title · Changed fields` for the diff;
      warnings as a sub-bullet list; result line at the top per existing
      skill convention.
    - **Error handling**: how to render `cycle_detected` (with clickable
      Linear issue links from the trace), `internal`, validation errors,
      and `_LinearSync` schema errors (with recovery steps).
  - Update the "Things this skill does NOT do" section: replace the
    blanket "Linear" exclusion with: "Linear: pull-only in Phase 1
    (workbook updates from Linear); push/sync in later phases."
- **Verify:**
  - Mental dry-run on T6 probe project: do playbook steps produce a
    payload that the CLI accepts?
  - Read-through review against captured MCP fixtures
- **Files:** `skills/gantt/SKILL.md`

### T8 — README + live final acceptance

- **Acceptance:**
  - New `### Linear integration (Phase 1, pull-only)` subsection under
    `## Usage` in `README.md`:
    - One-paragraph description of what works today
    - Example prompt: *"/gantt pull the X Linear project into a gantt
      chart called TPM90"*
    - Dry-run-first convention explained
    - MCP requirement explained (`claude_ai_Linear` MCP installed and
      authenticated in Claude Code)
    - Notes that Phase 1 doesn't write to Linear at all
    - Notes about the hidden `_LinearSync` tab (what it's for, do not edit)
    - Link to spec, plan, tasks docs
  - Update project layout in README to include `gantt_lib/cp/` and
    `gantt_lib/linear/` packages
  - Update test count in Development section
  - **Live final smoke test (user-driven):**
    1. User picks a Linear project (T6 probe project works)
    2. User issues: `/gantt pull the <project> Linear project into a
       gantt chart called <program>`
    3. Agent runs MCP fetch sequence → normalizes → invokes
       `gantt linear-pull --stdin --as <program> --dry-run`
    4. Agent renders diff table, asks for confirmation
    5. On yes: agent runs again without `--dry-run`
    6. User opens the workbook, confirms program tab exists with right
       tasks, `_LinearSync` tab exists and hidden
    7. User confirms each task name cell is a clickable hyperlink that
       opens the corresponding Linear issue in a new tab
    8. User runs `/gantt critical path on <program>` — works as expected
    9. User runs `/gantt mark task <wbs-id> in <program> as done` — works
    10. User re-runs pull — expect "0 added, 0 updated, N unchanged",
        hyperlinks still present (formulas not clobbered on no-change re-pull)
- **Verify:** all 9 acceptance steps pass; any issue filed in `docs/issues/`
- **Files:** `README.md`

---

## Final acceptance gate

After T8:
1. Live pull against user's real Linear project succeeds
2. Dry-run-first behavior visible in the agent's response
3. Diff table renders cleanly
4. After apply, all existing gantt verbs (`critical-path`, `recalc`,
   `task update`, `shift`, `deck`, `baseline create`) work on the new
   program identically to a workbook-native one
5. Re-pull is a no-op
6. Manually editing a workbook-only field (e.g., `--percent 50` or
   adding `+3` lag to a predecessor) and re-pulling preserves the edit
7. `_LinearSync` tab exists, hidden, with the DO-NOT-EDIT warning row
8. No Linear writes happened (verify via Linear UI history)
9. Result line `gantt: linear-pull <program> — A added, U updated, K unchanged ✓`
   surfaces correctly at top of agent response

If issues: file in `docs/issues/` and iterate.

## Execution notes

- **No subagent spawns.** Same pattern as recent phases — pure code
  work + one live MCP probe. Token budget = $0 beyond agent context.
  T6 probe makes ~8 user-visible MCP tool calls (cost negligible,
  flagged per user-feedback memory).
- **One commit per task** (T2-T5 may split into impl + tests if diffs
  get fat).
- **Backwards compat:** strictly additive. `_LinearSync` is a new tab;
  no existing tab schema changes. New CLI subcommand is additive.
  Existing verbs untouched.
- **No engine refactor.** `cascade.py` + `critical_path.py` reused
  unchanged. The new `gantt_lib/cp/` package is a contract + adapter
  layer on top, not a replacement.
- **No Linear writes** — Phase 1 is read-only against Linear. Phase 2+
  introduces writes and the associated agent-turn cost.
- **Future phases (2-3) get their own plan + tasks files** when ready.
