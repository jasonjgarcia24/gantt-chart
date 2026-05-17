"""Pure-math project-graph contracts + cascade adapter.

This package is the seam between the `gantt_lib.cascade` / `critical_path`
engine and the data sources that feed it. The Sheets-backed program path
talks to the engine directly via Program; the Linear-backed pull path
talks to the engine through this contract layer (JSON in → Program → JSON
out). The CLI never imports Linear; Linear data arrives as normalized
JSON from the agent (see SKILL.md).
"""
from gantt_lib.cp.adapter import (
    cp_input_to_program,
    default_wbs_assignments,
    program_results_to_cp_output,
    run_cp,
)
from gantt_lib.cp.contracts import (
    ContractValidationError,
    CpError,
    CpInput,
    CpInputConfig,
    CpInputEdge,
    CpInputIssue,
    CpInputProject,
    CpOutput,
    CpOutputItem,
    CpOutputWarning,
    from_json,
    to_json,
)

__all__ = [
    "ContractValidationError",
    "CpError",
    "CpInput",
    "CpInputConfig",
    "CpInputEdge",
    "CpInputIssue",
    "CpInputProject",
    "CpOutput",
    "CpOutputItem",
    "CpOutputWarning",
    "cp_input_to_program",
    "default_wbs_assignments",
    "from_json",
    "program_results_to_cp_output",
    "run_cp",
    "to_json",
]
