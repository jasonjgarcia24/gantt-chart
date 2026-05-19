---
name: gantt
description: Operate the user's program portfolio workbook in Google Sheets via the skill-bundled `gantt` CLI at scripts/gantt (under this skill's base directory). USE THIS SKILL whenever the user mentions any of — program plans, program tabs (named like P_TPM90, P_Q3Launch — short program tokens), task progress updates ("OK2DC is 50% done", "task 1.2 is complete", "mark X done"), task scheduling or duration changes ("the eyepiece fab task should be 8 days not 5"), dependencies / predecessors (FS / SS / FF / SF, "depends on", "after task 2"), shifting tasks ("push the launch milestone out 2 weeks", "pull task 5 in by 3 days"), recalculating dates after sheet edits ("I edited a few rows, recalc TPM90"), critical-path queries on the workbook ("what's the critical path in TPM90", "what tasks are blocked"), creating a new program ("create a new program called Q3Launch"), milestones in the workbook, or ANY task referenced by WBS id (e.g. 1, 5.2, OK2DC). When the user refers to a program by a short token (e.g. TPM90, Q3Launch), this skill applies. ALSO triggers on Linear sync (Phase 2: bidirectional): "sync TPM90 with Linear", "push my TPM90 changes to Linear", "create new Linear issues for the tasks I added", "what's different between TPM90 and Linear", "pull the X Linear project into a gantt chart", "refresh TPM90 from Linear", "create a gantt chart from the Linear project X" — anything that maps a Linear project's issues to or from a program tab. Linear URLs (linear.app) or team-key prefixes (e.g. "JAS-5") also count. Capabilities: add / update / delete tasks; cascade dates via topo sort + working-days math; auto-derive Status from %complete + dependencies; shift tasks ±N working days; compute critical path with bold highlighting; sort rows by WBS id; snapshot and inspect baselines; generate audience-targeted Google Slides decks; sync a Linear project bidirectionally with a program tab via the Linear MCP (pull + push + create + archive with 3-way merge). Does NOT handle: gantt visualizations in matplotlib / plotly / Python libraries (those use the libraries directly); metaphorical critical-path / milestone language (hiring, standups, meetings); Asana / Jira / Trello (other PM tools); calendar scheduling (standups, meetings, milestone-review events); generic PM-vocabulary questions ("what does FS mean"); arbitrary Google Sheets unrelated to the portfolio workbook.
tools: Bash
---

You manage the user's program plans in a Google Sheets workbook (default
title `Program Portfolio`, customizable at bootstrap). You drive a CLI
bundled inside this skill at `<skill-base-dir>/scripts/gantt` that
reads/writes one tab per program (`P_TPM90`, etc.). The skill's base
directory is provided at activation time — resolve it once, then invoke the
script with the absolute path. There is no `gantt` on `$PATH`; the CLI is
skill-local on purpose so the bundle stays fully self-contained.

## Subcommand reference

### Lifecycle (first run / status)
- `gantt setup` — create skill-local venv at `<skill-base-dir>/.venv/` and install dependencies. Required on a fresh machine. `--force` rebuilds.
- `gantt bootstrap [--title "<name>"]` — OAuth browser consent + create the portfolio workbook. `--title` sets the Google Sheet name (default: `Program Portfolio`). Required before any other write. `--force` creates a new sheet (old one not deleted).
- `gantt info` — read-only: print sheet URL, config paths, OAuth token + venv state.

### Programs
- `gantt program new <name>` — create a new tab `P_<name>` with the full schema:
  4 header rows (quarter / month / week-num / day), 126 daily timeline columns,
  ARRAYFORMULA-rendered task region, weekend shading + Q/M boundary borders.
  `--force` recreates the tab (loses existing tasks).
- `gantt program migrate-schema <name>` — upgrade an existing tab from the
  pre-PR2b v1 schema (13 data cols, no Milestone Link) to the current v2
  schema (14 cols). Idempotent — no-op on v2 tabs. Use when reading a tab
  raises `ProgramTabSchemaError` pointing to a v1 schema. Mechanically:
  inserts one column between Milestone? (col L) and Notes (now col N);
  timeline cells shift one column to the right. CF rules and ARRAYFORMULA
  references that target cells past col L adjust implicitly via the
  Sheets API.

### Tasks (each auto-cascades after the mutation)
- `gantt task add <program> "<name>" [flags]` — append a new task; cascade fires immediately.
  Flags: `--owner --team --start YYYY-MM-DD --end YYYY-MM-DD --duration N --percent 0-100 --status {Not Started,In Progress,Blocked,At Risk,Done} --predecessors "1FS+3, 2SS" --milestone --notes "..." --parent 1.2`.
  When `--parent` is given, the new task's row is inserted directly under its parent (children stay contiguous in the sheet).
- `gantt task update <program> <id> [flags]` — modify fields; same flags as add. Cascade fires.
- `gantt task delete <program> <id>` — remove. Refuses if any other task lists `<id>` as a predecessor.

### Cascade + analysis
- `gantt recalc <program>` — read tab, cascade dates via topological sort + working-days math (FS/SS/FF/SF + lag), auto-derive Status, sort rows by WBS id, refresh row groups, batch-write changes. Idempotent.
- `gantt shift <program> <id> <±Nd>` — convenience: shift a task's anchor by N working days. If task has predecessors, modifies the lag on its first predecessor (`1FS` → `1FS+5`). If no predecessors, shifts the manual Start. Then auto-cascades.
  - **Negative deltas need the `--` separator** to bypass argparse:
    `gantt shift TPM90 2 -- -2d`
- `gantt critical-path <program>` — compute critical path via CPM forward+backward pass; bold those rows in cols A-M. Print the chain.

## Execution model: you run the skill-bundled `gantt` yourself

- Resolve the skill base directory from the activation context (Claude Code provides it). The entrypoint is `<skill-base-dir>/scripts/gantt`. Invoke it via Bash with that absolute path — there is intentionally no `gantt` on `$PATH`.
- Throughout this document, command references like `gantt info` or `gantt task add ...` are shorthand for `<skill-base-dir>/scripts/gantt info`, etc. Construct the absolute path before invoking.
- Run read-only commands (`gantt info`) without preamble.
- For `bootstrap`, ask the user once what to name the workbook (offering `Program Portfolio` as the default), then tell them one line **before** you run it: "About to create a new Google Sheet titled '<name>' and open a browser for OAuth consent (first run only)." Then run `gantt bootstrap --title "<name>"`.
- For mutations (`task add/update/delete`, `shift`), the CLI auto-cascades and prints TWO result lines (the mutation + the recalc summary). Surface BOTH lines as-is at the top of your response.

## Natural-language parsing

The user describes operations conversationally; translate to flags.

| User says | You run |
|---|---|
| "add a 5-day OKR task to TPM90, PM team, Alex owns it" | `gantt task add TPM90 "Define OKRs" --owner Alex --team PM --duration 5` |
| "add a launch milestone after task 2" | `gantt task add TPM90 "Launch" --milestone --duration 0 --predecessors "2FS"` |
| "add a child task under task 5 called eyepiece fab, 5 days" | `gantt task add TPM90 "Eyepiece fab" --parent 5 --duration 5` |
| "task 1.2 is 50% done now" | `gantt task update TPM90 1.2 --percent 50` |
| "mark task 4 complete" | `gantt task update TPM90 4 --percent 100` |
| "remove task 1.1" | `gantt task delete TPM90 1.1` |
| "shift task 4 out 5 working days" | `gantt shift TPM90 4 +5d` |
| "pull task 4 in by 2 days" | `gantt shift TPM90 4 -- -2d`  *(note the `--` separator for negative deltas)* |
| "what's the critical path on TPM90?" | `gantt critical-path TPM90` |
| "I edited some cells, recalc TPM90" | `gantt recalc TPM90` |
| "create a new program called Q3Launch" | `gantt program new Q3Launch` |
| "show me the workbook URL" | `gantt info` |

### Rules

- **Identify tasks by WBS id, not name.** If the user describes a task by name, look up its id from a recent read of the program tab (or run `gantt recalc <program>` once to get the current state). **If multiple tasks match the name, ASK for the id — do NOT guess.** Picking the wrong task silently corrupts the plan.
- **Predecessor DSL:** `<id><relation>[<signed_lag>]` where relation is `FS` (default — start after pred ends), `SS` (start when pred starts), `FF` (end when pred ends), or `SF` (rare). Multiple comma-separated: `"1FS+3, 2SS"`.
- **Dates:** ISO `YYYY-MM-DD` for `--start` and `--end`.
- **Negative shift deltas need `--`:** `gantt shift TPM90 1 -- -2d`. Positive and zero deltas don't need it.
- **Default `--team`:** leave blank if not specified. Do not invent values.
- **Mutations auto-cascade.** `task add`, `task update`, and `shift` print TWO lines (mutation + recalc). Surface both.

## Linear MCP mode — sync (push + pull + create + archive)

When the user wants to sync workbook state with a Linear project — pull
Linear changes in, push workbook changes out, create new Linear issues
for workbook-only tasks, or archive Linear issues for deleted workbook
rows — route to this mode. After the sync, the workbook and Linear
agree per the 3-way merge conflict policy.

`gantt linear-sync <program>` is the only entry point. For read-only
behavior pass `--direction=pull`; the standalone Phase-1 `linear-pull`
command was removed once Phase 2 fully covered its semantics.

### Trigger language

Prompts that should route to Linear sync mode:

- "sync TPM90 with Linear"
- "pull the X Linear project into a gantt chart [called TPM90]"
- "refresh TPM90 from Linear"
- "push my TPM90 changes to Linear"
- "create new Linear issues for the tasks I added to TPM90"
- "what's different between TPM90 and Linear"
- "create a gantt chart from the Linear project X"
- any URL of the form `https://linear.app/<workspace>/project/<slug>`
- any prompt that mentions Linear plus a target program name

### Source detection rule

If the user names a Linear project (URL, team-key prefix like `JAS-`, or
explicit "linear" keyword) AND a target program (e.g. `--as TPM90`),
route to Linear sync mode. If the target program isn't obvious from the
prompt, ask once before guessing — never invent a tab name.

If both a workbook program and a Linear project plausibly match the
user's words, ask once to disambiguate. Never guess.

**Direction hints**: "sync" → `--direction=both` (default). "pull /
refresh from Linear" → `--direction=pull`. "push my changes to
Linear" → `--direction=push`. "what's different" → `--dry-run` (no apply).

### MCP read-side call sequence

Use the `claude_ai_Linear` MCP (must be installed + authenticated in
Claude Code; if it isn't, tell the user and stop). For sync, every
invocation starts with this read sequence:

1. `mcp__claude_ai_Linear__list_teams()` — cache for the session
2. `mcp__claude_ai_Linear__list_projects(team=<team>, query=<name-or-slug>)` — resolve project ID
3. `mcp__claude_ai_Linear__get_project(query=<id>, includeMilestones=true)` — full description + milestones
4. `mcp__claude_ai_Linear__list_issues(project=<id>)` — all issues (paginate via `cursor` if `hasNextPage` is true; up to 250 per page)
5. **For every issue:** `mcp__claude_ai_Linear__get_issue(id=<issue>, includeRelations=true)` — needed because `list_issues` does NOT include `blockedBy` / `relations`. This is the dominant per-sync read cost: roughly N calls for an N-issue project.
6. `mcp__claude_ai_Linear__list_milestones(project=<id>)` — prefer this over `get_project(includeMilestones)` (cleaner numeric `progress` 0..1).
7. `mcp__claude_ai_Linear__list_issue_statuses(team=<team>)` — **required for Phase-2 archive**; also needed for custom-state name mapping. Cache for the session.
8. `mcp__claude_ai_Linear__list_users()` — **required for assignee pre-validation** so the CLI can skip workbook Owner values that don't resolve to a workspace user (Linear's `save_issue.assignee` silently no-ops invalid names). Cache for the session.

Cost note: surface to the user before step 5 fires if the project has
more than ~20 issues. *"This project has 47 issues — fetching blocker
data will take ~47 MCP read calls. Proceed? (Y/n)"*

### MCP write-side execution loop (Phase-2 push path)

The CLI returns a JSON list of `mcp_requests`. Each request describes
one `save_issue` call the agent must dispatch. Walk the list in two
passes:

**Pass 1 — independent writes (parallel dispatch in one Claude turn).**
All `pass_number=1` requests can fire simultaneously via parallel
tool calls. Includes:
- field updates on existing issues (`id=JAS-X` with the changed fields)
- creates (no `id`, with `team`/`project`/`title`/...) — capture each
  response's `id` and `url` immediately
- archives (`id=JAS-X` with `state=<canceled-type state name>`)

**Pass 2 — blockedBy reconciliation (depends on pass-1).** All
`pass_number=2` requests reference `pass_1_placeholders` like
`__NEW_<wbs>__`. Substitute each placeholder with the matching
pass-1 response's `id` before dispatching this pass.

**Pass-1 cost guidance**: dispatch up to ~10-20 calls per turn. For
larger batches, split across multiple turns. Total Claude-turn count
is roughly `ceil(N / 15)` for an N-changed-issue sync — not
`N` turns.

**Post-call snapshot refresh (optimistic)**: the CLI already wrote
the expected post-sync state to `_LinearSync` before returning the
MCP TODO list. The agent doesn't need a follow-up CLI call for
updates / archives — just dispatch the writes. If any write fails or
silently no-ops (see warning below), the next sync's 3-way merge
will detect the drift and re-resolve.

**Create-result handoff**: for `create` rows specifically, the CLI
COULD NOT write the `_LinearSync` row at sync time (didn't know the
new linear_id yet). After pass-1 returns, hand the
`{wbs_id → new_linear_id, new_url}` map back to a follow-up CLI call
(`gantt linear-sync --apply-create-results` — future feature; for v1
the user can re-sync to pick up the new state via the normal diff).

**SILENT-NO-OP WARNING**: `save_issue` accepts an invalid `state` name
silently — it returns 200-OK with the issue unchanged (same `updatedAt`
as `createdAt`, no error). After each write, compare the response's
`updatedAt` to its `createdAt` (or to the issue's pre-call `updatedAt`).
If they're equal, log a warning: *"save_issue for JAS-X didn't take
effect — likely an invalid field value. Check the state/assignee name
against `list_issue_statuses`/`list_users`."* The probe found this
when "Cancelled" (British) silently failed; "Canceled" (American)
worked. **Always populate `linear_archive_state` from
`list_issue_statuses` — never hard-code.**

### Normalization recipe (MCP responses → `linear-sync --stdin` JSON)

Build the JSON payload that the CLI expects. Field-by-field:

| `CpInput*` field | MCP source | Notes |
|---|---|---|
| `project.name` | `get_project.name` | Used in agent rendering only |
| `project.source` | literal `"linear"` | |
| `project.source_ref` | `get_project.url` | Deep link for traceability |
| `config.default_duration_days` | `1` | Or whatever the user prefers; surface in dry-run |
| `config.today` | today's ISO date | Used to anchor issues with no blockers + no startedAt |
| `config.estimate_to_days.ratio` | `1.0` for points→days | Detect unit from issue `estimate.name` |
| `config.linear_team` | the team name from `list_teams` (e.g. `"JasonGarcia"`) | **Required for create operations**. Empty string when not creating anything new |
| `config.linear_project` | the project name from `list_projects` | **Required for Phase-2 create** |
| `config.linear_archive_state` | first state of `type=="canceled"` from `list_issue_statuses` (e.g. `"Canceled"`) | **Required for Phase-2 archive**. If empty, archive requests are silently skipped |
| `config.linear_team_label_map` | `{workbook_team_name: linear_label_name}` dict — populate from user-supplied mapping or workbook config | **Optional**. Enables Team ↔ Linear-labels bidirectional sync. Empty `{}` disables team sync (workbook Team becomes a sidecar-only field). |
| `config.linear_users` | `list_users()` response, mapped to `[{id, email, name, displayName}, ...]` | **Required for assignee push**. Empty `[]` disables assignee pre-validation (raw workbook Owner is sent to Linear and may silently no-op). |
| `issues[].linear_id` | `issue.id` (e.g. `JAS-5`) | The Linear identifier, not the UUID |
| `issues[].title` | `issue.title` | |
| `issues[].state` | mapped from `issue.statusType` | See state mapping table — map by **type**, not name |
| `issues[].estimate_days` | `issue.estimate.value × estimate_to_days.ratio` | If `estimate` is absent, leave `null` — adapter warns + uses default |
| `issues[].percent` | always `0` (Linear doesn't carry %complete) | |
| `issues[].assignee` | `issue.assignee.email` if present else `""` | Email preferred over display name |
| `issues[].start_anchor` | `issue.startedAt` or `null` | |
| `issues[].end_anchor` | `issue.dueDate` or `null` | |
| `issues[].is_milestone` | `false` for regular issues; `true` for synthesized milestone tasks | See milestone synthesis below |
| `issues[].parent_linear_id` | `issue.parentId` or `null` | Stable identifier; CLI uses for WBS hierarchy |
| `issues[].linear_url` | `issue.url` | Used by CLI for HYPERLINK formula on the name cell |
| `issues[].labels` | `[lbl.name for lbl in issue.labels]` | List of label names. Required when `linear_team_label_map` is set so the merge engine can derive workbook Team. Pass `[]` (or omit) when team sync is off. |
| `issues[].milestone_id` | `"MS-" + issue.milestone.id` when the issue is on a milestone | Prefix with `MS-` so it matches the synthesized milestone-row `linear_id` (the CLI compares them in the 3-way merge). Empty string when the issue isn't on a milestone or milestone sync is off. |
| `edges[]` | from `get_issue.relations.blockedBy` per issue | Each `blockedBy` item → `{from_linear_id, to_linear_id, type: "FS", lag_days: 0}` |

**State mapping** (always by `statusType`, not `status` name):

| Linear `statusType` | Workbook `state` |
|---|---|
| `backlog` | `Not Started` |
| `unstarted` | `Planned` |
| `started` | `In Progress` |
| `completed` | `Done` |
| `canceled` | `Cancelled` |

The workbook has additional state vocabulary that doesn't round-trip from
Linear: `Blocked` and `At Risk` are workbook-local flags. They get pushed
to Linear as `In Progress` (Blocked) or `In Progress`/`Backlog` (At Risk,
depending on whether start ≤ today). On pull, Linear's `In Progress`
collapses to workbook `In Progress` — losing the Blocked/At Risk flag.
Users who care about preserving those flags re-set them after sync.

**Estimate unit detection** — read the first non-null `estimate.name`:

| `estimate.name` example | Inferred unit | Default ratio (→ days) |
|---|---|---|
| `"5 Points"` | points | 1.0 |
| `"8 Hours"` | hours | 0.125 (8h = 1 day) |
| `"L"`, `"M"`, etc. | t-shirt | manual user input — ask |
| (no estimates) | none | 1.0 (use default_duration_days for all) |

**Milestone synthesis** — for each entry in `list_milestones`, append a
synthetic issue to the payload:
- `linear_id`: milestone UUID prefixed `MS-` (won't collide with real issue keys)
- `title`: milestone name
- `is_milestone`: `true`
- `estimate_days`: `0`
- `linear_url`: the project URL (Linear doesn't deep-link to milestones)

**Pagination** — if `list_issues.hasNextPage`, follow `cursor` and
merge pages before invoking the CLI.

**Team ↔ labels mapping** — when the user wants Team sync, fetch labels per team via `list_issue_labels(team=<team_name>)` and ask the user (or read from a workbook-side config) for the workbook-team → Linear-label correspondence. Populate `config.linear_team_label_map` accordingly. The team-label set must be mutually-exclusive: each issue should carry at most one team-label, or pull-side team derivation will silently pick the first-match.

**Milestone bidirectional sync (PR2b)** — when a workbook user sets the visible "Milestone Link" column (col M) on a task to a milestone-row's WBS, the CLI translates that to the milestone's `MS-<uuid>` and pushes `save_issue(milestone=<uuid>)` (strips the `MS-` prefix). On pull, the agent must populate `issues[].milestone_id` as `"MS-" + linear_milestone_uuid` so the merge engine can compare it directly to the workbook-derived value. Milestone rows themselves stay synced via the existing `linear_id="MS-<uuid>"` convention; their title + targetDate round-trip via the standard title/due_date paths.

**Assignee pre-validation (PR-G)** — workbook Owner cells can contain either plain text (legacy) or Google Sheets person chips (preferred — `sheets.read_owner_chip_emails` lifts the chip's canonical email automatically). On read, `task.owner` holds whatever is most-resolvable: chip email if present, else the raw cell text. Push then resolves that against `config.linear_users` (which the agent populates from `list_users()`). When the resolver can't match, the assignee field is dropped from the `save_issue` kwargs entirely — better than sending a value Linear silently no-ops. Unresolved values are surfaced in `SyncResult.unresolved_owners` (per-row list) and `summary.unresolved_owners` (count); the stderr result line appends ", N owner(s) unresolved" when non-zero. External collaborators who aren't workspace users will appear in this list each sync — that's expected; the user can invite them as guests in Linear if they want assignee sync to start working.

### Dry-run-first + two-stage confirmation

Sync is a real workbook + Linear write. Always preview before applying:

1. **Fetch + normalize → JSON payload.**
2. **Run `linear-sync --dry-run`**, pipe payload via stdin.
3. **Parse the JSON summary; render the diff to the user** as a
   markdown table (see Rendering below).
4. **Confirmation gate logic** (this is where the two-stage matters):
   - If the diff has **only** `update` / `unchanged` / `pull_new` /
     `orphaned_link` rows (no creates, no archives): **apply
     automatically** without an extra prompt. Per the established
     no-redundant-apply-gate pattern, the dry-run preview IS the
     consent — re-prompting on every clean sync is friction.
   - If the diff has any `create` or `archive` rows: **ask explicitly
     before applying**. *"This sync will create N new Linear issues
     and archive M existing ones. Apply? (y/n)"* Creates and
     archives are higher-stakes — wrong create makes noise in Linear
     that's hard to clean up; wrong archive destroys team context.
5. **On apply**: re-run without `--dry-run`. The CLI applies pull-side
   workbook writes + emits the MCP TODO list for the agent to dispatch.
6. **Dispatch MCP TODO list** per the write-side execution loop above.
7. **Surface the result line** at the top of the response per the
   skill's convention.

### CLI invocation

Primary verb (Phase 2):
```
<skill-base-dir>/scripts/gantt linear-sync --stdin --as <program> [--dry-run] [--direction={pull,push,both}] [--force]
```

Alias:
- `gantt linear-push --stdin --as <program>` — same as `linear-sync --direction=push`

Capture stdout (JSON) separately from stderr (result line). Result line
goes at the top of your response verbatim.

If the program tab doesn't exist (CLI returns `program_tab_missing`),
tell the user and ask whether to run `gantt program new <program>`
first, then retry the sync.

### Rendering

After the dry-run summary, present the diff as a markdown table:

| Action | WBS | Linear ID | Title | Direction | Changed fields |
|---|---|---|---|---|---|
| update | 1 | JAS-5 | Spec optics | push | title: "Old" → "New" (workbook wins) |
| update | 2 | JAS-6 | Eyepiece fab | pull | state: Backlog → In Progress (linear) |
| create | 3 | — | Manual task | push | new Linear issue |
| pull_new | — | JAS-99 | Brand new from Linear | pull | new workbook row |
| archive | 4 | JAS-9 | Launch | push | state → Canceled |
| orphaned_link | 5 | JAS-GHOST | (gone from Linear) | — | workbook row preserved |
| unchanged | 6 | JAS-8 | Eyepiece QA | — | — |

Below the table:

- **Summary**: *"N pushed, M pulled, K created, J archived, C conflicts
  resolved, U unchanged."*
- **Conflicts** (if any): dedicated sub-section listing each true
  conflict with the resolved winner and an offer to override:
  > ⚠ JAS-5 title: workbook "Spec optics (workbook rename)" vs Linear
  > "Spec optics (linear rename)". Default policy: Linear wins. Reply
  > "override JAS-5 title workbook" to flip.
- **Warnings** as a bulleted sub-list (missing-estimate, unmapped
  state, etc.).
- **Snapshot disclaimer**: *"Snapshot fetched at HH:MM:SS — Linear
  may have changed since."*
- **MCP cost preview** (push/both directions): *"This sync will
  dispatch K MCP write calls across ~⌈K/15⌉ agent turns."*
- **Confirmation prompt** per the two-stage rule above.

After apply, response opens with the verified result line:
`gantt: linear-sync <program> — N pushed, M pulled, K created, J archived, C conflicts resolved, U unchanged ✓`

### Error handling

| CLI error / exit code | How to render |
|---|---|
| `contract_validation` (exit 1) | "I built a malformed payload — bug in normalization. Detail: \<error\>." Don't retry; surface to user. |
| `invalid_argument` (exit 1) | E.g. unknown `--direction`. Show the error and retry with corrected args. |
| `program_tab_missing` (exit 2) | "Program tab `P_<program>` doesn't exist. Run `gantt program new <program>` first? (y/n)" |
| `internal` (exit 3) | Show error verbatim; tell user to retry. |

**MCP-side errors** (after CLI returns):
- `save_issue` no-op (response `updatedAt == createdAt`): log warning,
  surface as a per-issue note in the result. Likely a state/assignee
  name mismatch — re-fetch `list_issue_statuses` / `list_users` and
  retry that one issue.
- `save_issue` rate-limit: back off ~5s, retry the same parallel batch.
- `save_issue` validation error (e.g. invalid `team` on create): stop
  the sync, surface the error, do NOT retry — user needs to fix the
  payload.

## First-run handling

`gantt` has three first-run states. Handle each separately:

1. **No venv / dependencies missing** — CLI prints `gantt: error: gantt dependencies not installed. Run: gantt setup`. Tell the user "the venv isn't set up yet — run `gantt setup` to create the skill-local venv and install dependencies." Ask if you should run it. Do NOT auto-run.
2. **No sheet bootstrapped** — CLI prints `gantt: no sheet bootstrapped yet. Run: gantt bootstrap`. Tell the user the sheet hasn't been created yet. Ask what they want to name the workbook (offer `Program Portfolio` as the default) and confirm whether to run `gantt bootstrap --title "<name>"`. Do NOT auto-bootstrap (it opens an OAuth browser flow and creates a real Sheet in their Drive).
3. **OAuth token expired / `invalid_grant`** — delete `~/.config/gantt/token.json` (you can `rm` it) and re-run the original command to trigger re-consent. Warn the user once before the delete.

## Result-line convention

Every `gantt` command prints a verified line that starts with `gantt:` and ends with ` ✓` (or ` ✗` on error). For mutations, `task add/update` and `shift` print TWO lines: the mutation result + the auto-recalc summary.

**The first line(s) of your response MUST be the verified result line(s) from the CLI output.** No preamble, no "okay, here's what happened." Just the line(s), then supporting detail below if useful.

## Things this skill does NOT do

- **Fuzzy task lookup by name** — always ask the user for the WBS id when ambiguous.
- **Linear** — `gantt linear-sync` (full bidirectional: pull + push + create + archive; use `--direction=pull` for read-only) is supported via the Linear MCP. See the Linear MCP sync mode section above.
- **Jira / Asana / Trello sync** — out of scope.
- **Multi-PM concurrent edit reconciliation** — single-writer model. If another PM edits the sheet, re-run `recalc` to re-cascade.
- **Sub-day granularity, per-team calendars, multiple critical paths** — single critical path only; days only.
- **Renaming WBS ids on cleanup** — `recalc` sorts rows by WBS id but never changes ids (would break predecessor references).
