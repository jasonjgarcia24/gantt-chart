"""Factory helpers for BaselineRow test data.

Keep tiny — each helper should illustrate ONE concept (a single row, a fresh
snapshot, a re-snapshot, etc.) so test failures point at the structure, not the
data layout.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from gantt_lib.baseline import BaselineRow


def make_baseline_row(
    program: str = "TPM90",
    wbs: str = "1",
    task_name: str = "Concept",
    snapshot_date: date = date(2026, 4, 1),
    baseline_start: Optional[date] = date(2026, 4, 1),
    baseline_end: Optional[date] = date(2026, 4, 8),
    baseline_duration: int = 6,
    baseline_predecessors: str = "",
    snapshot_label: str = "",
    snapshot_actor: str = "test",
) -> BaselineRow:
    return BaselineRow(
        program=program,
        wbs=wbs,
        task_name=task_name,
        snapshot_date=snapshot_date,
        baseline_start=baseline_start,
        baseline_end=baseline_end,
        baseline_duration=baseline_duration,
        baseline_predecessors=baseline_predecessors,
        snapshot_label=snapshot_label,
        snapshot_actor=snapshot_actor,
    )
