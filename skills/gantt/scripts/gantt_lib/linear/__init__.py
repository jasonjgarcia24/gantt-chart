"""Linear-integration helpers for the gantt CLI.

This package handles workbook-side state for tasks pulled from Linear via
the agent's MCP layer. The CLI itself never calls Linear; the agent does
the fetches and pipes a normalized JSON payload to `gantt linear-pull
--stdin`. See `docs/specs/linear-integration.md` for the full design.

Modules:
- `sync_tab`: read/write the hidden `_LinearSync` workbook tab that holds
  `(program, wbs_id, linear_id, last_synced, linear_url)` rows linking
  workbook tasks to their Linear issues.
- `pull`: orchestrator that ingests a Linear payload, reconciles against
  existing linkage, applies the conflict policy, and writes the program
  tab. (Phase 1, T4.)
"""
