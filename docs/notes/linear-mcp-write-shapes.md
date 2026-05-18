# Notes: Linear MCP write-side response shapes (P2-T8 probe findings)

**Date:** 2026-05-17
**Probe target:** `Gantt Skill — Linear Integration Test` Linear project (`JAS` team)
**Fixtures:** `tests/fixtures/linear_mcp/save_issue_*.json` (4 files)
**Companion:** Phase-1 read-side findings in `docs/notes/linear-mcp-shapes.md`

This doc captures the Phase-2 write-side `save_issue` response shapes
and behavior quirks. Informs the SKILL.md MCP TODO execution loop
(P2-T9) and several push.py / sync.py implementation details.

## Findings (answers to the plan's 4 open questions)

### 1. Does `save_issue` for a single field include unmodified fields in the response? — **Yes, full issue object.**

`save_issue` always returns the entire issue object as it now stands
post-write — every field that `get_issue` returns, **minus** the
`relations` block. Even if you only changed `state`, the response
includes title, estimate, assignee, dueDate, all the timestamps, etc.

Implication for the CLI: after each successful save_issue, the agent
has enough data to refresh most of the `_LinearSync` snapshot columns
inline. Only the `blockedby` snapshot field requires a separate
`get_issue(includeRelations=true)` follow-up, since save_issue never
returns relations.

### 2. What's the exact response shape after `removeBlockedBy`? — **No `relations` in response; need follow-up `get_issue`.**

Same as #1 above — the `save_issue` response after a `removeBlockedBy:
["JAS-X"]` call shows the issue's updated `updatedAt` but does NOT
include the post-removal `relations.blockedBy`. The agent must follow
up with `get_issue(includeRelations=true)` if it needs to verify the
post-call relation set.

For most Phase-2 sync use cases this follow-up isn't strictly needed —
the CLI knows the *intended* post-call state from its own diff
calculation, and writes that to the snapshot columns optimistically.
Follow-up reads are useful only when:
- Suspecting concurrent edits (someone added a blocker between sync's
  read and write)
- Debugging / verifying a partial-failure recovery

### 3. Does archive (`state=Canceled`) need any other field set? — **No, but state-name matching is EXACT and SILENT on mismatch.**

Single field is enough: `save_issue(id=X, state="Canceled")` flips
the issue to canceled-type, populates `canceledAt`, and surfaces in
the response with `statusType="canceled"`. Linear's `archivedAt` field
stays null — Linear distinguishes:
- **Canceled** (workflow state — reversible, shows in default views with strikethrough)
- **Archived** (admin lifecycle action — hides from default views,
  reversible only via the Linear UI's archive panel)

For Phase 2 we use **Canceled**, not Archived. Rationale: UI-replayable,
appears in pulls so the agent can detect re-opens, doesn't require a
separate MCP mutation.

**SILENT-FAILURE WARNING:** state-name matching is case-and-spelling
exact. The probe's first archive attempt used `state="Cancelled"`
(British, two L). The MCP returned a 200-OK with the issue's UNCHANGED
state (still Backlog), same `updatedAt` as `createdAt`. No error. No
log. Just nothing happened.

This means the agent MUST resolve the team's actual canceled-type
state name from `list_issue_statuses` rather than hard-coding. The
P2-T6 CLI already takes `linear_archive_state` from the payload's
`config` block; the agent's responsibility is to populate it correctly.

Future hardening: the CLI could refuse to emit an archive MCP request
when `linear_archive_state` is empty (push.py already does this
gracefully — emits no archive call).

### 4. Any rate-limit headers or batch-size hints? — **Not exposed via MCP.**

The MCP wrapper hides any HTTP-level rate-limit headers Linear's
GraphQL API might return. No batch-size hints surfaced in responses.

Practical implication: parallel-dispatch (the R1 mitigation) is bounded
by Claude's per-turn tool-call limit (~10-20), not by Linear API
limits we can directly observe. If we ever hit Linear's actual
throttle, it would surface as a generic MCP error and the agent's
retry loop would need to back off.

## Surprises / gotchas

### Create returns full issue immediately

`save_issue` (no `id`) for create returns the full new issue
synchronously, including the auto-assigned `id` (e.g. `JAS-10`) and
`url`. This means the agent can build pass-2 (blockedBy reconciliation)
referencing the new ID right after pass-1 returns — no polling needed.

### Default state on create

Create without an explicit `state` lands the issue in the team's
configured default state (Backlog for `JasonGarcia` team). Agent
typically doesn't need to set state on create — let Linear pick.

### `gitBranchName` auto-generated

Every issue (existing or new) carries an auto-generated `gitBranchName`
in the form `<workspace>/<slug>`. Not relevant to the sync but useful
if we ever want to surface PR-create suggestions to the user.

### `updatedAt` semantics under no-op writes

When a save_issue effectively did nothing (e.g., state-name mismatch),
the response's `updatedAt` equals `createdAt` — i.e., the issue wasn't
touched at all. This is the agent's signal that the write didn't take
effect. The CLI's optimistic-snapshot-refresh assumes writes succeeded;
if a no-op occurs, the next sync's 3-way merge will detect the snapshot
↔ Linear divergence and re-resolve.

## SKILL.md (P2-T9) implications

The sync playbook's MCP TODO execution loop should:

1. Dispatch pass-1 requests in parallel (one Claude turn).
2. For each `save_issue` response, validate that `updatedAt > createdAt`
   (or just compare to the issue's pre-call `updatedAt`). Log a warning
   if no-op detected — likely a state-name or other field-name mismatch.
3. For each `create` response, capture `id` and `url` from the
   response. Pass them to a follow-up `gantt linear-sync --apply-create-results`
   call (or substitute into pass-2 placeholders if blockedBy
   reconciliation references them).
4. Dispatch pass-2 requests (blockedBy reconciliation) — also parallel.
5. Skip explicit relation-verification reads unless debugging.

## Cleanup notes

- `JAS-10` ("Phase-2 probe test (safe to delete)") is now in `Canceled`
  state in the test project. Safe to leave there — it's a record of
  the probe and won't appear in default views. Delete from Linear UI
  if desired.
- The state-mismatch silent no-op on the first archive attempt didn't
  leave any stray data — JAS-10 stayed in Backlog until the corrected
  call.
- JAS-7 state was flipped In Progress → Backlog and reverted. Clean.
- JAS-9 blockedBy had JAS-5 added then removed. Clean.
- Test project is back to its pre-probe state apart from the new
  Canceled JAS-10 issue.
