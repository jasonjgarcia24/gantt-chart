# gantt

A Python CLI + Claude skill for creating, editing, and maintaining program
Gantt charts in Google Sheets. The Sheet is the artifact PMs share; the CLI
does the dependency math (topological sort, working-days arithmetic, FS/SS/FF/SF
relations, critical path) and writes the results back.

Modeled on the `daily-ops` bundle pattern: skill-local venv, user state under
`~/.config/gantt/`, OAuth-backed Sheets writes, one verified result line per command.

The CLI is **bundled inside the Claude skill** at `skills/gantt/scripts/gantt`
— there is no `gantt` on `$PATH`. You drive it through the `/gantt` slash
command; the skill resolves the bundled binary and invokes it for you.

---

## Status — v0.5 thin slice

| Phase | Scope | State |
|---|---|---|
| 1. Foundation | `setup`, `bootstrap`, `info` | ✅ shipped |
| 2. Domain logic | model, DSL parser, working-days math, cascade engine | ✅ shipped |
| 3. First user-visible program | `program new`, `task add/update/delete` | ✅ shipped |
| 4. Cascade wiring | `recalc` (reads tab, cascades, writes back) | ✅ shipped |
| 5. Critical path + shift | `critical-path`, `shift` | ✅ shipped |
| 6. Skill surface | `SKILL.md`, `/gantt` slash command | ✅ shipped |
| 7. Plugin lifecycle | `.claude-plugin/`, `/gantt:init`, `--remove` | ✅ shipped (canonical install dance below) |

Test suite: 358 tests across `model`, `dsl`, `dates`, `cascade`, `refs`, `schema`, `sheets_helpers`, `auto_status`, `critical_path`, baseline tracking, deck generation, narrative LLM client, and `.env` loader.

See `docs/ideas/gantt-skill-v0.5.md` for the v0.5 charter and `docs/plans/v0.5-backlog.md`
for the full task breakdown.

---

## Quick Start

> **Before you start:** this plugin needs a Google OAuth Desktop client (Sheets + Drive scopes) to authenticate against your Google account. You can set that up during `/gantt:init` (Gate 6 walks you through Cloud Console), or pre-create it from [console.cloud.google.com](https://console.cloud.google.com) → APIs & Services → Credentials → OAuth client ID → Desktop app, and drop `credentials.json` into `~/.config/gantt/` first. Python 3.10+ also required.

<details>
<summary><b>Claude Code — Marketplace (recommended)</b></summary>

```
/plugin marketplace add jasonjgarcia24/gantt-chart
/plugin install gantt@jason-gantt
/reload-plugins
/gantt:init
```

The first two add the marketplace and install the plugin; the third reloads the current session so the new commands are callable without restarting Claude Code; the fourth runs first-run setup (7 gates: Python, short-form alias, Claude Code permissions, skill-local venv, OAuth credentials, workbook bootstrap, final read-back). `/gantt:init` is idempotent — re-running it only fixes what's missing.

To pull a newer version later: **uninstall first then reinstall** (Claude Code's `/plugin install` skips already-installed plugins, so a vanilla rerun won't pick up upstream changes):

```
/plugin marketplace update jason-gantt
/plugin uninstall gantt@jason-gantt
/plugin install gantt@jason-gantt
/reload-plugins
/gantt:init
```

> **Two ways to invoke `gantt`.** `/gantt:gantt` is the plugin-namespaced form (always available after install). `/gantt` is the short form — during `/gantt:init`, a user-level symlink is installed at `~/.claude/commands/gantt.md` → the plugin's `commands/gantt.md`, so both resolve to the same file with no drift. (Init itself stays namespaced — `/gantt:init` only — because Claude Code has a built-in `/init` command for CLAUDE.md initialization that the short form would collide with.)

> **SSH errors?** The marketplace clones repos via SSH. If you don't have SSH keys set up on GitHub, either [add your SSH key](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/adding-a-new-ssh-key-to-your-github-account) or switch to HTTPS for fetches only:
> ```bash
> git config --global url."https://github.com/".insteadOf "git@github.com:"
> ```

</details>

<details>
<summary><b>Uninstall</b></summary>

Three steps. The first cleans up the user-level shims init created; the next two remove the plugin and marketplace.

```
/gantt:init --remove
/plugin uninstall gantt@jason-gantt
/plugin marketplace remove jason-gantt
```

**`--remove` removes** (only if they exist and point at gantt-chart paths):

- `~/.claude/commands/gantt.md` — the short-form `/gantt` alias
- `~/.local/bin/gantt` — legacy CLI symlink from pre-self-contained-skill installs (no longer created by setup; cleaned up if found)

**`--remove` does NOT touch** (manage these yourself if you want full cleanup):

- `~/.config/gantt/credentials.json` — OAuth client (sensitive — keeping it means no new Cloud Console setup if you reinstall)
- `~/.config/gantt/token.json` — OAuth refresh token (sensitive)
- `~/.config/gantt/config.json` — Sheet id + URL — deleting forces re-bootstrap (creates a NEW workbook; old one stays in your Drive)
- `~/.claude/settings.json` — has `gantt` permissions from `--init` Gate 3
- Your portfolio workbook in Google Drive — `--remove` never touches Drive content
- The skill-local `.venv` at `<plugin>/skills/gantt/.venv` — gets removed when `/plugin uninstall` runs

For full cleanup, manually:

```bash
rm -rf ~/.config/gantt
# Edit ~/.claude/settings.json to remove the gantt permissions block (back up first)
# Delete the portfolio workbook from Google Drive UI if unwanted
```

</details>

<details>
<summary><b>Claude Code — Local / development clone</b></summary>

Useful if you want to edit the plugin in place and see changes without reinstalling.

```bash
git clone https://github.com/jasonjgarcia24/gantt-chart.git ~/code/gantt-chart
claude --plugin-dir ~/code/gantt-chart
```

</details>

<details>
<summary><b>Manual install (no plugin marketplace)</b></summary>

Bolt-on to an existing Claude Code config without the marketplace:

```bash
git clone https://github.com/jasonjgarcia24/gantt-chart.git ~/gantt-chart
cd ~/gantt-chart

# Skill-local venv + dependencies
./skills/gantt/scripts/gantt setup

# Skill — symlink the skill directory so Claude can discover it
mkdir -p ~/.claude/skills
ln -sf "$PWD/skills/gantt" ~/.claude/skills/gantt

# Slash command — short form so /gantt routes to NL handling
mkdir -p ~/.claude/commands
ln -sf "$PWD/commands/gantt.md" ~/.claude/commands/gantt.md
```

The CLI lives inside the skill bundle and isn't installed on `$PATH` — drive
it through `/gantt` or `/gantt:gantt` from inside Claude Code.

Merge the plugin's permissions into `~/.claude/settings.json`:

```bash
cp ~/.claude/settings.json ~/.claude/settings.json.bak
jq -s '
  (.[0].permissions.allow // []) as $a
  | (.[1].permissions.allow // []) as $b
  | .[0] * .[1]
  | .permissions.allow = ($a + $b | unique)
' ~/.claude/settings.json settings.fragment.json \
  > /tmp/settings.json && mv /tmp/settings.json ~/.claude/settings.json
```

(The naive `jq '.[0] * .[1]'` form replaces arrays rather than concatenating them, which silently drops any existing `permissions.allow` entries. The form above concatenates and dedupes.)

Then complete OAuth + workbook bootstrap:

```bash
gantt bootstrap
```

</details>

<details>
<summary><b>OAuth setup (required once, any install method)</b></summary>

Needed because the skill's `bootstrap` step writes to a Google Sheet on your behalf.

1. Go to [console.cloud.google.com](https://console.cloud.google.com) → create a new project (e.g. `gantt`) or reuse an existing one.
2. APIs & Services → Library → enable **Google Sheets API**, **Google Drive API**, AND **Google Slides API** (Slides is needed for deck generation).
3. APIs & Services → OAuth consent screen → External → add yourself as a Test user → add scopes `https://www.googleapis.com/auth/spreadsheets`, `https://www.googleapis.com/auth/drive.file`, and `https://www.googleapis.com/auth/presentations`.
4. APIs & Services → Credentials → Create Credentials → OAuth client ID → **Desktop app** → download the JSON.
5. Drop into place:
   ```bash
   mkdir -p ~/.config/gantt
   mv ~/Downloads/credentials.json ~/.config/gantt/credentials.json
   ```
6. Run `/gantt:init` from Claude Code. Gate 6 will trigger the bootstrap (opens a browser for first-time consent) and create the workbook.

After bootstrap, `/gantt info` (or `/gantt show me the workbook URL`) reports the sheet URL, OAuth state, and venv status.

State lives in `~/.config/gantt/`:
- `credentials.json` — OAuth 2.0 Desktop client (sensitive)
- `token.json` — refresh token after first consent (0600)
- `config.json` — sheet id + URL after bootstrap

Override the credentials path with `GANTT_CREDS=/path/to/credentials.json` if you want to keep them elsewhere.

</details>

---

## Usage (commands shipped so far)

> **You drive these through the `/gantt` slash command, not from a shell.**
> The CLI examples below (`./gantt program new TPM90` etc.) are the literal
> commands the skill constructs and runs against the bundled binary at
> `skills/gantt/scripts/gantt`. They're documented here so you can reason
> about what the skill is doing — but you wouldn't type them yourself.
> Talk to `/gantt` in natural language and it translates.

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

### Baseline tracking

A baseline freezes the current `(start, end, duration, predecessors)` of every
task as the plan-of-record. Once captured, `gantt baseline show` reports per-task
slip and a delta on critical-path duration — the headline number leadership
asks for ("are we on track vs. what we committed to?").

Snapshots live in a workbook-level `_Baselines` tab (auto-created on first
snapshot). The tab is append-only — every snapshot is preserved as audit
history. The *active baseline* for any task is the most recent row.

```bash
# Freeze TPM90's current dates as the baseline of record.
./gantt baseline snapshot --program=TPM90
# → gantt: baseline snapshot — TPM90, 12 tasks, 2026-05-14 ✓
# Optional flags: --label="Q2 plan freeze", --actor=jason.garcia
# --all snapshots every program in the workbook in one pass.

# Re-running on a program that already has a baseline refuses (exit 1):
./gantt baseline snapshot --program=TPM90
# → gantt: baseline already exists for TPM90 (last snapshot 2026-05-14).
#          Use --rebaseline to replace the slip-comparison anchor.
#          History is always preserved. ✗

# Move the slip anchor to today's plan; old snapshots stay in _Baselines.
./gantt baseline snapshot --program=TPM90 --rebaseline

# Summary block + top 10 slippers by abs end-slip (default).
./gantt baseline show --program=TPM90
# Other detail modes:
#   --top=5             top N slippers
#   --slipping-only     every task with end-slip > 0
#   --all               every baselined task, sorted by WBS
#   --milestones-only   filter detail to milestones (combinable)
#   --critical-path-only  filter detail to critical-path tasks (combinable)
# Portfolio rollup — summary per program, no detail tables:
./gantt baseline show --all-programs

# Destructive — refuses without --force; deletes every _Baselines row for
# the named program. The next snapshot starts a new history.
./gantt baseline clear --program=TPM90 --force
```

`gantt info` also reports baseline coverage per program after the local
diagnostic block (sheet URL, paths, venv) — `K/N tasks baselined, last
snapshot YYYY-MM-DD by <actor>` or `baseline none`.

Full spec: [`docs/specs/baseline-tracking.md`](docs/specs/baseline-tracking.md).

### Deck generation

Audience-targeted Google Slides decks generated from the workbook + active
baselines. Two distinct cognitive frames:

- **Tactical** (Engineering / ICs) — *what's on my plate this week, who am
  I blocked by, what just shipped*. Per-program detail; embeds a
  matplotlib gantt-zoom for the next 30 days.
- **Strategic** (EM / TPM / Leadership) — *are we hitting milestones,
  what slipped, where's the risk*. Portfolio rollup; tables + a top-risks
  bullet list.

Each invocation appends a date-stamped **section** (1 divider + 5 content
slides) to a yearly file — one URL per audience per year, with built-in
audit history. Yearly files are stored under
`~/.config/gantt/config.json` in the new `decks:` block.

```bash
# Tactical — per-program (one of --program OR --all required)
./gantt deck --audience=tactical --program=TPM90
./gantt deck --audience=tactical --all                # one section per program

# Strategic — defaults to portfolio rollup
./gantt deck --audience=strategic
./gantt deck --audience=strategic --program=TPM90     # single-program leadership view
./gantt deck --audience=strategic --all-programs      # explicit form of default

# All commands return:
#   gantt: deck appended — <url>#slide=id.<divider_id> ✓
# Click the URL — it deep-links straight to the new section divider.
```

**OAuth scope upgrade**: the first deck command after install triggers an
extra OAuth re-consent flow because the Slides API needs the
`presentations` scope (Phase 1 only had Sheets + drive.file). gantt prints
a clear message before the browser opens — same Google account, ~30s.

**Embedded chart images**: the tactical gantt-zoom slide embeds a PNG
that gantt uploads to your Drive root with public-link permissions
(`anyone with link, view-only`). Anyone with the (unguessable) URL can
view the chart. Files persist forever; manual cleanup via Drive search
for `gantt-deck-image-` prefix.

`gantt info` lists the yearly deck URLs after the Programs block:

```
Decks:
  strategic 2026: https://docs.google.com/presentation/d/...
  tactical 2026:  https://docs.google.com/presentation/d/...
```

Full spec: [`docs/specs/deck-generation.md`](docs/specs/deck-generation.md).

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
├── pyproject.toml              # pytest config
├── conftest.py                 # adds skills/gantt/scripts to sys.path for tests
├── settings.fragment.json      # Bash(*/skills/gantt/scripts/gantt:*) for /gantt:init Gate 3
├── README.md
├── LICENSE
├── .gitignore
├── .claude-plugin/
│   ├── plugin.json             # plugin manifest (name=gantt)
│   └── marketplace.json        # marketplace manifest (name=jason-gantt)
├── commands/
│   ├── gantt.md                # /gantt:gantt + short-form /gantt slash command
│   └── init.md                 # /gantt:init (Setup + Cleanup modes)
├── skills/
│   └── gantt/                  # SELF-CONTAINED SKILL BUNDLE — drop the whole folder anywhere
│       ├── SKILL.md            # Claude skill — natural-language → bundled CLI routing
│       ├── .venv/              # skill-local venv (created by `gantt setup`; gitignored)
│       └── scripts/
│           ├── gantt           # CLI entry point (executable, with venv self-exec trampoline)
│           ├── requirements.txt
│           └── gantt_lib/      # pure-Python domain logic + Sheets I/O wrappers
│               ├── model.py            # Task, Program, Status, next_wbs_id, wbs_sort_key
│               ├── dsl.py              # predecessor DSL parser + formatter
│               ├── dates.py            # add_working_days, working_days_between
│               ├── cascade.py          # topo sort + dependency cascade
│               ├── refs.py             # predecessor-graph utilities (used by delete)
│               ├── auto_status.py      # derive Status from %complete + dates + preds
│               ├── critical_path.py    # CPM forward+backward, slack, critical path
│               ├── schema.py           # column layout, ARRAYFORMULA, CF/DV/border requests
│               ├── sheets.py           # thin gspread wrapper for program tabs
│               ├── env_file.py         # ~/.config/gantt/.env loader (ANTHROPIC_API_KEY etc.)
│               ├── baseline.py         # BaselineRow, slip math, summary, baseline-CP
│               ├── baseline_io.py      # gspread wrapper for the _Baselines tab
│               ├── baseline_cmds.py    # cmd_baseline_{snapshot,show,clear} handlers
│               ├── deck/               # Phase 2/3 — deck generation package
│               │   ├── data.py         # 10 selection functions (5 tactical + 5 strategic)
│               │   ├── charts.py       # matplotlib gantt-zoom PNG renderer
│               │   ├── slides_io.py    # Slides + Drive API wrappers + decks config
│               │   ├── templates.py    # slide template builders + section orchestrators
│               │   └── narrative.py    # Anthropic SDK section summaries (Haiku default)
│               └── deck_cmds.py        # cmd_deck handler (audiences + program scoping)
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
│   ├── test_baseline.py        # pure-logic tests for baseline.py
│   ├── test_baseline_io.py     # row parse/serialize tests
│   ├── test_baseline_cmds.py   # handler tests via FakeSpreadsheet
│   ├── test_deck_data.py       # 10 selection functions
│   ├── test_deck_charts.py     # matplotlib PNG smoke tests
│   ├── test_deck_slides_io.py  # config helpers (file-only)
│   ├── test_deck_templates.py  # request shape + object-id uniqueness
│   ├── test_deck_cmds.py       # handler tests via FakeSlides+FakeDrive
│   └── fixtures/
│       ├── programs.py         # shared Program factories
│       ├── baselines.py        # BaselineRow factory
│       ├── fake_workbook.py    # in-memory gspread fakes
│       └── fake_slides.py      # Slides + Drive API fakes
└── docs/
    ├── ideas/gantt-skill-v0.5.md
    ├── issues/                  # incident write-ups (e.g. baseline-clear-quota.md)
    ├── specs/
    │   ├── baseline-tracking.md
    │   └── deck-generation.md
    └── plans/
        ├── v0.5-backlog.md
        ├── baseline-tracking-plan.md
        ├── baseline-tracking-tasks.md
        ├── deck-generation-plan.md
        └── deck-generation-tasks.md
```

---

## Development

The venv lives inside the skill bundle at `skills/gantt/.venv/`. The
top-level `conftest.py` prepends `skills/gantt/scripts/` to `sys.path` so
tests at the repo root can `import gantt_lib.*` without being skill-aware.

```bash
# Run the full suite (currently 358 tests):
skills/gantt/.venv/bin/python3 -m pytest tests/ -v

# One module:
skills/gantt/.venv/bin/python3 -m pytest tests/test_cascade.py -v

# Coverage:
skills/gantt/.venv/bin/python3 -m pytest tests/ --cov=gantt_lib
```

Domain logic in `skills/gantt/scripts/gantt_lib/` is pure Python — no Sheets
dependency, no network. The CLI and `gantt_lib/sheets.py` are the only
places that touch gspread.

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
