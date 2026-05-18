---
name: gantt
description: Operate the user's program portfolio workbook in Google Sheets via the skill-bundled `gantt` CLI at scripts/gantt (under this skill's base directory). USE THIS SKILL whenever the user mentions any of — program plans, program tabs (named like P_TPM90, P_Q3Launch — short program tokens), task progress updates ("OK2DC is 50% done", "task 1.2 is complete", "mark X done"), task scheduling or duration changes ("the eyepiece fab task should be 8 days not 5"), dependencies / predecessors (FS / SS / FF / SF, "depends on", "after task 2"), shifting tasks ("push the launch milestone out 2 weeks", "pull task 5 in by 3 days"), recalculating dates after sheet edits ("I edited a few rows, recalc TPM90"), critical-path queries on the workbook ("what's the critical path in TPM90", "what tasks are blocked"), creating a new program ("create a new program called Q3Launch"), milestones in the workbook, or ANY task referenced by WBS id (e.g. 1, 5.2, OK2DC). When the user refers to a program by a short token (e.g. TPM90, Q3Launch), this skill applies. ALSO triggers on Linear pulls (Phase 1): "pull the X Linear project into a gantt chart", "refresh TPM90 from Linear", "create a gantt chart from the Linear project X", "pull from Linear" — anything that maps a Linear project's issues into a program tab. Linear URLs (linear.app) or team-key prefixes (e.g. "JAS-5") also count. Capabilities: add / update / delete tasks; cascade dates via topo sort + working-days math; auto-derive Status from %complete + dependencies; shift tasks ±N working days; compute critical path with bold highlighting; sort rows by WBS id; snapshot and inspect baselines; generate audience-targeted Google Slides decks; pull a Linear project into a program tab via the Linear MCP (read-only against Linear in Phase 1). Does NOT handle: gantt visualizations in matplotlib / plotly / Python libraries (those use the libraries directly); metaphorical critical-path / milestone language (hiring, standups, meetings); writes back to Linear (push/sync are Phase 2+); Asana / Jira / Trello (other PM tools); calendar scheduling (standups, meetings, milestone-review events); generic PM-vocabulary questions ("what does FS mean"); arbitrary Google Sheets unrelated to the portfolio workbook.
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

## Linear MCP mode (Phase 1: pull only)

When the user wants to pull a Linear project into the workbook, route to
this mode. After the pull, every existing gantt verb (`recalc`,
`critical-path`, `deck`, `baseline`, `shift`, etc.) works on the pulled
program identically to a workbook-native one.

**Phase 1 is read-only against Linear.** No writes are made to Linear
issues, descriptions, relations, or anything else. Push/sync are Phase 2+.

### Trigger language

Prompts that should route to Linear pull mode:

- "pull the X Linear project into a gantt chart [called TPM90]"
- "refresh TPM90 from Linear"
- "create a gantt chart from the Linear project X"
- "pull the Linear project at linear.app/.../<slug>"
- any URL of the form `https://linear.app/<workspace>/project/<slug>`
- any prompt that mentions Linear plus a target program name

### Source detection rule

If the user names a Linear project (URL, team-key prefix like `JAS-`, or
explicit "linear" keyword) AND a target program (e.g. `--as TPM90`),
route to Linear pull mode. If the target program isn't obvious from the
prompt, ask once before guessing — never invent a tab name.

If both a workbook program and a Linear project plausibly match the
user's words, ask once to disambiguate. Never guess.

### MCP call sequence

Use the `claude_ai_Linear` MCP (must be installed + authenticated in
Claude Code; if it isn't, tell the user and stop). Call sequence:

1. `mcp__claude_ai_Linear__list_teams()` — cache for the session
2. `mcp__claude_ai_Linear__list_projects(team=<team>, query=<name-or-slug>)` — resolve project ID
3. `mcp__claude_ai_Linear__get_project(query=<id>, includeMilestones=true)` — full description + milestones
4. `mcp__claude_ai_Linear__list_issues(project=<id>)` — all issues (paginate via `cursor` if `hasNextPage` is true; up to 250 per page)
5. **For every issue:** `mcp__claude_ai_Linear__get_issue(id=<issue>, includeRelations=true)` — needed because `list_issues` does NOT include `blockedBy` / `relations`. This is the dominant per-pull cost: roughly N MCP calls for an N-issue project.
6. `mcp__claude_ai_Linear__list_milestones(project=<id>)` — prefer this over `get_project(includeMilestones)` because the shape is cleaner (numeric `progress` 0..1 instead of percent string)
7. `mcp__claude_ai_Linear__list_issue_statuses(team=<team>)` — cache for the session; needed for state-name → workbook-Status mapping when teams have custom states

Cost note: surface to the user before step 5 fires if the project has
more than ~20 issues. Example: *"This project has 47 issues — fetching
blocker data will take ~47 MCP calls. Proceed? (Y/n)"*

### Normalization recipe (MCP responses → `linear-pull --stdin` JSON)

Build the JSON payload that the CLI expects. Field-by-field:

| `CpInput*` field | MCP source | Notes |
|---|---|---|
| `project.name` | `get_project.name` | Used in agent rendering only |
| `project.source` | literal `"linear"` | |
| `project.source_ref` | `get_project.url` | Deep link for traceability |
| `config.default_duration_days` | `1` | Or whatever the user prefers; surface in dry-run |
| `config.today` | today's ISO date | Used to anchor issues with no blockers + no startedAt |
| `config.estimate_to_days.ratio` | `1.0` for points→days | Detect unit from issue `estimate.name` (see below) |
| `issues[].linear_id` | `issue.id` (e.g. `JAS-5`) | The Linear identifier, not the UUID |
| `issues[].title` | `issue.title` | |
| `issues[].state` | mapped from `issue.statusType` | See state mapping table below — map by **type**, not name |
| `issues[].estimate_days` | `issue.estimate.value × estimate_to_days.ratio` | If `estimate` is absent, leave `null` — adapter will warn + use default |
| `issues[].percent` | always `0` for now | Linear doesn't carry %complete |
| `issues[].assignee` | `issue.assignee.email` if present else `""` | Email preferred over display name |
| `issues[].start_anchor` | `issue.startedAt` or `null` | For issues with no blockers |
| `issues[].end_anchor` | `issue.dueDate` or `null` | |
| `issues[].is_milestone` | `false` for regular issues; `true` for synthesized milestone tasks | See milestone synthesis below |
| `issues[].parent_linear_id` | `issue.parentId` or `null` | Stable identifier; CLI uses for WBS hierarchy |
| `issues[].linear_url` | `issue.url` | Used by CLI for HYPERLINK formula on the name cell |
| `edges[]` | from `get_issue(includeRelations=true).relations.blockedBy` per issue | Each `blockedBy` item → one edge: `{from_linear_id, to_linear_id, type: "FS", lag_days: 0}` |

**State mapping** (always by `statusType`, not `status` name):

| Linear `statusType` | Workbook `state` |
|---|---|
| `backlog` | `Not Started` |
| `unstarted` | `Not Started` |
| `started` | `In Progress` |
| `completed` | `Done` |
| `canceled` | `Cancelled` |

**Estimate unit detection** — read the first non-null `estimate.name`
across the project's issues:

| `estimate.name` example | Inferred unit | Default ratio (→ days) |
|---|---|---|
| `"5 Points"` | points | 1.0 |
| `"8 Hours"` | hours | 0.125 (8h = 1 day) |
| `"L"`, `"M"`, etc. | t-shirt | manual user input — ask |
| (no estimates) | none | 1.0 (use default_duration_days for all) |

Surface the detected unit and the conversion in the dry-run preview so
the user can confirm before applying.

**Milestone synthesis** — for each entry in `list_milestones`, append a
synthetic issue to the payload with:
- `linear_id`: the milestone's UUID prefixed with `MS-` (e.g. `MS-0ec2ab6b-68aa-4c46-9dd1-2f59bae7921d`) so it doesn't collide with real issue keys
- `title`: the milestone name
- `is_milestone`: `true`
- `estimate_days`: `0`
- `linear_url`: the project URL with `?milestone=<id>` query, if Linear provides one (else just the project URL)
- No edges; the agent can optionally add an edge from the last
  non-milestone issue to the milestone if it makes sense (skip if
  unsure — milestones can be free-floating).

**Pagination** — if `list_issues.hasNextPage` is true, follow `cursor`
and merge pages before invoking the CLI.

### Dry-run-first convention (always)

The agent ALWAYS runs `linear-pull` with `--dry-run` first, then asks
the user to confirm before applying. Pull is a real workbook write —
matching the skill's existing "confirm-before-side-effect" pattern.

Flow:

1. Fetch + normalize → JSON payload
2. Run `<skill-base-dir>/scripts/gantt linear-pull --stdin --as <program> --dry-run`,
   piping the JSON payload via stdin
3. Parse the stdout JSON summary; render the diff to the user as a
   markdown table (see Rendering below)
4. Ask: *"Apply this? (y/n)"*
5. On `y`: re-run the same command without `--dry-run`; surface the
   result line at the top of the response per the skill's convention.
6. On `n`: stop; don't write.

### CLI invocation

Construct the absolute path:
```
<skill-base-dir>/scripts/gantt linear-pull --stdin --as <program> [--dry-run] [--force]
```

Capture stdout (JSON) separately from stderr (result line) — the
result line goes at the top of your response verbatim per the
skill's convention.

If the program tab doesn't exist yet (CLI returns
`program_tab_missing` error), tell the user and ask whether to create
it first via `<skill-base-dir>/scripts/gantt program new <program>`,
then retry the pull.

### Rendering

After the dry-run summary, present the diff as a markdown table:

| Action | WBS | Linear ID | Title | Changed fields |
|---|---|---|---|---|
| added | 1 | JAS-5 | Spec optics | — |
| updated | 2 | JAS-6 | Eyepiece fab | state: Backlog→In Progress (linear) |
| unchanged | 3 | JAS-7 | Doc revision | — |
| workbook_only_preserved | 4 | — | Manual checkpoint | — |

Below the table:

- One-line summary: *"N added, M updated, K unchanged"*
- Any warnings as a bulleted sub-list (e.g. *"JAS-7: no estimate; using
  default 1d"*)
- Snapshot disclaimer: *"Snapshot fetched at HH:MM:SS — Linear may have
  changed since."*
- The "Apply this? (y/n)" prompt

After apply, the agent's response opens with the verified result line
(`gantt: linear-pull <program> — N added, M updated, K unchanged ✓`)
per the existing skill convention.

### Error handling

| CLI error / exit code | How to render |
|---|---|
| `contract_validation` (exit 1) | "I built a malformed payload — bug in normalization. Try again or report: \<error detail>." Don't retry — the bug is in the agent, not the user's input. |
| `program_tab_missing` (exit 2) | "Program tab `P_<program>` doesn't exist. Run `gantt program new <program>` first? (y/n)" |
| `cycle_detected` (exit 2) | Surface the trace as Linear-issue links so user can fix the cycle in Linear. Re-pull after fix. |
| `unanchored_task` (exit 2) | An existing workbook row has no predecessors and no manual start. Tell the user to run `gantt recalc <program>` to surface the offending row. |
| `internal` (exit 3) | Show the error verbatim; tell user to retry. |

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
- **Linear push / sync** — Phase 1 supports Linear → workbook pull only. Writes back to Linear (push) and bidirectional sync are Phase 2+.
- **Jira / Asana / Trello sync** — out of scope.
- **Multi-PM concurrent edit reconciliation** — single-writer model. If another PM edits the sheet, re-run `recalc` to re-cascade.
- **Sub-day granularity, per-team calendars, multiple critical paths** — single critical path only; days only.
- **Renaming WBS ids on cleanup** — `recalc` sorts rows by WBS id but never changes ids (would break predecessor references).
