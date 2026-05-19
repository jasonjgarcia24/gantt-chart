# Linear ↔ Workbook field mapping

Inventory of every field on both sides of the sync, what's currently wired, and where the gaps are.

Last updated: 2026-05-18 (End ↔ dueDate now fully bidirectional via 3-way merge).

Companion docs:
- `linear-mcp-shapes.md` — read-side MCP payload shapes
- `linear-mcp-write-shapes.md` — write-side `save_issue` response shapes (P2-T8 probe)
- `../specs/linear-integration.md` — Phase 2 spec (3-way merge, sidecar columns)

---

## Side A — Workbook fields

### Program-tab columns (per task row)

Defined in `model.Task` (`skills/gantt/scripts/gantt_lib/model.py`) and rendered through `sheets/schema.py`.

| Col | Field | Type | User-set vs. computed |
|-----|-------|------|-----------------------|
| A | `ID` (WBS) | str | User-set / assigned at create |
| B | `Level` | int | Derived from WBS depth |
| C | `Name` | str | User-set; rendered as `=HYPERLINK(linear_url, name)` if linked |
| D | `Owner` | str | User-set |
| E | `Team` | str | User-set (workbook-only metadata) |
| F | `Start` | date | **Computed** by cascade (unless explicit anchor) |
| G | `End` | date | **Computed** by cascade (unless explicit anchor) |
| H | `Duration` | int (days) | User-set |
| I | `% Complete` | int (0–100) | User-set |
| J | `Status` | enum: `Not Started / In Progress / Blocked / At Risk / Done / Cancelled` | User-set, can also be cascaded by `auto_status` |
| K | `Predecessors` | str (DSL: `"1FS+3, 2.1SS"`) | User-set |
| L | `Milestone?` | bool | User-set |
| M | `Notes` | str | User-set |

### `_LinearSync` sidecar/snapshot columns (per linked row)

Defined in `linear/sync_tab.SyncLink` + `SYNC_HEADERS` (18-col Phase-2 schema).

| Col | Field | Purpose |
|-----|-------|---------|
| A | `program` | Program tab name |
| B | `wbs_id` | Foreign key into program tab |
| C | `linear_id` | Foreign key into Linear (e.g. `JAS-10`, or `MS-…` synthetic) |
| D | `last_synced` | ISO timestamp of last successful sync |
| E | `linear_url` | Cached for hyperlink rendering |
| F | `sidecar_predecessors` | Workbook predecessor DSL (workbook-wins; not pushed verbatim — translated to `blockedBy`) |
| G | `sidecar_percent` | Workbook %-complete (workbook-wins; never pushed) |
| H | `sidecar_notes` | Workbook notes (workbook-wins; never pushed) |
| I | `sidecar_team` | Workbook team (workbook-wins; never pushed) |
| J–R | `snapshot_*` | Frozen copy of last-known Linear state for 3-way merge: `title, state, assignee, estimate, parent_id, blockedby, due_date, milestone_id, updated_at` |

---

## Side B — Linear fields (issue + milestone)

Captured across `tests/fixtures/linear_mcp/save_issue_*.json` and `linear/snapshot.IssueSnapshot`.

| Field | Type | Status | Notes |
|-------|------|--------|-------|
| `id` | str | **consumed** | Primary key |
| `title` | str | **consumed** | Pull/push/merge/snapshot |
| `state` (nested) / `statusType` | obj `{name, type}` / enum | **consumed** | `type` ∈ `backlog/unstarted/started/completed/canceled` |
| `assignee` | obj `{email, displayName}` or null | **consumed** | Pull/push/merge/snapshot |
| `dueDate` | ISO date | **consumed** | Pull/push/merge/snapshot — bidirectional, `LINEAR_WINS` on conflict |
| `estimate` | obj `{value, name}` or null | **consumed** | Points-based; merge has special policy |
| `parentId` | str or null | **consumed** | Pull/push/merge/snapshot |
| `relations.blockedBy` | `[{id, title}]` | **consumed** | Pass-2 reconciliation; append/remove via MCP |
| `url` | str | **consumed** | Cached → workbook `HYPERLINK` formula |
| `createdAt` / `updatedAt` | ISO timestamp | **consumed** (snapshot only) | Used for change detection |
| `milestone` | obj `{id, name}` or null | **partial** | Snapshot captured; not merged or pushed |
| `project` | obj | metadata | Captured in CpInput, not synced per-row |
| `team` | obj | metadata | Captured in CpInput config, not synced per-row |
| `gitBranchName` | str | **ignored** | Auto-generated; candidate for Phase 2+ PR linking |
| `labels` | `[{id, name, color}]` | **ignored** | Candidate for Team mapping in Phase 2+ |
| `cycle` | obj or null | **ignored** | Not in scope |
| `priority` | obj `{value, name}` | **ignored** | Not in scope |
| `description` | str (markdown) | **ignored** | Workbook has no rich-text field |
| `startedAt / completedAt / canceledAt / archivedAt` | ISO timestamp | **ignored** | Auto-populated on state transitions |
| `createdBy` | obj | **ignored** | No collaboration model in workbook |
| `subscribers` | array | **ignored** | — |
| `attachments` | array | **ignored** | — |
| `comments` | array | **ignored** | — |

---

## What's wired (current Phase 2 v1)

| Workbook field | Linear field | Direction | Merge policy | Where |
|----------------|--------------|-----------|--------------|-------|
| `Name` | `title` | both | `LINEAR_WINS` | `merge.MERGEABLE_FIELDS`, `_push_field_kwargs` |
| `Status` | `state.name` (+ `statusType`) | both | `LINEAR_WINS` | merge + push; state name resolved at sync time via `list_issue_statuses` |
| `Owner` | `assignee.email` (or displayName) | both | `LINEAR_WINS` | case-insensitive equality in `snapshot.assignees_equal` |
| `Duration` | `estimate.value` | both | Special: workbook wins if non-zero, else Linear wins | `resolve_conflict()` in `merge.py` |
| `Predecessors` (DSL) | `relations.blockedBy[].id` | both | `WORKBOOK_WINS` | DSL parsed → flat `blockedBy` list; FS+0 only (lossy on lags/SS/SF) |
| `Parent` (from WBS depth) | `parentId` | both | `LINEAR_WINS` | Computed from WBS hierarchy |
| `End` | `dueDate` | both | `LINEAR_WINS` | Treated as the same field; goes through normal 3-way merge. Workbook cascade still owns End locally between syncs. |
| `Linear URL` | `url` | pull only | n/a | Cached → `HYPERLINK` formula |
| `Milestone?` (bool) | `milestone.id` | snapshot only | n/a | Captured for future Phase 2.1 |

---

## Gaps & misalignments

### Workbook-only (no Linear counterpart)

| Field | Why not synced |
|-------|----------------|
| `% Complete` | Linear has no native completion-% on issues; state transitions are the proxy |
| `Notes` | Linear's `description` is rich-text markdown — semantic mismatch with workbook freeform notes; not yet wired |
| `Team` | Linear has no per-issue team field (team is project-level). Could map to `labels` in Phase 2+ but opt-in |
| `Level` | Pure UI artifact (WBS depth) — meaningless in Linear |

### Linear-only (we don't consume)

| Field | Notes |
|-------|-------|
| `labels` | Candidate for Team mapping; would need policy on multi-label rows |
| `cycle` | Could surface in workbook for release-grouping; not yet specified |
| `priority` | No workbook priority column |
| `description` | Workbook has no rich-text store; would need a sidecar column |
| `gitBranchName` | Useful for PR linking; not yet specified |
| `comments / attachments / subscribers` | No workbook collaboration model |
| `startedAt / completedAt / canceledAt` | Workbook reconstructs from cascade + status; redundant |

### Both sides have it, but not synced

| Pair | Why excluded (Phase 2 v1) |
|------|---------------------------|
| `End` ↔ `dueDate` | **Now fully bidirectional (2026-05-18)**. User directive: keep it simple, treat them as the same field. Empty/clear on either side propagates. Workbook cascade still runs locally between syncs; on next sync, computed End re-establishes via PUSH if Linear hasn't moved it. |
| `Milestone?` ↔ `milestone` | Lossy: workbook `bool` can't encode Linear milestone obj (title/progress/dates). Phase 2.1 adds milestone-id sidecar. |
| `Start` ↔ `startedAt` | Different semantics: workbook cascade-projected vs. Linear actual transition timestamp. Not directly mappable. |
| `Predecessors` (DSL) ↔ `blockedBy` | **Partial**: only FS+0 round-trips. Lags (`+3`), SS/SF, and parent-level predecessors drop on push. |

### Asymmetric flows (one-way by design)

- **Status → Linear**: workbook `Cancelled` added to enum specifically to round-trip Linear's `canceled`-type states (auto_status carve-out preserves it).
- **Archive (delete)**: workbook delete → Linear `state = <team's first canceled-type state name>`. Resolved at sync time via `list_issue_statuses` (CpInputConfig.linear_archive_state). Hardcoding fails silently — "Cancelled" (UK) vs "Canceled" (US) caught live in P2-T8 probe.
- **blockedBy two-pass push**: Pass 1 creates new issues + emits placeholders (`__NEW_<wbs>__`); Pass 2 substitutes real IDs into `blockedBy` deltas. Append/remove via `save_issue(blockedBy=…, removeBlockedBy=…)`.

### Known issues / footguns

| Issue | Impact | Tracker |
|-------|--------|---------|
| Synthetic `MS-*` milestone IDs fail `save_issue` | dueDate push for milestone rows must be skipped manually for now | GH #8 |
| `save_issue` response excludes `relations` block | No direct post-write verification of `blockedBy` deltas; relying on optimistic snapshot | Phase 2 v1 accepted risk |
| State-name matching exact + silent on mismatch | Wrong state name → 200-OK no-op (no error). MUST resolve from `list_issue_statuses` | doc'd in SKILL.md |
| Estimate unit not detected | Assumes 1 point = 1 day. Linear units (Points/Hours/T-shirt/Exponential) ignored | `estimate_to_days` config exists but unused (`cp/adapter.py:137`) |
| Predecessor DSL → blockedBy is lossy | Lags / SS / SF / parent-refs dropped on push; only FS+0 round-trips | unscoped |
| Pull → workbook hyperlink overwrites manual edits to Name col formula | Rare in practice; covered by `LINEAR_WINS` policy on title | accepted |

### Semantic conflicts worth flagging

- **Cascade vs. commitment**: workbook `End` is a projection from predecessors + duration; Linear `dueDate` is typically a user-set commitment. Per user direction (2026-05-18), we treat them as the same field anyway. Empty Linear dueDates DO pull through and clear workbook End; the next cascade run will re-derive a value from predecessors + duration if appropriate. This is simpler to reason about than the prior anchor-flag design.
- **Milestone**: workbook treats milestones as zero-duration tasks; Linear treats them as separate first-class objects with progress aggregation. Lossless mapping requires a new milestone-link sidecar.
- **Owner vs. assignee**: workbook `Owner` is freeform (display name OR email OR initials); Linear `assignee` is a User reference. Snapshot uses case-insensitive comparison but no canonicalization on push — user must type a string Linear can resolve.
