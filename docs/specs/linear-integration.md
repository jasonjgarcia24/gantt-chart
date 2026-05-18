# Spec: Linear Integration

**Status:** Draft, awaiting user approval
**Date:** 2026-05-16
**Builds on:** all of v0.5 (workbook + cascade + critical path + decks)
**Phases:** 1 (pull Linear → workbook tab, **shipped**) → 2 (full bidirectional sync: pull + push + create + archive, with 3-way merge)

## Objective

Make the gantt skill operate against **Linear** as a first-class data source
that flows into the existing Google Sheets workbook. After a Linear project
is pulled, every existing gantt verb — `recalc`, `shift`, `critical-path`,
`baseline`, `deck`, etc. — works on it identically to a workbook-native
program. The workbook stays the analysis and rendering surface; Linear
becomes a source-of-truth feeder.

Primary user value: **see your Linear team's plan as a gantt chart with
real cascade math, critical path, baselines, and decks — without copying
data by hand.**

Strategic direction (user-stated): prefer MCP servers over direct API
integrations where MCP coverage exists. The Linear MCP is already wired
up in this Claude Code install.

### Success criteria (Phase 1)

This phase is done when **all** of the following hold:

1. `gantt linear-pull <project-ref> --as <program>` reads a Linear project
   via the MCP and creates a program tab `P_<program>` whose tasks
   correspond to Linear issues (with parent/sub-issue hierarchy preserved
   as WBS hierarchy).
2. After the pull, `gantt critical-path <program>`, `gantt recalc <program>`,
   `gantt deck --program <program>`, and `gantt baseline create <program>`
   all work on the pulled data identically to a workbook-native program.
3. The Linear→workbook linkage is persisted in a hidden `_LinearSync`
   tab (one row per linked task: `program, wbs_id, linear_id, last_synced`),
   created on first pull. **No program tab schema change is required.**
4. Re-pulling the same Linear project upserts: matches existing rows by
   Linear ID, updates Linear-sourced fields, preserves workbook-only
   fields per the conflict policy below. New Linear issues are appended;
   workbook-only rows (no Linear ID in `_LinearSync`) are untouched.
5. `--dry-run` mode prints the proposed changes (add/update/keep counts +
   per-field diff for updates) without writing.
6. Linear issues with missing estimates degrade gracefully — a
   configurable fallback (default: 1 working day) is applied, and the
   pull output surfaces a warning per affected issue.
7. Skill activation language is updated so prompts like *"pull the
   TPM-Eyepiece Linear project into a gantt chart"* trigger this flow.

Phases 2 and 3 have their own success criteria; not blocking for Phase 1
ship.

## Architecture

**Arch B**: pure-math CLI + agent-orchestrated MCP. The CLI has no Linear
access; the agent has no Sheets access. The Linear→workbook flow:

```
┌───────────────────────────────────────────────────────────────────┐
│ User: "pull the TPM-Eyepiece Linear project into a gantt chart    │
│        called TPM90"                                               │
└───────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌───────────────────────────────────────────────────────────────────┐
│ Agent (driven by SKILL.md)                                         │
│   1. Linear MCP → list_teams + list_projects + get_project         │
│                 → list_issues + (get_issue ×N if needed)           │
│                 → list_milestones                                  │
│   2. Normalize into the linear-pull JSON contract                  │
│   3. Bash → gantt linear-pull --stdin --as TPM90 [--dry-run]       │
│   4. Render result line + summary table to user                    │
└───────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌───────────────────────────────────────────────────────────────────┐
│ CLI: gantt linear-pull --stdin --as <program>                      │
│   reads Linear payload JSON on stdin                               │
│   ─ runs cp/adapter to build Tasks + cascade them                  │
│   ─ reconciles against existing program tab (if any) via           │
│     _LinearSync hidden tab                                         │
│   ─ writes program tab via existing Sheets I/O                     │
│   ─ updates _LinearSync rows (program, wbs_id, linear_id, ts)      │
│   ─ emits result line + JSON summary on stdout                     │
└───────────────────────────────────────────────────────────────────┘
```

### Why this seam

- **Linear access lives in exactly one place** (the agent + MCP), Sheets
  access lives in exactly one place (the CLI). Neither knows about the
  other.
- **The cp engine is reusable.** A new internal library
  `gantt_lib/cp/` (contracts + adapter) translates the Linear JSON
  payload into the existing `Program` model and back. The existing
  `cascade.py` and `critical_path.py` modules are reused unchanged.
- **No new auth surface.** Reuses the already-installed Linear MCP.
- **Reversible.** A future Arch A entry point (`gantt linear-pull
  <project>` calling Linear's GraphQL directly) would add as a parallel
  front door calling the same `linear/pull.py` orchestrator. No
  rewrite.

### Known limitations of Arch B (Phase 1)

- Headless / cron / non-Claude-Code runs of `linear-pull` are off the
  table — the agent (with MCP loaded) must be in the loop.
- Phase 1 reads from Linear but does not write back. So the "agent
  burns N MCP turns per pull" cost that Arch B was flagged for is
  **zero in Phase 1** — only the read-side fetches happen. Phase 2
  (push) is where write-side turn cost becomes real; addressed there.
- Test coverage of the agent+MCP layer is fuzzier than mocking a Python
  GraphQL transport. Mitigated by keeping the agent layer thin and
  pushing logic into the CLI.

## CLI surface (Phase 1)

### `gantt linear-pull --stdin --as <program> [--dry-run]`

Read a normalized Linear project payload as JSON on stdin; create or
upsert program tab `P_<program>`; update the `_LinearSync` tab; emit a
result line + JSON summary.

**Flags:**
- `--stdin` (required) — read JSON payload from stdin
- `--as <program>` (required) — target program tab name
- `--dry-run` (optional) — compute the diff, print it, do not write

**Input contract (stdin JSON):**

```json
{
  "project": {
    "name": "TPM-Eyepiece",
    "source": "linear",
    "source_ref": "https://linear.app/acme/project/tpm-eyepiece-..."
  },
  "config": {
    "default_duration_days": 1,
    "estimate_to_days": {"kind": "points_to_days", "ratio": 1.0},
    "today": "2026-05-16"
  },
  "issues": [
    {
      "linear_id": "TPM-42",
      "title": "Spec eyepiece optics",
      "state": "In Progress",
      "estimate_days": 5,
      "percent": 30,
      "assignee": "alex@example.com",
      "start_anchor": null,
      "end_anchor": null,
      "is_milestone": false,
      "parent_linear_id": null
    }
  ],
  "edges": [
    {"from_linear_id": "TPM-42", "to_linear_id": "TPM-47", "type": "FS", "lag_days": 0}
  ]
}
```

**Output contract (stdout JSON):**

```json
{
  "ok": true,
  "program": "TPM90",
  "applied_at": "2026-05-16T17:42:00Z",
  "dry_run": false,
  "summary": {
    "added": 12,
    "updated": 18,
    "kept_unchanged": 4,
    "workbook_only_preserved": 2
  },
  "diffs": [
    {
      "linear_id": "TPM-42",
      "wbs_id": "1",
      "action": "updated",
      "fields": {
        "state": {"from": "Todo", "to": "In Progress", "source": "linear"},
        "duration": {"from": 3, "to": 5, "source": "linear"}
      }
    }
  ],
  "warnings": [
    {"linear_id": "TPM-50", "kind": "missing_estimate", "message": "no estimate; using default 1d"}
  ]
}
```

**Result line (stderr, final line):**
`gantt: linear-pull TPM90 — 12 added, 18 updated, 4 unchanged ✓`
(or `linear-pull TPM90 — DRY RUN: 12 would add, 18 would update ✓`)

### Existing CLI: untouched in Phase 1

`gantt task add`, `gantt recalc`, `gantt critical-path`, `gantt deck`,
`gantt baseline *`, etc. continue to work exactly as today. The pull
just writes into a program tab; everything downstream reads it the same
way as a workbook-native tab.

### Internal-only library layer

The JSON-contract / cp-engine layer added in Phase 1 is **library code,
not a user-facing CLI verb**:

- `gantt_lib/cp/contracts.py` — dataclasses + JSON serde for the project
  graph input + cascade result output
- `gantt_lib/cp/adapter.py` — converts the JSON graph to the existing
  `Program` model, runs `cascade()` + `compute_slack()`, translates back
  to JSON
- `gantt_lib/linear/pull.py` — orchestrator: takes the Linear payload,
  calls cp/adapter for cascade math, reconciles against the existing
  tab via `_LinearSync`, writes the Sheet

No user-facing `gantt cp --stdin` or similar subcommand. The seam exists
for testability and to keep the cascade logic reusable, not as a
user-typed command.

## Mapping: Linear → workbook

| Linear concept | Workbook concept | Notes / caveats |
|---|---|---|
| Project | Program tab `P_<program>` | One project per tab; `program` name supplied by `--as` flag |
| Project milestone | Milestone task (duration 0) | Order from Linear preserved |
| Issue | Task row | WBS id assigned by CLI on first pull, persisted in `_LinearSync`, reused on re-pull |
| Issue title + Linear URL | Task name (column C) rendered as `=HYPERLINK("<linear_url>", "<title>")` | Cell displays title text; clicking opens the Linear issue. Quotes in title are escaped per Sheets formula rules (`"` → `""`). On read, gspread returns the displayed text (just the title), so the existing `Task.from_row` path is unchanged |
| Sub-issue (`parent_linear_id`) | `--parent <wbs>` hierarchy | Recursive |
| `blockedBy` | Predecessors (FS, lag=0 default) | Linear has no relation type or lag — first pull uses FS+0; workbook user can edit to add lag/types and re-pulls preserve their edits (per conflict policy) |
| `estimate` (points) | Duration (days) | Via config; default `1 point = 1 working day`. Issues with no estimate get `default_duration_days` + a warning |
| `dueDate` | End anchor | Only set if Linear has it |
| `startedAt` | Start anchor | Used for already-in-progress issues |
| State | Status | `Done`→`Done`; `Canceled`→`Cancelled`; `In Progress`→`In Progress`; `Backlog`/`Todo`→`Not Started`. Custom workflow states fall back to `Not Started` with a warning |
| Assignee | Owner | By email |

### `_LinearSync` hidden tab (new in Phase 1)

Created on first pull if absent. One row per linked task. Schema:

| Column | Type | Notes |
|---|---|---|
| program  | str (e.g., `TPM90`) | The program tab name |
| wbs_id   | str (e.g., `1`, `1.2`) | The WBS id we assigned |
| linear_id| str (e.g., `TPM-42`) | The Linear issue identifier |
| last_synced | ISO 8601 timestamp | When this row was last touched by a pull |
| linear_url | str | Convenience: deep-link to the Linear issue |

Tab is hidden (Sheets `hidden: true`) so it doesn't clutter the UI but
remains accessible to anyone who needs to inspect the linkage.

### Conflict policy on re-pull

When the same row exists in both Linear and the workbook (matched by
`linear_id`):

| Field | Winner on conflict | Why |
|---|---|---|
| `title` (name) | **Linear** | Linear is the authoritative description |
| `state` (status) | **Linear** | Linear is where the team manages execution |
| `assignee` (owner) | **Linear** | Same |
| `dueDate` (end anchor) | **Linear** | Same |
| `start_anchor` | **Linear** if set, else workbook | Linear's `startedAt` is informational; workbook value preserved if no Linear value |
| `predecessors` (DSL) | **Workbook** | Workbook holds the FS/SS/FF/SF + lag info Linear can't express |
| `duration` | **Workbook** | The user may have refined the estimate post-pull |
| `percent_complete` | **Workbook** | Linear doesn't carry this |
| `notes` | **Workbook** | User's free-text annotations |
| `team` | **Workbook** | Linear has labels, not a per-issue team field |

The `--dry-run` output shows every conflict with both values so the user
can preview before applying. Workbook-only rows (rows in the program tab
with no `_LinearSync` entry) are never touched by a re-pull.

## Agent playbook (Phase 1)

Lives in `skills/gantt/SKILL.md`. Key additions:

1. **Trigger language**: extend the SKILL description so phrases like
   *"pull the X Linear project into a gantt chart"*, *"refresh TPM90
   from Linear"*, or *"create a gantt chart from the Linear project
   X"* activate Linear-pull mode.
2. **Source detection**: when the user names a Linear project (URL,
   team-key prefix like `TPM-`, or explicit "linear" keyword), the
   agent routes to the Linear pull path.
3. **Target tab disambiguation**: if `--as <program>` is not obvious
   from the prompt, ask the user once — never guess.
4. **MCP call sequence**:
   - `list_teams` (cache for the session)
   - `list_projects(team)` → resolve project ID
   - `get_project(projectId)` → metadata + state
   - `list_issues(projectId)` → all issues (paginate as needed)
   - For each issue with relationships (if `list_issues` returns
     blockedBy only as IDs): `get_issue(issueId)` to pull `relations`
     — verified by Phase 1's MCP probe step.
   - `list_milestones(projectId)` → milestone tasks
5. **Normalization**: agent transforms MCP responses into the
   `linear-pull --stdin` JSON contract. State name mapping,
   estimate-to-days conversion, missing-field defaulting all happen
   here.
6. **Dry-run-first default**: agent runs `--dry-run` first, surfaces the
   diff to the user, asks "Apply? (y/n)", then runs without `--dry-run`
   on confirm. Pull is a real workbook write — surfacing the diff first
   matches the existing skill convention for confirming-before-side-effect.
7. **Invocation**: `<skill-base-dir>/scripts/gantt linear-pull --stdin
   --as <program>` with JSON piped via stdin. Capture stdout (JSON
   summary) and stderr (result line).
8. **Rendering**: present the summary as a markdown table:
   `Action · WBS · Linear ID · Title · Changed fields`. Warnings under
   the table as a sub-bullet list. Result line surfaced verbatim at the
   top per the existing skill convention.

## Phased plan

### Phase 1 — Pull Linear project into a workbook tab (this spec's MVP)

Deliverables: `gantt linear-pull --stdin --as <program>` + the internal
library layer (`gantt_lib/cp/` + `gantt_lib/linear/`) + `_LinearSync`
hidden tab + SKILL.md playbook + tests. After Phase 1, every existing
gantt verb works on Linear-pulled programs.

### Phase 2 — Full bidirectional sync (pull + push + create + archive)

The single user-facing entry point: `gantt linear-sync <program>
[--dry-run] [--direction=pull|push|both]`. Default direction is `both`:
the agent pulls Linear state, computes a 3-way diff against the stored
snapshot, and applies the resolved changes to whichever side(s) need
updating. Existing `linear-pull` stays as a Phase-1-compatible
read-only entry point but becomes a thin alias for
`linear-sync --direction=pull`.

In scope:
- **Updates** to existing linked issues (already covered conceptually by
  Phase 1's pull, plus the reverse direction): name, state, assignee,
  dueDate, milestone, blockedBy graph
- **New issues**: workbook rows added since last sync (`linear_id`
  empty in `_LinearSync`) get a new Linear issue created; the assigned
  ID is written back to `_LinearSync`
- **Archives**: workbook rows deleted since last sync (`linear_id`
  present in `_LinearSync` but no matching workbook row) trigger
  archiving the Linear issue (state set to a canceled-type state +
  optionally `archivedAt` if the MCP exposes it)
- **Sidecar metadata**: workbook-only fields (signed lag, non-FS
  relations, %complete, workbook-only notes) stored sheet-side in
  extended `_LinearSync` columns — see schema below

Out of scope (Phase 2):
- Linear→workbook propagation of comments, attachments, sub-issue
  reordering within a parent, Cycle assignment
- Multi-PM concurrent sync (single-writer assumed; race detection is a
  follow-up if needed)
- Auto-scheduled background sync (manual invocation only)

### Sidecar + snapshot schema (sheet-side, in extended `_LinearSync`)

The Phase 1 `_LinearSync` tab is extended with columns to carry both
the sidecar (workbook-only fields Linear can't represent) and the
3-way-merge snapshot (last-known-Linear-state per field).

| Col | Field | Source | Purpose |
|---|---|---|---|
| A | program | (existing) | linkage |
| B | wbs_id | (existing) | linkage |
| C | linear_id | (existing) | linkage |
| D | last_synced | (existing) | ISO timestamp; updated on every successful sync touch |
| E | linear_url | (existing) | convenience deep-link |
| F | sidecar_predecessors | workbook | full predecessor DSL with relation type + signed lag (workbook-only beyond Linear's bare blockedBy) |
| G | sidecar_percent | workbook | %complete (Linear doesn't carry) |
| H | sidecar_notes | workbook | workbook Notes column (Linear doesn't carry) |
| I | sidecar_team | workbook | workbook Team column (Linear doesn't carry) |
| J | snapshot_title | linear@sync | last-known Linear title |
| K | snapshot_state | linear@sync | last-known Linear state name |
| L | snapshot_state_type | linear@sync | last-known Linear state type (one of backlog/unstarted/started/completed/canceled) |
| M | snapshot_assignee | linear@sync | last-known Linear assignee email |
| N | snapshot_due_date | linear@sync | last-known Linear dueDate (ISO) |
| O | snapshot_estimate | linear@sync | last-known Linear estimate (raw value, e.g. `5` for 5 points) |
| P | snapshot_blockedby | linear@sync | last-known Linear blockedBy as comma-separated linear_ids |
| Q | snapshot_parent | linear@sync | last-known Linear parentId |
| R | snapshot_milestone | linear@sync | last-known Linear milestone (id of project milestone, if any) |

Row layout: header row 1 + DO-NOT-EDIT warning row 2 (existing) +
data rows 3+. The extension is additive — existing Phase 1 rows
expand to the new column count on first Phase-2 sync (a one-time
migration backfills columns F-R from the next pull).

### 3-way merge — per-field conflict resolution

For every linked issue and every reconciled field, the agent (and CLI
helpers) compare three values:

- **W** = current workbook value
- **S** = stored snapshot value (`snapshot_*` column)
- **L** = current Linear value

| W vs S | L vs S | Action |
|---|---|---|
| equal | equal | no-op (no drift) |
| changed | equal | push W → Linear (workbook-side edit only) |
| equal | changed | pull L → workbook (Linear-side edit only) |
| changed | changed, W==L | converged independently; no-op, just refresh snapshot |
| changed | changed, W≠L | CONFLICT — apply per-field default policy below; for adversarial fields, surface to user with proposed resolution + override prompt |

**Per-field default policy on true conflicts** (when both sides changed
to different values):

| Field | Default winner | Reasoning |
|---|---|---|
| `title` | Linear | Linear is the authoritative description for the team |
| `state` / `state_type` | Linear | Linear is the team's execution surface |
| `assignee` | Linear | Same |
| `dueDate` | Linear | Same |
| `parent` | Linear | Structural changes happen in Linear UI |
| `milestone` | Linear | Same |
| `predecessors` / blockedBy | Workbook | Workbook holds the full DSL (relation type + signed lag) Linear can't express |
| `percent` | Workbook | Linear doesn't carry %complete |
| `notes` | Workbook | Linear doesn't carry workbook Notes |
| `team` | Workbook | Linear has labels, not a per-issue team field |
| `estimate` / `duration` | Workbook (if workbook value non-default) | The user often refines estimates post-pull; never silently overwrite |

After applying the per-field winner, the snapshot column is refreshed
to the post-merge Linear value so the next sync sees no drift.

The `--dry-run` mode surfaces every detected change with its
classification (push / pull / converged / conflict-resolved) and the
proposed action; the user reviews before applying.

### Phase 2 — Create + archive paths

**Create**: a workbook row with a `linear_id` of empty string AND no
matching row in `_LinearSync` is classified as `workbook_only`. On
sync (push direction), the agent calls `save_issue` to create a new
Linear issue with the workbook's `title`, `team` (resolved to a
Linear project — defaults to the program's bound project from
`config.linear` block; user is prompted if ambiguous), `assignee`,
`estimate`, `parentId`. The returned Linear ID is written back to
`_LinearSync` and the workbook row's name cell is rewrapped as a
HYPERLINK formula. Predecessors (`blockedBy`) are reconciled in a
second pass after all new issues have IDs.

**Archive**: a `_LinearSync` row whose `linear_id` no longer matches
any workbook row is classified as `workbook_deleted`. On sync (push
direction), the agent confirms with the user (single batched prompt
showing all deletions), then calls `save_issue` with `state` set to
the team's canceled-type state. The `_LinearSync` row stays as a
tombstone with `last_synced` set to the archive timestamp; future
syncs treat it as `archived` and skip it.

Both create and archive paths are gated behind explicit user
confirmation in the dry-run preview — never silent.

### Phase 2 — CLI surface

New subcommands:

- `gantt linear-sync <program> [--dry-run] [--direction=pull|push|both]`
  - `--dry-run`: compute the diff, surface it, do not write
  - `--direction=pull`: only apply pull-direction changes (equivalent
    to Phase 1's `linear-pull`)
  - `--direction=push`: only apply push-direction changes
  - `--direction=both` (default): apply both, with conflict resolution
  - Always pipes a normalized payload via `--stdin` like `linear-pull`
- `gantt linear-push <program>` (convenience alias for
  `linear-sync --direction=push`)
- `gantt linear-pull <program>` stays as Phase-1-compatible alias for
  `linear-sync --direction=pull`

Result line shape:
`gantt: linear-sync <program> — N pushed, M pulled, K created, J archived, C conflicts resolved, U unchanged ✓`

## Out of scope (all phases)

- Linear Cycles → workbook representation (no clean mapping)
- Linear comments / attachments / status updates round-trip
- Multi-team projects (a Linear project can span teams; we treat the
  first team as canonical)
- Linear's project status field (Planned/Started/Completed/Cancelled) —
  derivable from milestones, not separately modeled
- Sub-day granularity in cascade math
- Per-team calendars (working days remain global)
- Multi-PM concurrent sync of the same program (single-writer model
  assumed; if two PMs sync the same program simultaneously, the
  later writer's `last_synced` timestamp wins per-row — no
  cross-PM merge)
- Auto-scheduled / background sync (Phase 2 is manual via
  `gantt linear-sync`; a daemon/cron wrapper would be a separate
  follow-up)
- Showing the Linear ID inline as separate text on each row (we add a
  hyperlink on the task name instead — clicking opens the issue. A
  visible inline Linear-ID column is a Phase 2+ UX add if wanted)
- Preserving the name hyperlink across `gantt task update --name`:
  if a user renames a Linear-pulled task via the workbook CLI, the
  hyperlink wrapping is lost (plain text overwrite). Re-pull restores it.
  Acceptable because the conflict policy says Linear wins on title
  anyway — workbook-side title edits are not the intended flow.

## Open questions

These are explicit "we'll resolve at implementation time" items, not
blockers for spec approval:

1. **Linear MCP `list_issues` payload shape** — does it return
   `relations.blockedBy` inline, or only on `get_issue`? Determines
   whether the agent makes O(1) or O(N) MCP calls per project.
   Resolved by the Phase 1 probe task; SKILL.md is written against
   the captured fixture.
2. **Estimate semantics per team** — Linear teams configure estimate
   units (points, hours, exponential, t-shirt sizes, none). Phase 1
   assumes points-to-days via a config ratio surfaced in the input
   payload; the agent reads the team's estimate unit at fetch time and
   chooses the right ratio. Per-team overrides in `~/.config/gantt/config.json`
   are a Phase 2+ enhancement if a user has multiple teams with
   different units.
3. **Custom workflow states** — Linear allows custom states per team.
   Default mapping covers the standard set; custom states fall back to
   `Not Started` with a warning. User can add a mapping in
   `~/.config/gantt/config.json` (Phase 2+ formalizes the config block).

## Configuration additions

`~/.config/gantt/config.json` gains an optional `linear` block — read by
the agent at normalization time, **not** the CLI (CLI takes config inline
in the `--stdin` JSON payload):

```json
{
  "linear": {
    "default_estimate_to_days": 1.0,
    "team_overrides": {
      "TPM": {"estimate_to_days": 0.5}
    },
    "state_overrides": {
      "Code Review": "In Progress",
      "Awaiting QA": "At Risk"
    }
  }
}
```

Phase 1 reads this block if present; if absent, agent uses defaults.

## Risks

- **MCP coverage gaps.** If `claude_ai_Linear` MCP lacks a needed field
  (e.g., relation type, lag), we accept the loss for Phase 1 (FS+0
  default) and store any compensating workbook edits in the program tab
  per the conflict policy.
- **Schema drift on `_LinearSync`.** Adding new columns to the sync tab
  later (e.g., when Phase 2's sidecar arrives) is an additive change.
  Make the reader tolerant to extra/missing columns from day 1.
- **Linear pagination.** Large projects need `list_issues` pagination.
  Agent handles in the normalization step.
- **First-pull cost.** A 30-issue project = ~5-10 MCP read calls
  depending on whether `list_issues` returns relations inline (probe
  task answers this). Read-only, no writes — modest turn cost.

## Appendix A — Architecture decision: Arch A vs Arch B

**Decision:** Arch B (agent uses Linear MCP, CLI stays pure math —
extended in Phase 1 to also own Sheets writes for the pull path).
**Date:** 2026-05-16.
**Decided by:** user, with assistant recommendation.

### Options considered

**Arch A — CLI calls Linear's GraphQL API directly.**
A Python `linear` module inside the CLI authenticates with a
`LINEAR_API_KEY` stored in `~/.config/gantt/.env`. The CLI owns Linear
connectivity end to end.

**Arch B — Agent uses the Linear MCP; CLI stays pure math + Sheets I/O.**
The agent calls the `claude_ai_Linear` MCP to fetch (and, in Phase 2+,
write) Linear state. The CLI gets a normalized JSON payload on stdin
and writes to Sheets. The CLI never sees Linear.

### Trade-offs

| Dimension | Arch A | Arch B |
|---|---|---|
| Headless / cron / non-Claude-Code runs | Works | Blocked — agent + MCP must be loaded |
| Auth surface | New: `LINEAR_API_KEY` in `.env` | None new — reuses MCP install |
| Phase 2/3 sync atomicity | One process, retryable, scriptable | O(N) MCP tool calls per write-sync, agent-turn cost |
| Test seam | Mock Python GraphQL transport (clean) | Mock MCP boundary (fuzzier, but CLI testable in isolation against fixture JSON) |
| Inheritance of upstream improvements | None | Inherits MCP audit logs, rate-limit handling, schema evolution |
| Alignment with stated user direction | Counter to it | Matches it |
| Reversibility | High — can add Arch B later as a second front door | High — can add Arch A later as a second front door, *provided* the CLI's stdin/stdout contract stays library-clean |

### Why Arch B was chosen

1. **User-stated strategic direction**: "we want to make our way towards
   using MCPs instead of APIs where able." Picking Arch A here would
   set a counter-precedent for future integrations.
2. **No second auth surface to maintain or document.**
3. **Headless-run constraint doesn't bind today** — gantt is always
   driven through `/gantt` slash commands.
4. **Forces a cleaner library seam.** The `gantt_lib/cp/` contracts and
   adapter (added in Phase 1 for the Linear-pull path) become reusable
   any time we want to feed cascade math from a non-Sheets source.

### Trade-offs explicitly accepted

- Phase 2 push of a 100-issue project may take ~100 agent turns. The
  Phase 2 design must be diff-based to keep this bounded. **Phase 1 has
  zero Linear writes** — agent turn cost is read-only fetches only.
- Tests of the Linear path require mocking the agent + MCP boundary.
  Mitigated by keeping the agent layer thin and pushing logic into the
  CLI, where it's testable in isolation against fixture JSON.
- If the `claude_ai_Linear` MCP lacks a needed field, we accept the
  loss or work around in the Sheet.

### Linkage storage decision (sub-decision under Arch B)

**Decision:** store Linear↔workbook linkage in a hidden `_LinearSync`
tab, not in a new column on each program tab.

**Why:** column N in the existing program tab schema is the first day
of the 126-column timeline region. Inserting a `Linear ID` column at N
would shift the entire timeline, requiring updates to ARRAYFORMULA
ranges, header generation, weekend shading, Q/M boundary borders, and
a migration step for every existing program tab. Hidden sibling tab is
strictly additive — zero risk to existing tabs, zero migration cost.

**Trade-off accepted:** the `_LinearSync` tab itself isn't where users
look when scanning a program tab. Mitigated by wrapping the task name
cell as a Sheets `HYPERLINK(linear_url, title)` formula on pull writes
— the name stays human-readable but becomes a one-click jump to the
Linear issue. The mapping table above documents the formula shape.

### Phase 2 design decisions (locked 2026-05-17)

After Phase 1 shipped, three architectural forks were settled before
drafting the Phase 2 spec. Each is captured here because they reshape
the body of the spec and may need to be revisited if real-world use
surfaces problems.

**Decision 1 — Sidecar location: sheet-side (extended `_LinearSync` columns).**

Options considered:
- *Linear-side* (original spec): hidden `<!-- gantt:v1 ... -->` block in
  each Linear issue description footer. Cost: one MCP `save_issue` per
  changed issue, N turns per sync. Visible to teammates as raw HTML.
- *Sheet-side*: extend `_LinearSync` with workbook-only-field columns
  (predecessors-DSL, percent, notes, team). Cost: one Sheets write per
  sync regardless of issue count. Invisible to non-gantt Linear users.
- *Both* (sheet authoritative, Linear mirror): highest cost, best
  cross-team visibility.

Chosen: **sheet-side.** The Phase-1 round-trip experience showed that
MCP write turns scale O(N) and the agent-turn budget is the real cost
cap. Sheet-side keeps Linear clean (no machine-generated HTML in
descriptions) and keeps sync turn count at O(N) only for actual
field-update writes, not metadata maintenance. The visibility
trade-off (teammates don't see workbook-only fields in Linear) is
acceptable because those fields are gantt-specific concepts that
Linear users don't act on anyway.

**Decision 2 — Push scope: full bidirectional (updates + create + archive).**

The original spec split this across Phase 2 (push updates) and
Phase 3 (full sync). Locked the merged scope to avoid a future
migration: anyone using Phase 2 will eventually want create/archive
too, and shipping the snapshot machinery once is cheaper than once
for push-only and again for create/archive.

**Decision 3 — Conflict detection: snapshot-diff with 3-way merge.**

Options considered:
- *Workbook-wins*: push always overwrites Linear. Simple but clobbers
  Linear-side edits between syncs.
- *Per-issue prompt*: surface every divergence; user picks per
  issue. High UX cost.
- *Snapshot-diff* (chosen): store `last_known_linear_state` per issue
  in `_LinearSync`; 3-way compare on sync; per-field default winners
  with prompt only for true conflicts (both sides changed to
  different values).

Sheet schema reserves columns J-R for the snapshot — see the spec
body. Snapshot is refreshed on every successful sync touch so the
next sync starts from a clean baseline.

### Reversibility plan

The decision is reversible at low cost provided the CLI's
stdin/stdout contracts and library-internal `gantt_lib/cp/` +
`gantt_lib/linear/` layers stay clean. To add Arch A later:

1. Add `gantt_lib/linear/client.py` wrapping Linear's GraphQL.
2. Add a `gantt linear-pull <project-ref>` (positional, not `--stdin`)
   entry point that internally fetches via the new client then calls
   the same `gantt_lib/linear/pull.py` orchestrator.
3. Document `LINEAR_API_KEY` in README + `~/.config/gantt/.env`.

No existing code changes. Both front doors call the same orchestrator.

### Revisit criteria

Re-evaluate the Arch A vs B decision if:

- A user genuinely needs headless / cron / CI runs against Linear.
- Phase 2 sync turn cost makes routine use painful even after the
  diff-based design.
- The `claude_ai_Linear` MCP loses a feature we depend on, or its
  rate limits become a blocker.
- Anthropic deprecates or significantly changes the MCP integration
  model in a way that breaks our usage.
