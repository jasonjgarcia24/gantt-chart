# `gantt` skill — v1 proposal

A Claude skill + Python CLI for creating, editing, and updating program Gantt charts in Google Sheets. Designed to mirror Jason's existing tooling pattern (`daily-ops`, `deb-manager`, `claude-tool`): thin skill that translates intent → CLI subcommands.

---

## Context & requirements

Captured from the brainstorm conversation:

- **Audience**: Cross-team program management; shared with other program managers.
- **Scale**: Many programs (portfolio model, not single-program).
- **Source of truth**: The Sheet itself is the standalone plan. Linear/Jira integration is explicitly *future* — not v1.
- **Auto-recalc on shift**: Required. Dependency engine, not just a visualization.
- **No template** to start from — build the shape from scratch.

---

## Approach summary

- **Storage**: Google Sheets — one portfolio workbook, one tab per program, plus shared config/lookup tabs.
- **Visualization**: Formula-rendered timeline grid with conditional formatting (no native chart objects). Robust at 100+ tasks; survives sharing.
- **Mutations**: Python CLI (`gantt`) writes through Google Sheets API. Uses `daily-ops` creds setup (`~/.config/daily-ops/{credentials,token}.json`).
- **Recalc**: CLI-driven. Edit cells or call CLI; `gantt recalc <program>` does topological sort + working-days math + cascade. Idempotent.
- **Skill**: Claude skill maps natural language → CLI calls and auto-runs `recalc` after mutations, reporting the diff.
- **Repo home**: `~/Documents/my-claude-tools/tools/gantt/` (eventually), deployed via the `claude-tool` manifest pattern.

---

## Workbook shape — one portfolio workbook, many tabs

```
_Config       # holidays, working days, team colors, status colors
_People       # owner/team lookup (referenced by data validation)
_Portfolio    # auto-rollup: every program's %done, end date, risk, link to tab
P_<name>      # one tab per program (e.g. P_DataPipeline, P_LaunchQ3)
```

Each `P_*` tab has two regions:

- **Cols A–M**: editable data table (humans + CLI write here)
- **Cols N onward**: timeline grid (formulas only — never hand-edited)

---

## Data model per program tab

| Col | Field | Notes |
|---|---|---|
| A | ID | Auto WBS: `1`, `1.1`, `1.1.2` |
| B | Level | 1 = workstream, 2 = task, 3 = subtask (drives indent) |
| C | Task name | |
| D | Owner | Validated against `_People` |
| E | Team | Validated; drives bar color |
| F | Start | Computed if predecessor exists, else manual |
| G | End | Computed: `WORKDAY(Start, Duration - 1, holidays)` |
| H | Duration | Working days |
| I | % Complete | 0–100 |
| J | Status | Not Started / In Progress / Blocked / At Risk / Done |
| K | Predecessors | DSL: `1.2FS+3, 1.3SS` — FS/SS/FF/SF with optional ±lag |
| L | Milestone? | TRUE = renders as ◆ at end date |
| M | Notes | |
| N+ | Timeline | Formula renders bar based on Start/End/Status |

Timeline cell formula:

```
=IF($L2, IF(N$1=$G2,"◆",""), IF(AND(N$1>=$F2,N$1<=$G2), $J2, ""))
```

Conditional formatting colors by team (default) or status (toggle in `_Config`). Today's column gets a vertical highlight.

---

## Auto-recalc — CLI, not Apps Script

**Decision: CLI does the math, sheet stores results.**

Apps Script in-sheet sounds nicer but:
- Corp Workspace orgs often block scripts on shared sheets.
- Every PM you share with has to re-authorize.
- Hard to version-control and test.
- Doesn't fit existing tooling pattern.

CLI-driven recalc:
- Edit fields in the sheet *or* via CLI → run `gantt recalc <program>`.
- Python pulls the table, computes a topological sort over predecessors, applies lags + working-days math, writes Start/End back.
- Idempotent. Testable in pytest.

**Tradeoff**: not real-time. After an edit, re-run the command. For a planning artifact (vs. a live ops dashboard), that's acceptable.

---

## CLI surface (v1)

```
gantt init                                  # create workbook + template tabs
gantt link <sheet-url>                      # bind CLI to an existing workbook

gantt program new <short-name> --title "..."
gantt program list
gantt program archive <short-name>

gantt task add <program> "name" [--start DATE] [--duration N]
                                [--predecessor 1.2FS+3] [--owner ...]
                                [--team ...] [--milestone] [--parent 1.1]
gantt task update <program> <id> [--field=value]...
gantt task delete <program> <id>

gantt shift <program> <id> ±Nd              # shift this task + cascade dependents
gantt recalc <program>                      # full recompute (idempotent)
gantt critical-path <program>               # compute slack; flag zero-slack tasks

gantt report <program>                      # %done, slip, critical path
gantt report portfolio                      # roll-up across all programs
```

---

## Skill surface

`/gantt` slash command, plus the skill triggers on phrases like *"gantt"*, *"program plan"*, *"shift this milestone"*, *"who's on the critical path"*. `SKILL.md` teaches the agent to:

- Map natural language → CLI subcommands.
  - Example: *"push the data pipeline integration milestone out two weeks"* → resolve program=`DataPipeline`, find task with `Milestone=TRUE` and name matching "integration", call `gantt shift DataPipeline <id> 10d` (working days).
- Ask once when ambiguous (multiple tasks match); don't guess.
- After any mutation, automatically run `recalc` and report the diff (what shifted, what's now on the critical path).

---

## What's IN v1

- Workbook + per-program tabs + portfolio rollup
- Data table with validation (owners, status, predecessor DSL)
- Formula-rendered timeline: team-color bars + milestone diamonds + today line
- CLI mutations + recalc + critical path
- Natural-language skill that calls the CLI
- Tests for: WBS sort, working-days math, predecessor cascade, critical path

## What's OUT of v1 (deliberate)

- Apps Script in-sheet recalc
- Linear/Jira sync (filed for later)
- Resource leveling / capacity / over-allocation warnings
- Baseline vs current variance tracking
- Gantt PDF/PNG export
- Multi-user concurrent-edit conflict handling (single-writer model: whoever ran last `recalc` wins)
- Sub-day granularity (v1 is days only)

---

## Assumptions

1. **Working days = Mon–Fri**, with a per-workbook holidays list in `_Config`. No per-team calendars.
2. **Timeline granularity = 1 column per day**. For long programs (>6 months) this gets wide — mitigated by freezing data columns and scrolling the timeline.
3. **One portfolio workbook**, not one workbook per program. Easier rollups, cleaner sharing.
4. **Other PMs view the sheet directly** (Google Sheets share). They don't need the CLI. If they want to edit, they edit cells; you re-run `recalc` to validate and re-cascade.
5. **Single critical path** computed and highlighted; ignore multiple critical paths for v1.
6. **The CLI is the source of truth for structure** (column layout, conditional formatting rules). It can detect "someone broke the schema" and offer to repair the tab.

---

## Open decisions to lock before scoping tasks

- **Workbook ownership**: should the CLI create one new workbook called e.g. `Jason — Program Portfolio`, or bind to an existing sheet?
- **Timeline default**: daily columns (precise, wide) or weekly columns with start/end-of-week alignment (compact, lossy for milestones)? Author's pick: daily.
- **Team colors**: seed `_Config` with a default palette, or leave for first-run user fill?
- **Predecessor DSL**: comfortable with `1.2FS+3` syntax, or prefer a UI-style "depends on 1.2, lag 3d"? Compact form is faster but uglier.

---

## Next step

Once open decisions are locked, run `planning-and-task-breakdown` to produce an ordered backlog. Rough phases:

1. Creds setup (reuse `daily-ops` OAuth flow + token).
2. Workbook bootstrap (`gantt init` creates tabs, formatting, validation).
3. Data model: read/write program tabs round-trip.
4. Recalc engine: topo sort + working-days + lag DSL parser.
5. CLI commands (one milestone per command group).
6. Critical-path computation + visualization.
7. Skill `SKILL.md` + natural-language routing.
8. Tests (pytest) — recalc, critical path, schema repair.
9. `claude-tool` manifest at `~/Documents/my-claude-tools/tools/gantt/`.
