# `gantt` skill — refined plan (v0.5 thin slice)

## Problem Statement
How might Jason maintain credible program plans with auto-cascading
dependency math in Google Sheets — without rebuilding Microsoft Project
in v1 or pretending the multi-PM sharing story is solved?

## Recommended Direction
Build **v0.5 first**: one program, one workbook, CLI-driven cascade +
critical path, Sheet is writable (accept stale-until-recalc). No portfolio
rollup, no NL skill, no multi-PM contract.

The dependency engine is the load-bearing technical bet and the demo-able
differentiator. Build it well, dogfood it on one real program (90-day plan
for next role / ultra training block / job search tracker), and only then
invest in portfolio + sharing.

The original v1 proposal becomes the v1 milestone — ship it after v0.5
proves the cascade workflow is one Jason actually uses.

## Key Assumptions to Validate
- [ ] **The "edit cells, run `gantt recalc`" workflow is one Jason will use** —
      not abandon for hand-maintained dates after week 2.
      → Test: dogfood on one real program for 2 weeks before building v1 features.
- [ ] **`gspread` round-trip latency is acceptable** (a few seconds for
      a 50-row table is OK; 30 seconds is not).
      → Test: build the read/write path first, time it on a 100-row table.
- [ ] **Predecessor DSL `1.2FS+3` is parseable enough that I'll actually use it**
      vs. always reverting to manual dates.
      → Test: write a real 30-task plan in YAML/CSV first, before Sheet UI.
- [ ] **The formula-rendered timeline grid survives Google Sheets' quirks**
      (column insertion, copy/paste, mobile view).
      → Test: build the grid early; share with one trusted PM friend; ask them to break it.

## v0.5 MVP Scope (in)
1. **Bundle skeleton** matching `daily-ops` pattern: `~/.local/bin/gantt`,
   `~/.config/gantt/{credentials,token,config}.json`, bundle-local `.venv`.
2. **`gantt setup` + `gantt bootstrap`** — venv install, OAuth, create one workbook.
3. **`gantt program new <name>`** — single program tab with the column schema
   from the proposal (A–M data, N+ formula timeline).
4. **`gantt task add` / `update` / `delete`** — CLI mutations to the data table.
5. **`gantt recalc <program>`** — topo sort, working-days math, FS/SS/FF/SF +
   lag DSL, idempotent rewrite of Start/End columns.
6. **`gantt critical-path <program>`** — compute slack, mark zero-slack tasks
   in a new column, optional bold conditional formatting.
7. **`gantt shift <program> <id> ±Nd`** — shift one task, cascade, report diff.
8. **Pytest suite** for: WBS sort, working-days math, predecessor DSL parser,
   topo sort + cascade, critical path on a fixture program.
9. **Skill `SKILL.md`** — minimal: maps the 5 commands above to natural language,
   no portfolio/NL-disambiguation cleverness yet.

## Not Doing in v0.5 (and Why)
- **Portfolio rollup tab** — can't build the rollup until you have ≥2 programs
  you actually maintain. Speculative until then.
- **Multi-PM sharing contract** — view-only sharing works in v0.5. "Other PMs
  edit safely" is a real distributed-systems problem; defer to v1 with explicit lock semantics.
- **`_People` validation tab** — until you have a real cross-team plan with
  multiple owners, validation is overhead.
- **Schema repair (`gantt repair`)** — only matters once shared editing is real.
  In v0.5, you're the only writer; if you break it, fix it manually.
- **NL natural-language skill cleverness** (e.g. "push the data pipeline
  integration milestone out two weeks" → resolve program + task + units) —
  the NL→CLI mapping is its own project. Start with `/gantt` slash-command + literal subcommands.
- **Linear/Jira sync** — already correctly deferred in v1 proposal; mention only to keep the door closed.
- **Apps Script** — already correctly rejected.
- **Sub-day granularity / per-team calendars / multi-critical-path** — YAGNI for v0.5.

## Locked Decisions (2026-05-12)
1. **Build location during v0.5**: `~/Documents/gantt-chart/`. Promote to
   `~/Documents/my-claude-tools/tools/gantt/` (with `claude-tool` manifest)
   only after the cascade workflow is dogfooded.
2. **First dogfood program**: 90-day plan for next TPM role. Has real
   dependencies, real slip risk, and forces the cascade engine to earn
   its keep immediately.
3. **Predecessor DSL**: compact form `1.2FS+3`. Jason is the only writer
   in v0.5; readability for other PMs is a v1 concern.
4. **Workbook ownership**: `gantt bootstrap` creates a new workbook named
   `Jason — Program Portfolio`. `gantt link <url>` deferred to v1.

## Path from v0.5 → v1
After 2 weeks of dogfooding v0.5 on one real program:
- If cascade workflow holds up → build portfolio rollup + multi-program.
- If you find yourself avoiding `recalc` → revisit Option B (YAML-as-source).
- If a real PM friend asks to edit → build schema repair + lock semantics.
- If you want NL queries ("who's on the critical path?") → build the skill layer.

Each of those is a separate v1 milestone, scoped after v0.5 proves the bet.
