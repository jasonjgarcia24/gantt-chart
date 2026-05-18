# Plan: Linear Integration — Phase 2 (full bidirectional sync)

**Spec:** [linear-integration.md](../specs/linear-integration.md) (Phase 2 section)
**Phase scope:** Phase 2 only — pull + push + create + archive + 3-way merge
**Status:** Awaiting approval before Tasks breakdown
**Date:** 2026-05-17
**Builds on:** Phase 1 (shipped — `gantt linear-pull`, `_LinearSync` linkage tab, cp engine adapter)

## Component map

| # | Component | Type | Lines (est.) | Purpose |
|---|-----------|------|--------------|---------|
| 1 | `gantt_lib/linear/sync_tab.py` extension     | edit       | ~120 | Extend `_LinearSync` schema from 5 → 18 columns. Add sidecar fields (F-I) + snapshot fields (J-R). Add `SyncLink` dataclass fields. Add `migrate_sync_tab(ss)` that backfills new columns on first Phase-2 sync of a pre-existing tab. |
| 2 | `gantt_lib/linear/snapshot.py`               | new        | ~150 | Pure-logic snapshot construction: convert a fetched Linear issue → snapshot field dict. Convert snapshot dict → SyncLink columns and back. Field-equality helpers (dueDate normalization, blockedBy set comparison, assignee email normalization). |
| 3 | `gantt_lib/linear/merge.py`                  | new        | ~250 | 3-way merge engine. `classify_field(W, S, L) -> str` returning `no_op` / `push` / `pull` / `converged` / `conflict`. `resolve_conflict(field, W, L) -> tuple[value, source]` per the default-policy table. `compute_sync_diff(workbook_tasks, sync_links, linear_payload) -> SyncDiff` top-level. |
| 4 | `gantt_lib/linear/push.py`                   | new        | ~280 | Push orchestrator: walks SyncDiff's push-direction entries, builds Linear-MCP-request descriptors (the agent executes the actual MCP calls), reconciles blockedBy in a second pass after creates. Emits `MCPRequest` records (intended call + params + post-call state update) for the agent to execute and report back. |
| 5 | `gantt_lib/linear/sync.py`                   | new        | ~200 | Top-level sync orchestrator. Wires pull → merge → split into pull-side updates (apply directly to sheet) + push-side updates (emit MCP descriptors) + create-list + archive-list. Honors `--direction` flag. |
| 6 | `gantt_lib/linear_cmds.py` extension         | edit       | ~130 | Add `cmd_linear_sync` handler. Extend `cmd_linear_pull` to delegate to sync orchestrator with `direction=pull`. Add `cmd_linear_push` thin alias. Emit JSON output that includes the agent's MCP TODO list (descriptors). |
| 7 | `gantt` script: argparse + dispatch          | small edit | ~50  | Add `linear-sync` subparser (`--stdin`, `--as`, `--dry-run`, `--direction={pull,push,both}`, `--force`). Add `linear-push` alias. Both route to the same `cmd_linear_sync` with the appropriate direction. |
| 8 | `gantt_lib/linear/pull.py` extension         | edit       | ~80  | Hoist diff/apply logic to compose with merge.py's bidirectional output. Pull becomes a `direction=pull` invocation of the unified sync flow rather than a standalone path. Keep public API stable for SKILL.md. |
| 9 | `tests/fixtures/sync/*.json`                 | new        | ~400 | 8 fixture pairs (input + expected SyncDiff): no-changes, workbook-edits-only, linear-edits-only, converged-independent, true-conflict-title, true-conflict-state, new-workbook-rows, archive-deletions. |
| 10 | `tests/test_linear_snapshot.py`             | new        | ~120 | Snapshot construction + equality helpers. Edge cases: assignee None vs "", dueDate timezones, blockedBy ordering. |
| 11 | `tests/test_linear_merge.py`                | new        | ~280 | 3-way merge logic. Per-field classification table verified exhaustively. Conflict resolution applies the default policy. Tests use direct `classify_field()` + `resolve_conflict()` calls plus the higher-level `compute_sync_diff()` against the 8 fixtures. |
| 12 | `tests/test_linear_push.py`                 | new        | ~250 | Push orchestrator: SyncDiff → MCPRequest descriptors. New-issue creation path. Archive path. blockedBy reconciliation pass after creates (ensures new issue IDs are resolved before being referenced). |
| 13 | `tests/test_linear_sync.py`                 | new        | ~280 | Top-level sync orchestrator end-to-end via FakeSpreadsheet. `--direction=pull` matches Phase-1 behavior (regression). `--direction=push` skips pull-side writes. `--direction=both` applies both. `--dry-run` produces the same diff with no writes. Migration path: pre-Phase-2 sync tab gets extended on first invocation. |
| 14 | `tests/test_linear_sync_tab_migration.py`   | new        | ~80  | Migration test: bootstrap a Phase-1-shape `_LinearSync` tab (5 cols), run migration, verify schema is now 18 cols + existing rows preserved + new columns blank. |
| 15 | `tests/test_linear_cmds_sync.py`            | new        | ~150 | Handler tests for `cmd_linear_sync`: stdin parsing, exit codes, result-line shape, JSON output structure (includes the MCP TODO list for the agent). |
| 16 | `skills/gantt/SKILL.md` extension            | edit       | ~220 | New section `## Linear MCP mode — sync (push + pull + create + archive)`. Replace/expand the Phase-1 pull-only section. New: how the agent executes the MCP TODO list returned by the CLI; how to render the bidirectional diff (split into push / pull / create / archive / conflict columns); confirmation gates for creates + archives; cost note (sync turn count = ~5 reads + N_changed writes). |
| 17 | `README.md` extension                        | edit       | ~110 | Replace the Phase-1 pull-only Linear section with a Phase-2 sync overview. Document the three `--direction` modes, the snapshot mechanism, the conflict policy, the create/archive gates. Update project layout + test count. Link to phase2 plan/tasks docs. |

**Net estimate:** ~3300 LOC across 17 deliverables. Bigger than Phase 1
(~2500) because of the snapshot machinery, 3-way merge logic, and the
two-pass create-then-reconcile-blockedBy flow.

## Implementation order

```
1. sync_tab.py extension + migration test
       │   (sheet schema first — everything else depends on the column layout)
       ▼
2. snapshot.py + tests
       │   (pure logic — convert Linear issue ↔ snapshot dict)
       ▼
3. merge.py + tests
       │   (pure logic — 3-way diff against 8 fixtures; uses snapshot.py for equality)
       ▼
4. push.py + tests
       │   (pure logic — SyncDiff → MCP request descriptors; create/archive paths)
       ▼
5. sync.py + tests
       │   (composes merge + push + the existing pull; honors --direction)
       ▼
6. linear_cmds.py + script wiring + handler tests
       │   (CLI shape; cmd_linear_sync delegates to sync.py)
       ▼
7. pull.py refactor: hoist into sync.py
       │   (keeps Phase 1 entry point working; passes all existing pull tests)
       ▼
8. Linear MCP probe — capture write-side response shapes
       │   (save_issue return shape for create; archive shape; blockedBy update shape;
       │    informs the SKILL.md MCP TODO format)
       ▼
9. SKILL.md sync playbook
       │   (informed by the probe; covers the MCP TODO execution loop)
       ▼
10. README + live final acceptance against the Linear test project
```

Steps 1-7 are pure code; can land back-to-back without any live MCP
involvement. Step 8 is one-shot exploratory (uses the same Linear test
project from Phase 1). Step 9 depends on 8's findings. Step 10 is the
acceptance gate.

## Risks and mitigations

### High

**R1. MCP write turn cost.** Phase 1 had zero Linear writes; Phase 2
introduces them. The `claude_ai_Linear` MCP exposes no batched
mutation primitive — every `save_issue`, `save_milestone`, etc. is
one MCP call. Naive worst case (every issue changed): N MCP calls
per sync.

The real cost dimension that matters is **Claude-turn count** (each
turn pays prompt-cache cost + latency). Mitigations bring that down
materially:

1. **Intra-issue collapsing (free).** `save_issue` accepts all field
   updates for one issue in a single call: state + assignee + dueDate
   + estimate + `blockedBy` array + `removeBlockedBy` + label list,
   all at once. The cost model is **1 MCP call per changed issue,
   not per changed field** — already assumed by the push orchestrator.

2. **Parallel dispatch in one Claude turn (primary).** The agent
   issues up to ~10-20 `save_issue` calls in parallel from a single
   turn (proven pattern from Phase 1 — fired 3 `get_issue` + 1
   `list_milestones` in one turn during the probe). The CLI's
   emitted MCP TODO list is *grouped into independent batches*; the
   agent dispatches one batch per turn. Claude-turn count drops from
   O(N) to ≈ **O(N / 20)**. For a 100-issue full-update sync: ~5-10
   turns total, not 100.

3. **Two-pass sequencing only where needed.** The create flow is
   inherently sequential between passes (pass 1 = create new issues
   without `blockedBy`; pass 2 = patch `blockedBy` once new IDs are
   known). Each pass can still parallel-dispatch internally. Pure
   updates and archives are fully parallel and need no pass barrier.

4. **Per-row snapshot refresh, not batch.** The CLI updates
   `_LinearSync.snapshot_*` columns and `last_synced` per row as
   each MCP call returns successfully — not in one batch at the end.
   Partial failures leave already-synced rows fully synced and
   not-yet-synced rows pristine; re-running sync resumes from where
   the last left off.

5. **Cost preview in dry-run.** Surface to the user: *"This sync
   will dispatch 23 MCP write calls (12 updates, 8 creates, 3
   archives) across ~2 agent turns. Proceed?"* — the user sees both
   the MCP count and the projected turn count before committing.

For very large syncs (>100 changes), the user can opt in to a
`--direction=push` only run followed by a separate
`--direction=pull` run, so each session stays bounded. But with
parallel dispatch the inflection point shifts well past where most
real PM-scale projects operate.

**R2. Partial-failure recovery.** If the agent's MCP write loop fails
midway (rate limit, network, Linear API error), `_LinearSync` may end
up partially updated — some rows have fresh snapshots, others don't.
Re-running sync should be safe; need to verify idempotency.

Mitigation:
- The CLI updates `_LinearSync.last_synced` AND `_LinearSync.snapshot_*`
  columns **per row after each successful MCP write**, not in a single
  batch at the end. A partial sync leaves successful rows fully
  synced and unsuccessful ones in their pre-sync state.
- The agent reports the outcome of each MCP call back to the CLI;
  the CLI knows what to flag as "still needs to be retried."
- Re-running sync after a partial failure picks up where the last
  one left off because the still-unsynced rows show as "changed
  since snapshot" again.

**R3. blockedBy reconciliation for new-issue creates.** If workbook
has two new tasks A and B where B blocks A, both have empty
`linear_id`. We need to create A and B first (in either order), then
update one or both with the freshly-assigned blockedBy linear_id.
That's a two-pass flow with dependency between the passes.

Mitigation:
- Pass 1: `save_issue` for each new task, without `blockedBy`.
  Receive new linear_ids.
- Pass 2: for any new tasks that reference other (also-new) tasks
  as predecessors, `save_issue` with the now-known `blockedBy`.
- Same flow handles new tasks blocking existing tasks (the
  existing tasks' `blockedBy` arrays get amended in pass 2).
- Document the two-pass cost: each new task adds 1 or 2 writes
  depending on whether it has new-task predecessors.

### Medium

**R4. Schema migration on first Phase-2 sync.** Existing Phase-1
`_LinearSync` tabs (LINEAR_TEST + any future Phase-1 installs) have
5 columns. Phase 2 needs 18. The migration must:
- Preserve existing row identity (program / wbs_id / linear_id)
- Backfill the new sidecar columns from current workbook state (the
  user's edits since Phase 1)
- Leave the new snapshot columns blank — they'll populate on the
  first sync's pull pass

Mitigation:
- `migrate_sync_tab(ss)` is called automatically by the sync
  orchestrator before any read of `_LinearSync` if the header row
  shows the Phase-1 column count.
- Migration is idempotent: if already at 18 cols, no-op.
- Test: `test_linear_sync_tab_migration.py` covers the upgrade
  path explicitly.
- Document in SKILL.md: first sync after Phase 2 ships will include
  a brief "migrating sync tab to v2 schema" note.

**R5. Sidecar column drift.** Users can manually edit the sync tab
(it's hidden but accessible via "Show hidden tabs"). If someone
hand-edits `sidecar_percent` or a snapshot column, the next sync's
3-way merge becomes wrong.

Mitigation:
- The DO-NOT-EDIT warning row already exists from Phase 1.
- Sync validates the header row on every read; if columns are renamed
  or reordered, refuse with a clear error.
- For accidentally-edited data cells, sync can't detect — the user
  effectively asserts that the new value is correct. Document
  explicitly that editing the sync tab manually breaks the sync
  contract; recovery is `gantt linear-sync --force` (treat all rows
  as fresh on next sync).

**R6. Linear-MCP `save_issue` semantics for blockedBy.** The MCP
schema documents `blockedBy` as "append-only; existing relations are
never removed" and exposes a `removeBlockedBy` array. Push must
diff workbook's blockedBy set against Linear's, then issue both
adds AND removes per changed issue.

Mitigation:
- Push orchestrator computes `blockedBy_to_add` and
  `blockedBy_to_remove` per issue based on snapshot + workbook
  versus current Linear.
- Test exhaustively for blockedBy churn (add only / remove only /
  both / no-op).

**R7. New-issue title hyperlink.** A new Linear issue created from
the push has no `linear_url` until after `save_issue` returns. The
workbook row's name cell needs a second-pass rewrite to wrap as
HYPERLINK once the URL is known.

Mitigation:
- Push's pass-2 also rewrites name cells for newly-created issues:
  `update_task_data(ws, row, task_with_linear_url)`.
- Test: `test_linear_push.py` verifies a new-issue diff produces
  the rewrite call.

### Low

**R8. Snapshot column width.** 13 snapshot/sidecar columns on top of
5 identity columns = 18 columns. Gspread + Sheets handle this fine;
the visible width is irrelevant since the tab is hidden.

**R9. Cancelled state mapping.** Linear's "Cancelled" status-type is
the closest to "archive intent." Some teams may have custom canceled
states (test project has both `Canceled` and `Duplicate` of type
`canceled`). Push to archive should use the first state of type
`canceled` it finds, or let the user configure.

Mitigation:
- Default to the first `canceled`-type state from the team's
  `list_issue_statuses` response.
- Phase 2+ config block in `~/.config/gantt/config.json`:
  `linear.archive_state` for per-team override.

**R10. Concurrent edits during sync.** If the workbook user is
editing in Google Sheets while a sync runs, the workbook reads might
catch mid-edit state. Phase 1 had the same risk but pull-only meant
the cost was just "stale view." Phase 2 pushes back, so a mid-edit
read could result in pushing a half-edited state to Linear.

Mitigation:
- Document: don't sync while actively editing the workbook.
- Sync prints the workbook fetch timestamp in the dry-run preview
  so the user knows the snapshot's freshness.
- Future hardening (out of scope): Sheets API has a `revision_id`;
  could compare before/after sync and abort if changed.

## Verification checkpoints

| After step | Verification | Pass criterion |
|---|---|---|
| 1 (schema + migration)   | `pytest tests/test_linear_sync_tab*.py -q`       | Both files green; migration backfills correctly |
| 2 (snapshot)             | `pytest tests/test_linear_snapshot.py -q`        | Equality helpers handle assignee/date/blockedBy edge cases |
| 3 (merge)                | `pytest tests/test_linear_merge.py -q`           | 8 fixture pairs round-trip; classification table exhaustive |
| 4 (push)                 | `pytest tests/test_linear_push.py -q`            | SyncDiff → MCPRequest correctness; two-pass blockedBy reconciliation |
| 5 (sync)                 | `pytest tests/test_linear_sync.py -q`            | All 3 directions; dry-run; migration auto-runs |
| 6 (cmds + CLI)           | Subprocess smoke + `pytest tests/test_linear_cmds_sync.py -q`  | Exit codes + result line + JSON output structure |
| 7 (pull refactor)        | All Phase-1 `test_linear_pull.py` tests still green | No regression in Phase-1 behavior |
| 8 (MCP probe write-side) | Captured fixtures + notes doc                    | Confirms create/archive/blockedBy-update shapes |
| 9 (SKILL.md)             | Mental dry-run against probe fixtures            | Agent can execute the CLI's MCP TODO list cleanly |
| 10 (live)                | End-to-end against `LINEAR_TEST` project: edit + sync + verify | Workbook edits propagate to Linear; Linear edits propagate to workbook; both at once trigger conflict resolution |

**Final acceptance gate (user-driven):**

Drive the live test project through a real bidirectional cycle:
1. Edit a few fields in the workbook (rename JAS-5, change JAS-7 estimate, mark JAS-8 done)
2. Edit a few fields in Linear (rename JAS-9, change JAS-6 state to In Progress, change JAS-7 assignee)
3. Add 1 workbook-only task (no linear_id)
4. Delete 1 workbook row (a Linear-linked one)
5. Run `/gantt sync LINEAR_TEST --dry-run` → confirm the diff is sensible
6. Apply → verify per-direction changes landed correctly in both Linear UI and workbook
7. Re-sync → expect 0 changes (idempotency)

If any failure mode surfaces (partial sync, blockedBy reconciliation bug,
hyperlink not rewritten for new issue, etc.), file in `docs/issues/` and iterate.

## What's NOT in scope (Phase 2)

- Multi-team projects (single team per project, like Phase 1)
- Linear Cycles, comments, attachments, sub-issue reordering within a parent
- Auto-scheduled / background sync (CLI invocation only)
- Multi-PM concurrent sync of the same program
- Slack / Linear comment notifications when sync runs
- Webhook integration (Linear → gantt push notifications)
- A web UI for conflict resolution (terminal/agent prompts only)

## Decisions needed before Tasks breakdown

None blocking. Three micro-decisions I'm making in the Tasks phase
without re-asking — flag if any are wrong:

1. **`--direction` flag default is `both`.** `gantt linear-sync TPM90`
   without flags runs a full bidirectional sync. The Phase-1
   `linear-pull` entry point stays as an alias for
   `linear-sync --direction=pull` so existing playbooks don't break.
2. **Confirmation gates: dry-run + per-category (creates and archives
   only).** Updates apply silently after the dry-run confirmation,
   matching the Phase-1 "no redundant apply gate" feedback. Creates
   and archives get a separate confirmation because they're
   higher-stakes (a wrong create makes noise in Linear that's hard
   to clean up; a wrong archive destroys context for the team).
3. **Archive uses `state="Cancelled"` (or first canceled-type state).**
   Not Linear's `archivedAt` field — Linear archives are reversible
   only via the UI, and we want the sync to be UI-replayable. Cancel
   states are the closest semantic match and round-trip-friendly.

## Cost flag

The Phase 2 implementation has zero MCP usage during code work —
all pure-Python until the T8 probe (~6 MCP calls) and T10 live
acceptance (~10-30 MCP calls depending on the test scenario). Total
MCP cost across the phase: ~$0.10 in API charges, well within
tolerance. Flagging per the user-feedback memory because Phase 2
introduces MCP writes for the first time.
