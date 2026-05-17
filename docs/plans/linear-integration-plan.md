# Plan: Linear Integration — Phase 1

**Spec:** [linear-integration.md](../specs/linear-integration.md)
**Phase scope:** Phase 1 only (pull Linear project into workbook tab)
**Status:** Awaiting approval before Tasks breakdown
**Date:** 2026-05-16

Phases 2-3 (push, bidirectional sync) get their own plan files when we
start them.

## Component map

| # | Component | Type | Lines (est.) | Purpose |
|---|-----------|------|--------------|---------|
| 1 | `gantt_lib/cp/__init__.py`                | new        | ~5   | Package marker |
| 2 | `gantt_lib/cp/contracts.py`               | new        | ~150 | Input/output dataclasses + JSON serde for the project-graph contract |
| 3 | `gantt_lib/cp/adapter.py`                 | new        | ~120 | Convert input JSON → `Program`; reuse `cascade.py` + `critical_path.py` unchanged; convert results back to JSON |
| 4 | `gantt_lib/linear/__init__.py`            | new        | ~5   | Package marker |
| 5 | `gantt_lib/linear/sync_tab.py`            | new        | ~180 | Read/write the `_LinearSync` hidden tab; bootstrap if absent; CRUD by `(program, linear_id)` |
| 6 | `gantt_lib/linear/pull.py`                | new        | ~280 | Orchestrator: takes Linear payload, normalizes via cp/adapter, reconciles against existing tab via sync_tab, applies conflict policy, writes program tab via existing Sheets I/O. Wraps name cells as `=HYPERLINK("<linear_url>", "<title>")` formulas (USER_ENTERED mode) with quote-escaping |
| 7 | `gantt_lib/linear_cmds.py`                | new        | ~100 | `cmd_linear_pull` handler — argparse glue, stdin parsing, dispatches to `linear/pull.py`, emits result line + JSON summary |
| 8 | `gantt` script: argparse + dispatch       | small edit | ~30  | Wire `linear-pull` subparser (--stdin, --as, --dry-run); route to `cmd_linear_pull` |
| 9 | `tests/fixtures/cp/*.json`                | new        | ~200 | 6 cp-engine fixture pairs (linear chain, fan-in, fan-out, missing-estimate, cycle, empty) |
| 10 | `tests/fixtures/linear_pull/*.json`      | new        | ~300 | 4 pull-orchestrator fixtures: first-pull, re-pull-no-changes, re-pull-with-conflicts, re-pull-with-workbook-only-rows |
| 11 | `tests/fixtures/linear_mcp/*.json`       | new (probe)| ~150 | Real captured MCP responses from a user-chosen Linear project |
| 12 | `tests/test_cp_contracts.py`             | new        | ~100 | Round-trip JSON serde; unknown-field tolerance; required-field validation |
| 13 | `tests/test_cp_adapter.py`               | new        | ~180 | Fixture-driven: cp engine correctness across 6 fixtures |
| 14 | `tests/test_linear_sync_tab.py`          | new        | ~120 | Sync tab bootstrap + CRUD via fake gspread |
| 15 | `tests/test_linear_pull.py`              | new        | ~250 | Pull orchestrator: first-pull writes tab; re-pull upserts; conflict policy honored; --dry-run doesn't write |
| 16 | `tests/test_linear_cmds.py`              | new        | ~120 | Handler tests — stdin parsing, exit codes, result-line shape |
| 17 | `skills/gantt/SKILL.md` — Linear section  | extension  | ~140 | Trigger language; MCP call sequence; normalization recipe; dry-run-then-apply convention; rendering shape |
| 18 | `README.md` — Linear section              | extension  | ~80  | "Linear integration (Phase 1, pull-only)" — what works, how it's invoked, MCP requirement, link to spec |

**Net estimate:** ~2500 LOC across 18 deliverables. Bigger than Phase 1 of
the Deck initiative (~1900) because pull involves both a new data source
(Linear via MCP) and a new persistent state surface (`_LinearSync` tab)
with reconciliation logic.

## Implementation order

```
1. cp/contracts.py + serde tests       (pure dataclasses — no engine deps)
       │
       ▼
2. cp/adapter.py + fixture tests       (engine glue — reuses cascade.py + critical_path.py)
       │
       ▼
3. linear/sync_tab.py + tests          (hidden tab bootstrap + CRUD; uses existing sheets layer)
       │
       ▼
4. linear/pull.py + fixture tests      (orchestrator — first-pull and re-pull semantics)
       │
       ▼
5. linear_cmds.py + argparse wiring    (CLI shape; `gantt linear-pull --stdin --as X` works)
       │                                  end-to-end with mock stdin)
       ▼
6. Linear MCP probe                    (capture real MCP shapes into tests/fixtures/linear_mcp/)
       │
       ▼
7. SKILL.md Linear playbook            (informed by probe — written against ground truth)
       │
       ▼
8. README + live final acceptance      (one end-to-end pull against a real Linear project)
```

Steps 1-5 are pure code; can land back-to-back without any live MCP
involvement. Step 6 is a one-shot exploratory probe. Step 7 depends on
6's findings. Step 8 is the acceptance gate.

## Risks and mitigations

### High

**R1. Linear MCP `list_issues` shape is unknown.** The MCP tool inventory
lists `list_issues` and `get_issue` but doesn't document whether
`list_issues` returns `blockedBy` / `relations` inline. If not, every
issue with relationships needs a separate `get_issue` call — O(N) MCP
turns per project, materially affecting latency.

Mitigation: probe before designing (Task T6). Captured fixtures inform
the SKILL.md playbook. If the answer is O(N), the playbook documents
the cost honestly and the user sees it coming on first pull.

**R2. `_LinearSync` tab consistency.** If a pull is interrupted mid-write
(network blip, OAuth expiry, Sheets quota), the program tab and the
sync tab can drift — e.g., a row written to the program tab but no
corresponding `_LinearSync` row, or vice versa. Future pulls would then
either skip the orphan or duplicate it.

Mitigation:
- Write the program tab first, then `_LinearSync`. A failure between
  the two leaves the program tab with new rows that have no linkage —
  on re-pull, those rows look like "workbook-only" and are preserved
  (no harm, just no auto-update). Better than the reverse.
- After every pull (success or partial), emit a final stderr line
  summarizing what was written. If it doesn't match the input
  expectation, surface a clear "partial pull — re-run to reconcile"
  warning.

**R3. Re-pull conflict policy correctness.** The conflict table in the
spec is opinionated; an unexpected case (e.g., user manually deleted
a workbook row that's still active in Linear) could produce surprising
behavior.

Mitigation:
- Pull is **dry-run-first by default** (SKILL.md playbook). The user
  sees the diff before applying.
- Test coverage in T15 covers each conflict policy row explicitly with
  a dedicated fixture, plus the edge cases: deleted-in-workbook,
  added-in-workbook-only, deleted-in-linear, milestone-vs-issue swap.
- `--dry-run` is also a top-level CLI flag (not just a SKILL.md
  convention) so scripted invocations can preview safely.

### Medium

**R4. WBS id assignment on first pull.** Linear has flat issue lists +
parent/sub-issue hierarchy. We need a deterministic WBS-id assignment
that's stable across pulls (so re-pulls don't accidentally renumber
everything).

Mitigation:
- First-pull algorithm: assign sequential `1, 2, 3...` to top-level
  issues in Linear's `sortOrder`; sub-issues get `parent.1, parent.2`
  recursively.
- WBS id is then persisted in `_LinearSync`; re-pulls match by
  `linear_id` and reuse the existing WBS id.
- New issues added after first pull get the next free top-level id
  (or the next free sub-id under their parent), never recycling.

**R5. Linear team estimate units vary.** Teams can use points, hours,
exponential, t-shirt sizes, or none. The spec assumes the agent reads
the team's estimate unit and converts to days in the normalization step.

Mitigation:
- Agent reads team estimate unit via `list_teams` / `get_team`.
- Default conversions: points → days (1:1), hours → days (8:1),
  exponential → days (map 1→1, 2→2, 3→3, 5→5, 8→8, 13→13), t-shirt →
  days (XS=1, S=2, M=3, L=5, XL=8), none → fallback default.
- Conversion is surfaced in the dry-run output so the user can
  confirm before applying.

**R6. Linear custom workflow states.** Custom states fall back to
`Not Started` per the spec. For a team with mostly-custom states, the
pull would mis-categorize most issues.

Mitigation:
- Agent surfaces an "Unmapped states" warning listing custom states
  encountered + their fallback. User adds mappings in
  `~/.config/gantt/config.json` (Phase 2+ formalizes; for Phase 1, the
  agent reads the block if present).
- Test in T15: re-pull preserves workbook-set status if it diverges
  from the (still-fallback) Linear state — conflict policy gives
  workbook precedence on status corrections.

**R7a. Sheets HYPERLINK formula correctness.** Wrapping the name cell
as `=HYPERLINK(...)` requires (a) `value_input_option=USER_ENTERED` on
the gspread write (RAW would write the literal text), and (b) escaping
double quotes in the title (Sheets uses `""` for a literal `"` inside a
string). Titles with unbalanced quotes or special characters could
produce broken formulas that show as `#ERROR!` in the cell.

Mitigation:
- Confirm the existing sheets write path uses `USER_ENTERED` (or
  override locally for pull writes). Audited at T4 implementation.
- Quote-escape titles via `title.replace('"', '""')` before formula
  construction.
- T4 fixture suite includes one fixture with a title containing
  double quotes, an em-dash, and a Unicode emoji — round-trip
  confirms the rendered text and the clickable URL both survive.

**R7. Hidden tab discoverability.** The `_LinearSync` tab is hidden so
it doesn't clutter the Sheet UI. But "hidden" in Sheets means "still
in the workbook, accessible via the right-click → Show Hidden Tabs
menu" — not invisible. Users may stumble on it and edit it manually,
which would corrupt linkage.

Mitigation:
- First row (header row) explicitly says:
  `DO NOT EDIT — Managed by gantt linear-pull. Edits will be overwritten on next pull.`
- Pull validates the tab schema on every read; if a column is renamed
  or rows are deleted, refuse the pull with a clear error pointing to
  the tab + recovery steps (re-create from scratch via `--force`).

### Low

**R8. Stdin size for very large projects.** A 500-issue project is
maybe ~300 KB of JSON on stdin. Python's stdin handling is fine well
past 10 MB.

**R9. Concurrent Linear edits during a pull.** Same caveat as
read-only CP: result reflects a fetch-time snapshot. Phase 1 surfaces
the snapshot timestamp in the rendered output.

**R10. Agent over-rendering.** SKILL.md constrains rendered output to a
fixed table shape + warnings. No deviation.

## Verification checkpoints

| After step | Verification | Pass criterion |
|---|---|---|
| 1-2 (cp engine)   | `pytest tests/test_cp_contracts.py tests/test_cp_adapter.py -q` | All green; 6 cp fixtures round-trip |
| 3 (sync_tab)      | `pytest tests/test_linear_sync_tab.py -q`                       | All green; bootstrap + CRUD covered |
| 4 (pull)          | `pytest tests/test_linear_pull.py -q`                           | All green; 4 pull fixtures pass; conflict policy honored |
| 5 (cmds + CLI)    | Subprocess: `cat tests/fixtures/linear_pull/first_pull.json \| gantt linear-pull --stdin --as TEST --dry-run` | Exits 0; emits expected JSON summary; result line on stderr |
| 6 (MCP probe)     | Captured fixtures exist, valid JSON, user-reviewed for sensitive content | Notes doc answers shape + estimate + state questions |
| 7 (SKILL.md)      | Mental dry-run of playbook against captured MCP fixtures        | Steps produce a payload that round-trips through CLI |
| 8 (live)          | End-to-end against a real Linear project                        | Program tab written, `_LinearSync` populated, `critical-path <program>` works on pulled data, `--dry-run` first showed diff |

**Final acceptance gate (user-driven):** one live run against the user's
real Linear project. Confirm:
- Dry-run output shows a sensible diff
- Apply produces a program tab whose tasks match Linear
- `gantt critical-path <program>` on the new tab returns the right CP
- `gantt deck --program <program>` generates a deck section
- Re-running `linear-pull` shows "0 added, 0 updated, N unchanged"
- Manually editing one workbook field (e.g., set `--percent 50` on a
  task) and re-pulling preserves the workbook value (per conflict policy)

If issues: file in `docs/issues/` and iterate.

## What's NOT in scope (Phase 1)

All explicitly Phase 2+:
- `gantt linear-push` — push workbook back to Linear
- `gantt linear-sync` — bidirectional sync
- Sidecar block in Linear issue descriptions (only needed when pushing
  workbook-only fields back to Linear, which is Phase 2's job)
- Visible inline Linear-id on program tab rows (e.g., Notes-column
  prefix or name hyperlink) — easy Phase 2+ UX add if wanted
- Per-team estimate config in `~/.config/gantt/config.json` formalized
  schema (Phase 1 reads it if present, but doesn't define a strict shape)
- Custom workflow state mappings formalized (same — reads if present)
- Multi-team projects, Linear Cycles, comments, attachments

## Decisions needed before Tasks breakdown

None blocking. Three micro-decisions I'm making in the Tasks phase
without re-asking — flag if any are wrong:

1. **`_LinearSync` tab is hidden, not deleted-on-bootstrap.** First
   pull creates it; subsequent pulls reuse. `gantt linear-pull --force`
   rebuilds it from scratch (drops existing linkage; treats every Linear
   issue as new).
2. **Dry-run is opt-in via flag, but SKILL.md playbook makes it the
   default agent behavior.** User who runs the CLI directly without
   `--dry-run` gets an immediate write. User via `/gantt` slash command
   always sees a diff preview first.
3. **WBS-id assignment uses Linear's `sortOrder`**, not creation date or
   alphabetical. Matches what users see in the Linear UI.
4. **Task name cells are hyperlinked to the Linear issue.** Wrapped as
   `=HYPERLINK(linear_url, title)` on pull writes. The `Task` dataclass
   gains a non-column metadata field `linear_url: Optional[str]` used
   only at write-time; column count stays at 13. Round-trip safe (gspread
   renders the displayed text on read). Known limitation: if the user
   later edits the name via `gantt task update --name`, the hyperlink is
   lost until next pull — documented in the spec as accepted.
