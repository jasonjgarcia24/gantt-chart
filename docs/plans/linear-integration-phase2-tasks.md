# Tasks: Linear Integration — Phase 2 (full bidirectional sync)

**Spec:** [linear-integration.md](../specs/linear-integration.md) (Phase 2 section)
**Plan:** [linear-integration-phase2-plan.md](linear-integration-phase2-plan.md)
**Status:** Awaiting approval to begin

Each task = one focused commit (or two where noted). Strictly sequential —
later tasks depend on earlier ones compiling and passing tests.

Phase scope: pull + push + create + archive + 3-way merge between
workbook and Linear. After Phase 2, `gantt linear-sync <program>` is
the user-facing entry point for keeping workbook and Linear in
agreement.

---

### P2-T1 — `_LinearSync` schema extension + auto-migration

- **Acceptance:**
  - `gantt_lib/linear/sync_tab.py`:
    - `SYNC_HEADERS` extended from 5 → 18 entries (cols A-R; see spec
      schema table)
    - `SyncLink` dataclass adds 13 new fields: `sidecar_predecessors`,
      `sidecar_percent`, `sidecar_notes`, `sidecar_team`,
      `snapshot_title`, `snapshot_state`, `snapshot_state_type`,
      `snapshot_assignee`, `snapshot_due_date`, `snapshot_estimate`,
      `snapshot_blockedby` (str — comma-separated linear_ids),
      `snapshot_parent`, `snapshot_milestone`. All default `""`/None.
    - `_link_to_row` and `_row_to_link` round-trip the new columns
    - `ensure_sync_tab` writes the 18-column header on tab creation
    - `migrate_sync_tab(ss) -> int` — detects Phase-1 5-column tabs,
      expands header + data rows to 18 columns (blank-fills new cols),
      idempotent (no-op if already 18 cols). Returns count of rows
      migrated (0 if already-v2 or absent).
  - The DO-NOT-EDIT warning row stays in row 2.
- **Verify:**
  - `pytest tests/test_linear_sync_tab.py -q` — all existing tests
    still pass (extended dataclass equality still works for old test
    SyncLink constructions with default new fields).
  - `pytest tests/test_linear_sync_tab_migration.py -q` (new file):
    - bootstrap a Phase-1-shape FakeWorksheet → run migration →
      verify header is 18 cols + data rows preserved + new cols blank
    - bootstrap a Phase-2-shape tab → run migration → verify no-op
    - bootstrap an empty workbook (no tab) → run migration → returns
      0 + tab is NOT created (migration is upgrade-only, not bootstrap)
- **Files:** `skills/gantt/scripts/gantt_lib/linear/sync_tab.py`,
  `tests/test_linear_sync_tab.py` (light edits for new fields),
  `tests/test_linear_sync_tab_migration.py` (new)

### P2-T2 — `gantt_lib/linear/snapshot.py` + tests

- **Acceptance:**
  - New module with pure-logic helpers:
    - `@dataclass IssueSnapshot` matching the snapshot subset of SyncLink columns
    - `build_snapshot_from_linear(linear_issue: dict, blockedBy: list[str]) -> IssueSnapshot`
      — converts a normalized Linear-MCP issue payload into a snapshot
    - `snapshot_to_sync_fields(snapshot: IssueSnapshot) -> dict[str, str]`
      — converts to the col J-R subset of SyncLink kwargs
    - `sync_fields_to_snapshot(link: SyncLink) -> IssueSnapshot`
      — inverse
    - Equality helpers: `assignees_equal(a, b)`, `due_dates_equal(a, b)`,
      `blockedby_equal(a, b)` (set comparison ignoring order), `titles_equal`,
      `estimates_equal` (handles None vs 0 vs `{value: 0}` Linear shape)
- **Verify:**
  - `pytest tests/test_linear_snapshot.py -q` — all green
  - Covered: assignee None vs "" (equal), dueDate timezone normalization,
    blockedBy ordering-independence, snapshot↔SyncLink round-trip
- **Files:** `skills/gantt/scripts/gantt_lib/linear/snapshot.py`,
  `tests/test_linear_snapshot.py`

### P2-T3 — `gantt_lib/linear/merge.py` + tests (8 fixtures)

- **Acceptance:**
  - `gantt_lib/linear/merge.py`:
    - `FieldClassification` enum: `NO_OP | PUSH | PULL | CONVERGED | CONFLICT`
    - `classify_field(workbook_value, snapshot_value, linear_value, *, equality_fn=operator.eq) -> FieldClassification`
      — the 3-way merge table from the spec
    - `resolve_conflict(field_name: str, workbook_value, linear_value) -> tuple[Any, str]`
      — applies the per-field default policy; returns `(resolved_value, "linear"|"workbook")`
    - `@dataclass SyncRowDiff` — per-issue diff: linear_id, wbs_id,
      action (`update_push` | `update_pull` | `create` | `archive` | `unchanged`),
      field_changes: list of `(field_name, w_val, l_val, classification, resolved_to)`,
      conflicts_requiring_user_attention: list[str] (field names)
    - `compute_sync_diff(*, workbook_tasks, existing_links, linear_issues, linear_blockers, milestones) -> SyncDiff`
      — top-level orchestrator producing one `SyncRowDiff` per touched row
- **Verify:**
  - `pytest tests/test_linear_merge.py -q` — all green
  - 8 fixture pairs in `tests/fixtures/sync/` cover:
    - `no_changes.json` — everything unchanged
    - `workbook_edits_only.json` — push direction only
    - `linear_edits_only.json` — pull direction only
    - `converged_independent.json` — both sides changed but to the same value
    - `true_conflict_title.json` — both sides changed title to different values; Linear wins per default
    - `true_conflict_state.json` — both changed state; Linear wins
    - `new_workbook_rows.json` — workbook has rows with no linear_id → action=create
    - `archive_deletions.json` — `_LinearSync` has rows not in workbook → action=archive
  - Classification table exhaustively tested via direct `classify_field`
    calls covering all 9 cells of the 3×3 table
- **Files:** `skills/gantt/scripts/gantt_lib/linear/merge.py`,
  `tests/test_linear_merge.py`, `tests/fixtures/sync/*.json`

### P2-T4 — `gantt_lib/linear/push.py` + tests

- **Acceptance:**
  - `gantt_lib/linear/push.py`:
    - `@dataclass MCPRequest` — descriptor of one Linear MCP call:
      tool name (`save_issue`, `save_milestone`), kwargs (issue id /
      team / project / fields), post-call instructions for the
      CLI to apply once the agent reports back (which SyncLink row
      to update, which snapshot fields to refresh, whether to rewrite
      the workbook name cell with the new linear_url)
    - `build_push_requests(diff: SyncDiff, linear_team: str, linear_project: str) -> list[MCPRequest]`
      — main entry. Two-pass build:
      - Pass 1: emit `save_issue` for each `action=update_push` (with
        per-field updates per the diff), and for each `action=create`
        (without blockedBy)
      - Pass 2: emit `save_issue` for blockedBy reconciliation —
        recompute add/remove sets after all new linear_ids are known
        (uses placeholder linear_ids `__NEW_<wbs>__` that the agent
        substitutes after pass-1 returns)
      - For `action=archive`: emit `save_issue` with `state` set to
        first canceled-type state from the team
    - `build_pull_writes(diff: SyncDiff, ws) -> list[Callable]`
      — returns no-arg callables that apply pull-direction changes to
      the workbook tab (existing `update_task_data` etc.)
- **Verify:**
  - `pytest tests/test_linear_push.py -q` — all green
  - Covered:
    - update_push produces one save_issue per changed issue with only
      the changed fields
    - create produces save_issue without blockedBy in pass 1
    - blockedBy reconciliation pass 2: for new-task-blocks-existing-task,
      the existing task gets blockedBy updated with the new task's
      placeholder id
    - archive uses the team's first canceled-type state
    - pull_writes call `update_task_data` with the right (Task, row) args
    - No requests emitted for `action=unchanged` rows
- **Files:** `skills/gantt/scripts/gantt_lib/linear/push.py`,
  `tests/test_linear_push.py`

### P2-T5 — `gantt_lib/linear/sync.py` + tests (top-level orchestrator)

- **Acceptance:**
  - `gantt_lib/linear/sync.py`:
    - `@dataclass SyncResult` — top-level outcome (counts per action,
      list of SyncRowDiff for rendering, list of MCPRequest descriptors
      for agent execution, list of pull_writes already applied if not
      dry_run, warnings, conflicts_resolved)
    - `sync(ss, payload: CpInput, program: str, *, dry_run: bool, direction: str, force: bool) -> SyncResult`
      — top-level orchestrator:
      - Run `migrate_sync_tab(ss)` (idempotent)
      - Read existing workbook tasks + sync links
      - Build snapshots from current payload
      - Call `compute_sync_diff(...)` to classify everything
      - Filter the diff per `--direction`:
        - `pull`: only `update_pull` entries (drop push/create/archive)
        - `push`: only `update_push` + `create` + `archive` entries
        - `both` (default): apply all
      - If not dry_run:
        - Apply pull writes immediately (workbook side)
        - Emit MCP requests for agent execution (push side)
        - Snapshot refresh on each row happens via the post-MCP-call
          instructions in each MCPRequest
      - Return SyncResult
- **Verify:**
  - `pytest tests/test_linear_sync.py -q` — all green
  - Covered:
    - All 3 directions exercise the diff correctly
    - `dry_run=True` produces the same SyncResult counts but no
      writes happen (verified via FakeSpreadsheet record count)
    - `--direction=pull` with existing Phase-1-shape fixture matches
      Phase-1's pull behavior exactly (regression)
    - `--direction=push` with no Linear edits produces no MCPRequest
      for `update_pull` rows
    - Migration auto-runs: bootstrap Phase-1 sync tab → sync → tab
      becomes 18-col
    - `force=True` drops snapshot columns and treats every row as
      converged (next sync starts fresh)
- **Files:** `skills/gantt/scripts/gantt_lib/linear/sync.py`,
  `tests/test_linear_sync.py`

### P2-T6 — CLI subcommand wiring + handler tests

- **Acceptance:**
  - `gantt_lib/linear_cmds.py`:
    - New `cmd_linear_sync(args, ss, *, stdin/stdout/stderr injectable) -> int`
      — reads stdin JSON, parses via `cp.contracts.from_json`, calls
      `sync.sync(...)` with the parsed args, emits JSON output that
      includes the SyncResult plus the MCP TODO list for the agent
    - `cmd_linear_pull` becomes a thin wrapper that calls `cmd_linear_sync`
      with `direction=pull` (preserves Phase-1 entry-point compat)
    - Result line on stderr:
      `gantt: linear-sync <program> — N pushed, M pulled, K created, J archived, C conflicts resolved, U unchanged ✓`
      (with `DRY RUN:` prefix when dry-run)
    - Exit codes: 0 success, 1 contract validation, 2 program-tab-missing
      / cycle / unanchored / migration error, 3 internal exception
  - `gantt` script:
    - New `linear-sync` subparser: `--stdin` (req), `--as PROGRAM`
      (req), `--dry-run`, `--direction={pull,push,both}` (default
      `both`), `--force`
    - `linear-push` alias subparser that wires to `cmd_linear_sync`
      with `direction=push`
    - `linear-pull` stays wired to `cmd_linear_pull` (which now
      delegates to sync internally, but the CLI verb is unchanged)
- **Verify:**
  - `pytest tests/test_linear_cmds_sync.py -q` — all green
  - All existing `tests/test_linear_cmds.py` still pass (no Phase-1
    regression)
  - `gantt linear-sync --help` shows the right shape
  - Subprocess smoke: `cat fixtures/sync/no_changes.json | gantt linear-sync --stdin --as TEST --dry-run` exits 0 with valid JSON
- **Files:** `skills/gantt/scripts/gantt_lib/linear_cmds.py`,
  `skills/gantt/scripts/gantt`, `tests/test_linear_cmds_sync.py`

### P2-T7 — Refactor `pull.py` to delegate to `sync.py`

- **Acceptance:**
  - `gantt_lib/linear/pull.py`:
    - Public API unchanged: `pull(ss, inp, program, *, dry_run, force) -> PullResult | dict`
    - Internally: now calls `sync.sync(...)` with `direction="pull"`
      and translates the SyncResult into the legacy PullResult shape
    - All Phase-1 tests in `tests/test_linear_pull.py` pass unchanged
      (regression gate)
  - This task is a refactor with zero new behavior — if all existing
    pull tests pass, ship it.
- **Verify:**
  - `pytest tests/test_linear_pull.py -q` — all green
  - `pytest -q` (full suite) — all green
- **Files:** `skills/gantt/scripts/gantt_lib/linear/pull.py`

### P2-T8 — Linear MCP probe (write-side response shapes)

**Live MCP exploration, not pure code.** Informs T9 SKILL.md.

- **Acceptance:**
  - Uses the existing `Gantt Skill — Linear Integration Test` project +
    `_LinearSync` rows from Phase 1 / LINEAR_TEST. Need to capture
    the write-side response shapes that Phase 1's read-only probe
    didn't cover:
    - `save_issue` for a field update (e.g., flip JAS-7's state to
      In Progress and back). Capture response shape.
    - `save_issue` for blockedBy add (set blocker that wasn't there).
      Capture response shape including the updated `relations.blockedBy`.
    - `save_issue` for blockedBy remove (`removeBlockedBy`).
    - `save_issue` create (a throwaway issue named `Phase-2 probe
      test` to capture the new-issue response shape; archive immediately
      after to clean up).
    - `save_issue` archive (state → Cancelled). Verify the response
      and that `list_issues` no longer surfaces it.
  - Saves each response to `tests/fixtures/linear_mcp/save_issue_*.json`
  - Writes `docs/notes/linear-mcp-write-shapes.md` summarizing:
    - Does `save_issue` for a single field include unmodified fields
      in the response? (full or partial?)
    - What's the exact response shape after `removeBlockedBy`?
    - Does archive (`state=Cancelled`) need any other field set?
    - Any rate-limit headers or batch-size hints?
- **Verify:**
  - All write-probe fixtures saved + valid JSON
  - Notes file answers the 4 questions
  - Probe issue cleaned up (archived) so it doesn't clutter the test project
- **Files:** `tests/fixtures/linear_mcp/save_issue_*.json`,
  `docs/notes/linear-mcp-write-shapes.md`
- **Cost note:** ~6-8 MCP write calls + ~3 read calls. Negligible
  cost, flagged for transparency.

### P2-T9 — SKILL.md Linear sync playbook (replaces Phase 1 section)

- **Acceptance:**
  - Replace `## Linear MCP mode (Phase 1: pull only)` section with
    `## Linear MCP mode — sync (push + pull + create + archive)`:
    - Trigger language: extend to include push/sync prompts —
      "sync TPM90 with Linear", "push my TPM90 changes to Linear",
      "what's different between LINEAR_TEST and Linear", "push the
      new tasks I added to LINEAR_TEST"
    - MCP call sequence: the full read sequence from Phase 1, PLUS
      the new write-side calls per the MCPRequest list returned
      from the CLI
    - **MCP TODO execution loop**: how the agent walks the CLI's
      emitted MCPRequest list, executes each call, captures the
      response, and reports back to the CLI for snapshot refresh
      (via a follow-up `gantt linear-sync --apply-mcp-results --stdin`
      pattern OR by composing the post-call updates into the next
      sync — TBD per implementation)
    - Dry-run-first remains, but with a TWO-stage confirmation:
      1. Dry-run preview surfaces the full diff (pushed / pulled /
         created / archived / conflicts resolved). Auto-applies
         updates after preview (per the "no redundant apply gate"
         memory).
      2. Creates AND archives get a separate explicit confirmation
         even after dry-run preview: "Create 3 new Linear issues and
         archive 1? (y/n)" — higher stakes, different gate.
    - Rendering: markdown table with columns
      `Action · WBS · Linear ID · Title · Direction · Changed fields`
    - Conflict resolution rendering: dedicated sub-section showing
      each true conflict with the chosen winner and override prompt
    - Error handling: extend Phase-1 table with new write-side errors
      (rate limit, save_issue validation, archive on already-canceled
      issue)
- **Verify:**
  - Mental dry-run of playbook against the captured T8 fixtures
  - SKILL.md description block (YAML) extended with sync trigger phrases
- **Files:** `skills/gantt/SKILL.md`

### P2-T10 — README + live final acceptance

- **Acceptance:**
  - Replace the Phase-1 "Linear integration (Phase 1, pull-only)"
    README section with a Phase-2 "Linear integration (full
    bidirectional sync)" section covering:
    - `gantt linear-sync` is the new entry point; `linear-pull` and
      `linear-push` are aliases
    - The three `--direction` modes
    - The snapshot mechanism (sheet-side `_LinearSync` extended cols)
    - The 3-way merge + per-field conflict policy
    - Create + archive gates
    - Migration note: existing Phase-1 sync tabs auto-upgrade on first sync
    - MCP write cost note (~N writes per N changed issues)
  - Update project layout to include `snapshot.py`, `merge.py`,
    `push.py`, `sync.py`, plus the new test files + sync fixtures
  - Update test count
  - Link to phase2 spec/plan/tasks docs
  - **Live final acceptance** against `LINEAR_TEST`:
    1. Edit JAS-5's title in workbook → run sync → confirm Linear
       title updated
    2. Edit JAS-9's state in Linear → run sync → confirm workbook
       state updated
    3. Add a new workbook-only task (no linear_id) → sync → confirm
       new Linear issue created + linear_id written back + name cell
       rewrapped as HYPERLINK
    4. Delete a workbook row (a Linear-linked one) → sync → confirm
       archive prompt → on yes, Linear issue state becomes Cancelled
    5. Edit the SAME field in both sides (workbook + Linear) → sync
       → confirm conflict surfaces + default winner applied + override
       prompt offered
    6. Re-sync → expect 0 changes (idempotency check)
- **Verify:** all 6 acceptance steps pass; file issues for any that don't
- **Files:** `README.md`

---

## Final acceptance gate

After P2-T10:
1. Live sync against LINEAR_TEST works end-to-end for all 6 acceptance scenarios
2. Idempotent re-sync after any successful sync (0 changes reported)
3. Migration auto-runs on the existing Phase-1 sync tab without data loss
4. All 431 + new test count passes (~600 total tests after Phase 2)
5. Phase-1 entry points (`gantt linear-pull`) still work as documented
6. SKILL.md sync playbook successfully drives the agent's MCP TODO loop
7. No regressions in any existing gantt verb (recalc, critical-path, deck, baseline)

## Execution notes

- **One subagent invocation expected**: hubert for atomic commits
  after each task. Watson optional for code-review-and-quality pass
  before final push. Negev optional for acceptance-exploration on T10.
- **One commit per task** (T1, T2, T3 may split into impl + tests if
  diffs get fat; same TDD discipline as Phase 1).
- **Backwards compat:** Phase-1 entry points unchanged. `linear-pull`
  is the documented Phase-1 verb; under the hood it now delegates to
  the sync orchestrator. Migration is automatic.
- **MCP usage:** zero during T1-T7 (pure code). T8 probe ~10 MCP calls.
  T10 live ~10-30 MCP calls. Total ~$0.10 in API charges.
- **Future phases:** none currently planned. After Phase 2 ships, the
  Linear integration is feature-complete for the established use case.
  Possible follow-ups (not in any current plan):
  - Webhook integration (Linear → gantt push notifications)
  - Multi-PM concurrent sync hardening
  - Auto-scheduled background sync
  - Comments / attachments round-trip
