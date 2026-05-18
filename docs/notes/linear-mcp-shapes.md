# Notes: Linear MCP response shapes (T6 probe findings)

**Date:** 2026-05-17
**Probe target:** synthetic `Gantt Skill — Linear Integration Test`
project in the user's `JasonGarcia` Linear workspace (team key `JAS`)
**Fixtures:** `tests/fixtures/linear_mcp/*.json` (8 files, one per MCP call)
**Project URL:** https://linear.app/jasongarcia/project/gantt-skill-linear-integration-test-799fa6cf074a

This doc captures findings from running the 8 read calls planned for
T6 against a live Linear workspace. It informs the SKILL.md playbook
(T7) so the normalization recipe is written against actual MCP shapes
rather than guesses.

## Findings (answers to the plan's 4 open questions)

### 1. Does `list_issues` include `blockedBy` / `relations` inline? — **No.**

`list_issues` returns issues with `parentId` (for sub-issues) but does
**not** include `relations` or `blockedBy`. To build the project's
dependency graph the agent must follow up with
`get_issue(id=<issue>, includeRelations=true)` for every issue that
might have blockers.

**Impact on the pull's MCP-call cost:** O(N) where N = number of issues
with potential blockers. For the 5-issue test project, that's 5 extra
get_issue calls (one each for JAS-5..JAS-9). Cheap. For a 100-issue
project that's 100 extra calls — still acceptable for an interactive
flow, but worth surfacing in SKILL.md's cost note.

**Optimization:** the agent can skip `get_issue` for issues whose
position in the create order makes blockers impossible — but that's a
micro-opt; safer to just fetch all.

The `relations` shape is minimal: `[{id, title}]` per blocker. No
relation type (FS/SS/FF/SF) or lag — Linear only models FS+0
semantically. This matches the spec's assumption.

### 2. What estimate unit does the probed team use? — **Points.**

`get_team` does **not** include estimate-unit config. The agent must
infer the unit by looking at any issue's `estimate` field, which has
shape `{value: <number>, name: "<N> Points"}` for points-based teams.
The probed team uses Points. Other Linear estimate units (hours,
exponential, t-shirt sizes, none) would produce different `name`
strings — `"8 Hours"`, `"L"`, etc. The agent's normalization step
should:

1. Read the first non-null `estimate.name` to detect the unit
2. Apply the appropriate conversion (default: 1 point = 1 working day)
3. Surface the detected unit + conversion in the dry-run preview so
   the user can confirm before applying

For issues with no estimate, the `estimate` key is simply **absent**
from the MCP response (not `null`). Treat missing-or-falsy as
"no estimate → use `default_duration_days` with a warning."

### 3. What states (default + custom) exist? — **7 states; map by `type` not `name`.**

The probed team's `list_issue_statuses` returned:

| Name | Type |
|---|---|
| Backlog | backlog |
| Todo | unstarted |
| In Progress | started |
| In Review | started |
| Done | completed |
| Canceled | canceled |
| Duplicate | canceled |

Linear's `type` field is one of: `backlog | unstarted | started |
completed | canceled`. **Always map by type, not by name.** Custom
workflow state names (`In Review`, `Duplicate`) still route to the
right workbook Status because their type is one of the known five.

Recommended default mapping:

| Linear `type` | Workbook Status |
|---|---|
| backlog | Not Started |
| unstarted | Not Started |
| started | In Progress |
| completed | Done |
| canceled | Cancelled |

If a future Linear release adds new state types, the agent should
fall back to "Not Started" with a warning until the mapping table is
updated.

### 4. Other surprises affecting normalization

- **`list_issues` returns issues in reverse-creation order** (newest
  first), not Linear's `sortOrder` like I'd assumed. The agent must
  explicitly re-sort for stable WBS-id assignment. For the test
  project, the order came back JAS-9, JAS-8, JAS-7, JAS-6, JAS-5 —
  reverse of creation.
- **`get_project` returns the FULL description** (untruncated).
  `list_projects` truncates description with a `… (truncated, use
  get_project for full description)` marker. The agent should use
  `get_project` for the description if it's surfaced anywhere.
- **`project.milestones` shape differs between endpoints:**
  - `get_project(includeMilestones=true)` returns `progress: "0%"` (string)
  - `list_milestones(project=...)` returns `progress: 0` (numeric, 0..1)
  - Pick one source and use it consistently. Recommendation:
    `list_milestones` for the canonical shape (numeric is easier to
    work with); only call `get_project(includeMilestones)` if you
    also need other project metadata in the same response.
- **Issue identifiers are stable short keys** like `JAS-5`. No
  leading zeros. Suitable for direct use as the persistent
  `linear_id` in `_LinearSync` rows.
- **`gitBranchName` is auto-generated** per issue (e.g.
  `jasongarcia24/jas-5-spec-optics`) — not relevant to the gantt
  skill but worth knowing if we ever surface PR links.
- **Linear users return as display names**, not emails, in
  `createdBy`. For the workbook `Owner` column the agent should use
  `assignee.email` when an issue has one (the test issues had no
  assignee so this wasn't directly observed — confirm at next live
  use). Empty assignee → empty Owner cell.
- **`labels` is an array** on every issue (even when empty).
  Could be used in Phase 2+ for `team` mapping (e.g. label
  `team:hardware` → workbook Team column = "Hardware").

## Project layout in the test fixture

For reference / re-use in Phase 2+ testing:

```
v1.0 launch (milestone, sortOrder 69)
JAS-5 Spec optics      (estimate 2pts, no blockers, no parent)
JAS-6 Eyepiece fab     (estimate 5pts, blockedBy: JAS-5)
  └── JAS-8 Eyepiece QA (estimate 2pts, parentId: JAS-6, no blockers)
JAS-7 Doc revision     (NO ESTIMATE,    blockedBy: JAS-5)
JAS-9 Launch           (estimate 1pt,   blockedBy: JAS-6, JAS-7)
```

Re-pulling this project should produce a workbook with:
- 5 task rows + 1 synthesized milestone row (`is_milestone=true` from
  the project's `v1.0 launch` milestone)
- WBS hierarchy preserving the JAS-8 → JAS-6 parent relation (`6` →
  `6.1` or similar)
- One warning for JAS-7's missing estimate
- One Critical-path chain: `Spec optics → Eyepiece fab → Launch`
  (longest path, 8 working days at default 1 pt = 1 day)

## SKILL.md (T7) translation recipe — quick draft

Based on the above, the MCP call sequence the SKILL.md playbook
should describe:

```
1. list_teams                        (1 call, cached per session)
2. list_projects(team=<chosen>)      (paginated; usually 1 call)
3. get_project(<chosen>,
               includeMilestones=true)  (1 call)
4. list_issues(project=<chosen>)     (paginated; 1 call per ~250 issues)
5. for each issue:                   (N calls; the dominant cost)
   get_issue(id=<issue>,
             includeRelations=true)
6. list_milestones(project=<chosen>) (1 call — preferred shape over get_project's inline milestones)
7. list_issue_statuses(team=<chosen>)(1 call, cached per session)
```

Total: ~5 + N calls per pull, where N = issue count. For the test
project: 10 calls. For a 30-issue real project: ~35 calls. Tractable
inside an interactive `/gantt` slash-command flow.
