# gantt

A Python CLI + Claude skill for creating, editing, and maintaining program
Gantt charts in Google Sheets. The Sheet is the artifact PMs share; the CLI
does the dependency math (topological sort, working-days arithmetic, FS/SS/FF/SF
relations, critical path) and writes the results back.

Modeled on the `daily-ops` bundle pattern: bundle-local venv, user state under
`~/.config/gantt/`, OAuth-backed Sheets writes, one verified result line per command.

---

## Status — v0.5 thin slice

| Phase | Scope | State |
|---|---|---|
| 1. Foundation | `setup`, `bootstrap`, `info` | ✅ shipped |
| 2. Domain logic | model, DSL parser, working-days math, cascade engine | ✅ shipped |
| 3. First user-visible program | `program new`, `task add/update/delete` | ✅ shipped |
| 4. Cascade wiring | `recalc` (reads tab, cascades, writes back) | ✅ shipped |
| 5. Critical path + shift | `critical-path`, `shift` | ✅ shipped |
| 6. Skill surface | `SKILL.md`, `/gantt` slash command | ✅ shipped (activate via the symlinks below) |

Test suite: 179 tests across `model`, `dsl`, `dates`, `cascade`, `refs`, `schema`, `sheets_helpers`, `auto_status`, `critical_path`.

See `docs/ideas/gantt-skill-v0.5.md` for the v0.5 charter and `docs/plans/v0.5-backlog.md`
for the full task breakdown.

---

## First-time setup

```bash
# 1. Create the bundle-local venv and install dependencies.
./gantt setup

# 2. Authorize Google Sheets access and create the portfolio workbook.
./gantt bootstrap
# → opens a browser for OAuth consent
# → creates "Jason — Program Portfolio" with a seeded _Config tab
# → saves sheet_id + URL to ~/.config/gantt/config.json

# 3. (Optional but recommended) Symlink the CLI onto PATH so you can run
# bare `gantt …` from anywhere instead of `./gantt …`.
ln -s ~/Documents/gantt-chart/gantt ~/.local/bin/gantt

# 4. (Optional) Activate the SKILL + slash command for Claude. Until the
# claude-tool manifest ships, this is a manual symlink:
ln -s ~/Documents/gantt-chart/SKILL.md          ~/.claude/skills/gantt/SKILL.md
ln -s ~/Documents/gantt-chart/commands/gantt.md ~/.claude/commands/gantt.md
```

After bootstrap, `gantt info` reports the sheet URL, OAuth state, and venv status.

State lives in `~/.config/gantt/`:
- `credentials.json` — OAuth 2.0 Desktop client (seeded one-time from
  `~/Documents/ai-project-model/credentials.json` if present)
- `token.json` — refresh token after first consent (0600)
- `config.json` — sheet id + URL after bootstrap

---

## Usage (commands shipped so far)

### Create a program

```bash
./gantt program new TPM90
# → creates tab P_TPM90 with:
#   • 13 data headers (A–M): ID, Level, Name, Owner, Team, Start, End,
#     Duration, % Complete, Status, Predecessors, Milestone?, Notes
#   • 4 timeline header rows above the day cells:
#       row 1: quarter (e.g. "Q2 2026"), one merged cell per quarter
#       row 2: month   (e.g. "May"),     one merged cell per month
#       row 3: week    (e.g. "Wk20"),    one merged cell per ISO week
#       row 4: day     (e.g. "11"),      one cell per CALENDAR day
#   • 126 daily timeline columns (N+) starting from Monday-of-today,
#     20px wide each
#   • Single ARRAYFORMULA at N5 fills the entire task region — new tasks
#     inside rows 5-104 render automatically without per-row formulas
#   • Conditional formatting:
#       — weekend cells (Sat/Sun) shaded light grey
#       — task bars colored by Team value (col E) using _Config palette
#   • Borders: light grey on month boundaries, heavier grey on quarters
#     (full vertical, span the whole tab)
#   • Status dropdown on col J, Milestone checkbox on col L
#   • Frozen 4 header rows + first 3 columns
```

### Add a task

```bash
./gantt task add TPM90 "Define OKRs" \
    --owner Jason --team PM --start 2026-06-01 --duration 5

./gantt task add TPM90 "Stakeholder interviews" \
    --owner Jason --team Research --duration 10 --predecessors "1FS"

./gantt task add TPM90 "Launch milestone" \
    --milestone --duration 0 --predecessors "2FS"

# Sub-task: WBS id is auto-assigned as a child of --parent.
./gantt task add TPM90 "Draft OKR doc" \
    --parent 1 --owner Jason --team PM --duration 2
```

WBS ids auto-assign:
- top-level → `1`, `2`, `3`, ... (always `max(existing) + 1`; gaps are not reused)
- child of `1` → `1.1`, `1.2`, `1.3`, ...
- child of `1.1` → `1.1.1`, ...

### Update / delete

```bash
./gantt task update TPM90 1.1 --duration 4 --notes "expanded scope"
./gantt task delete TPM90 1.1
# Delete refuses if any other task lists the target as a predecessor.
```

`task add` and `task update` auto-cascade — they print a second result line summarising the recalc that follows the mutation.

### Recalc, shift, critical-path

```bash
./gantt recalc TPM90
# Reads the tab, runs cascade + auto-status, sorts rows by WBS id, refreshes
# row groups, batch-writes the diff. Idempotent. Print line includes counts:
# "N dates shifted, M statuses updated, K names re-indented, P rows reordered".

./gantt shift TPM90 1.2 +5d
# Shift task 1.2 by 5 working days (anchors via manual Start if no preds,
# else by adding lag to the first predecessor). Auto-cascades.
./gantt shift TPM90 1.2 -- -2d
# Negative deltas need the `--` separator (argparse).

./gantt critical-path TPM90
# CPM forward+backward pass; bolds critical-path rows; prints the chain.
```

### Predecessor DSL

Compact form: `<id><relation><signed_lag>?`, comma-separated.

| Relation | Meaning |
|---|---|
| `1.2FS` | Finish-to-start: 2 starts the working day after 1.2 ends |
| `1.2FS+3` | …plus 3 working-day lag |
| `1.2SS-1` | Start-to-start, minus 1 day (overlap) |
| `1.2FF` | Finish-to-finish: succ ends when 1.2 ends |
| `1.2SF` | Start-to-finish (rare) |

Multiple predecessors: `"1.2FS+3, 1.3SS"`. Whitespace tolerated everywhere.

---

## Project layout

```
gantt-chart/
├── gantt                       # CLI entry point (executable, with venv self-exec trampoline)
├── requirements.txt
├── pyproject.toml              # pytest config
├── README.md
├── .gitignore
├── SKILL.md                    # Claude skill — natural-language → CLI routing
├── commands/
│   └── gantt.md                # /gantt slash command
├── gantt_lib/                  # pure-Python domain logic + Sheets I/O wrapper
│   ├── model.py                # Task, Program, Status, next_wbs_id, wbs_sort_key
│   ├── dsl.py                  # predecessor DSL parser + formatter
│   ├── dates.py                # add_working_days, working_days_between
│   ├── cascade.py              # topo sort + dependency cascade
│   ├── refs.py                 # predecessor-graph utilities (used by delete)
│   ├── auto_status.py          # derive Status from %complete + dates + preds
│   ├── critical_path.py        # CPM forward+backward, slack, critical path
│   ├── schema.py               # column layout, ARRAYFORMULA, CF/DV/border requests
│   └── sheets.py               # thin gspread wrapper for program tabs
├── tests/
│   ├── test_model.py
│   ├── test_dsl.py
│   ├── test_dates.py
│   ├── test_cascade.py
│   ├── test_refs.py
│   ├── test_schema.py
│   ├── test_sheets_helpers.py
│   ├── test_auto_status.py
│   ├── test_critical_path.py
│   └── fixtures/programs.py    # shared Program factories
└── docs/
    ├── ideas/gantt-skill-v0.5.md
    └── plans/v0.5-backlog.md
```

---

## Development

```bash
# Run the full suite (currently 179 tests):
.venv/bin/python3 -m pytest tests/ -v

# One module:
.venv/bin/python3 -m pytest tests/test_cascade.py -v

# Coverage:
.venv/bin/python3 -m pytest tests/ --cov=gantt_lib
```

Domain logic in `gantt_lib/` is pure Python — no Sheets dependency, no
network. The CLI and `gantt_lib/sheets.py` are the only places that touch
gspread.

---

## Architecture decisions

### From the v0.5 plan
- **Sheet is the artifact, plan files are the source of truth for code-shape
  decisions** — the Sheet stores the data; the CLI is the only writer that
  understands dependencies. After any human edit in the sheet, re-run `recalc`.
- **CLI-driven recalc, not Apps Script** — corp Workspace orgs block scripts,
  and re-authorizing per-viewer is friction.
- **Domain logic is pure Python**, fully unit-tested. Sheets layer is a thin
  adapter that round-trips dicts.
- **Sheet writes are batched** via gspread's `batch_update`. Conditional
  formatting and data validation use raw Sheets API request bodies.
- **Single-writer model in v0.5** — the CLI is the only writer that
  understands cascades. Other PMs view the sheet directly; if they edit a
  cell, re-run `recalc` to validate and re-cascade. Multi-PM concurrent-edit
  semantics are deferred to v1.

### Adopted during T8/T9 iteration (deviations from the v0.5 plan)
- **Calendar days, not working days**, for the timeline cells (126 cells = 18
  weeks). Weekends are visible cells shaded grey via CF; bars naturally skip
  them because cascade always lands End on a working day.
- **4 timeline header rows** (quarter / month / week-num / day) instead of one
  date header. Quarters and months are bracketed left+right with subtle and
  medium-weight borders respectively, spanning the full vertical of the tab.
- **Day cells store actual dates** (Sheets serial via `USER_ENTERED`) but
  display only the day-of-month via numberFormat pattern `d`. Required so
  `WEEKDAY` (weekend CF) and date comparisons (team-color CF for bars) work
  on real dates rather than day-of-month integers.
- **Single ARRAYFORMULA at N5** fills the entire 100×126 task region instead
  of 12,600 per-cell formulas. New tasks added inside rows 5-104 render
  automatically — `gantt task add` only writes cols A-M (13 cells) per task.
- **Status text only on the first cell of each bar** with `wrapStrategy=
  OVERFLOW_CELL`, allowing the text to overflow visually into adjacent
  team-colored cells.
- **Bars are CF-painted, not formula-rendered** — the cell content for bar
  cells is empty string; conditional formatting paints the background color
  based on the data row's Team value and a date-range check against
  Start/End. This decouples bar rendering from cell content and lets the
  team-color CF rules be the source of truth for visualization.

### Adopted during T10–T13 iteration
- **Auto-cascade on every mutation.** `task add`, `task update`, and `shift`
  invoke the recalc pipeline immediately, so dates / status / row order are
  always consistent without a separate `recalc` step. Single-step UX.
- **Auto-derived Status.** Recalc computes Status from `%complete` + start
  date + predecessor completion (Done / Not Started / Blocked / In Progress).
  Manually-set "At Risk" is overwritten — flag here if you want a carve-out.
- **WBS row sort.** Recalc detects when sheet rows aren't in WBS order and
  rewrites the entire task region in sorted order so children sit directly
  beneath their parents. Never renames ids (would break predecessor refs).
- **Row grouping.** Recalc adds Sheets native row dimension groups so L2/L3
  children become collapsible under their L1 parents.
- **Tree-style indent on Name (col C).** L1 = no prefix, L2 = `-- `,
  L3 = `---- `, etc. Stripped on read; re-applied on every write; backfilled
  on recalc when the displayed name doesn't match the level-derived form.

---

## Out of scope (v0.5)

- Portfolio rollup tab across multiple programs
- `_People` validation tab
- Schema repair (`gantt repair`)
- Natural-language disambiguation in the skill layer
- Linear / Jira sync
- Sub-day granularity, per-team calendars, multiple critical paths
- Multi-writer conflict resolution beyond "last `recalc` wins"

See `docs/ideas/gantt-skill-v0.5.md → Not Doing in v0.5` for the rationale on each.
