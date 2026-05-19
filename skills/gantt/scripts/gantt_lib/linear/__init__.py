"""Linear-integration helpers for the gantt CLI.

This package handles workbook-side state for tasks pulled from Linear via
the agent's MCP layer. The CLI itself never calls Linear; the agent does
the fetches and pipes a normalized JSON payload to `gantt linear-sync
--stdin`. See `docs/specs/linear-integration.md` for the full design.

Modules:
- `sync_tab`: read/write the hidden `_LinearSync` workbook tab that holds
  the workbook-task ↔ Linear-issue linkage plus sidecar + snapshot cols
  for the 3-way merge.
- `snapshot`: IssueSnapshot dataclass + per-field equality helpers used
  by the merge engine to detect cross-side changes.
- `merge`: 3-way merge engine — classifies each field as PUSH / PULL /
  CONVERGED / CONFLICT / NO_OP using (workbook, snapshot, Linear) values.
- `push`: translates the merge diff into Linear MCP `save_issue` request
  descriptors for the agent to dispatch (two-pass for blockedBy).
- `sync`: top-level orchestrator — composes pull, merge, push for all
  three directions (pull / push / both).
"""
