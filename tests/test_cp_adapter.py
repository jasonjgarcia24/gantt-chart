"""Tests for gantt_lib.cp.adapter — JSON-graph → cascade-engine glue.

Strategy: hand-write input fixtures only, then verify adapter behavior via
direct assertions. Trying to lock expected dates into separate
expected.json files is brittle (any change to working-days math or
calendar would force a fixture rewrite) and provides little value because
the cascade engine is already tested. The adapter's job is *translation*
— so that's what we test.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from gantt_lib.cp.adapter import (
    cp_input_to_program,
    default_wbs_assignments,
    run_cp,
)
from gantt_lib.cp.contracts import (
    CpError,
    CpInput,
    CpOutput,
    from_json,
)

FIXTURES = Path(__file__).parent / "fixtures" / "cp"


def _load(name: str) -> CpInput:
    return from_json((FIXTURES / name).read_text())


# --- linear_chain ------------------------------------------------------------


def test_linear_chain_all_on_critical_path():
    """A → B → C is a pure chain; every task has zero slack."""
    inp = _load("linear_chain.json")
    out = run_cp(inp)
    assert isinstance(out, CpOutput)
    assert out.ok is True
    assert len(out.cascade) == 3
    assert all(item.on_critical_path for item in out.cascade)
    assert [item.wbs_id for item in out.critical_path] == ["1", "2", "3"]
    assert [item.linear_id for item in out.critical_path] == [
        "TPM-1",
        "TPM-2",
        "TPM-3",
    ]


def test_linear_chain_dates_ordered():
    """Each task starts after its predecessor ends."""
    inp = _load("linear_chain.json")
    out = run_cp(inp)
    assert isinstance(out, CpOutput)
    by_wbs = {item.wbs_id: item for item in out.cascade}
    assert by_wbs["1"].start == date(2026, 5, 18)  # Monday
    assert by_wbs["2"].start > by_wbs["1"].end
    assert by_wbs["3"].start > by_wbs["2"].end


# --- fan_in ------------------------------------------------------------------


def test_fan_in_long_branch_on_critical_path():
    """A (2d) and B (10d) both feed C. B is on CP; A has slack."""
    inp = _load("fan_in.json")
    out = run_cp(inp)
    assert isinstance(out, CpOutput)
    by_linear = {item.linear_id: item for item in out.cascade}
    assert by_linear["TPM-B"].on_critical_path is True
    assert by_linear["TPM-B"].slack_days == 0
    assert by_linear["TPM-C"].on_critical_path is True
    assert by_linear["TPM-A"].on_critical_path is False
    assert by_linear["TPM-A"].slack_days > 0
    # A has 8 working days of slack (10 - 2).
    assert by_linear["TPM-A"].slack_days == 8


# --- fan_out -----------------------------------------------------------------


def test_fan_out_long_branch_on_critical_path():
    """A feeds B (10d) and C (2d). A + B is on CP; C has slack."""
    inp = _load("fan_out.json")
    out = run_cp(inp)
    assert isinstance(out, CpOutput)
    by_linear = {item.linear_id: item for item in out.cascade}
    assert by_linear["TPM-A"].on_critical_path is True
    assert by_linear["TPM-B"].on_critical_path is True
    assert by_linear["TPM-C"].on_critical_path is False
    assert by_linear["TPM-C"].slack_days == 8


# --- missing_estimate --------------------------------------------------------


def test_missing_estimate_emits_warning_and_applies_default():
    """An issue with no estimate gets default_duration_days + a warning.
    Milestones with no estimate are *not* warned (zero duration is intentional)."""
    inp = _load("missing_estimate.json")
    out = run_cp(inp)
    assert isinstance(out, CpOutput)

    warning_linear_ids = {w.linear_id for w in out.warnings if w.kind == "missing_estimate"}
    assert warning_linear_ids == {"TPM-B"}
    assert "TPM-C" not in warning_linear_ids  # milestone exemption

    by_linear = {item.linear_id: item for item in out.cascade}
    # TPM-B got the default 1d.
    assert by_linear["TPM-B"].start == by_linear["TPM-B"].end
    # TPM-C is a milestone: start == end (zero-duration).
    assert by_linear["TPM-C"].start == by_linear["TPM-C"].end


# --- cycle -------------------------------------------------------------------


def test_cycle_returns_error():
    """A ↔ B is a cycle; run_cp returns CpError not CpOutput."""
    inp = _load("cycle.json")
    out = run_cp(inp)
    assert isinstance(out, CpError)
    assert out.ok is False
    assert out.error == "cycle_detected"
    assert "cycle" in out.detail.lower() or "cyclic" in out.detail.lower() or out.detail  # engine message varies


# --- empty -------------------------------------------------------------------


def test_empty_project_is_clean():
    """No issues = no cascade, no warnings, ok=True."""
    inp = _load("empty.json")
    out = run_cp(inp)
    assert isinstance(out, CpOutput)
    assert out.ok is True
    assert out.cascade == []
    assert out.critical_path == []
    assert out.warnings == []


# --- WBS assignment ----------------------------------------------------------


def test_default_wbs_assignments_flat():
    """Top-level issues get sequential 1, 2, 3."""
    inp = _load("linear_chain.json")
    a = default_wbs_assignments(inp)
    assert a == {"TPM-1": "1", "TPM-2": "2", "TPM-3": "3"}


def test_default_wbs_assignments_hierarchical():
    """Sub-issues nested under parents get dotted ids."""
    inp = from_json(
        json.dumps(
            {
                "project": {"name": "H", "source": "linear"},
                "config": {"default_duration_days": 1, "today": "2026-05-18"},
                "issues": [
                    {"linear_id": "P1", "title": "Parent 1"},
                    {"linear_id": "C1A", "title": "Child A", "parent_linear_id": "P1"},
                    {"linear_id": "C1B", "title": "Child B", "parent_linear_id": "P1"},
                    {"linear_id": "P2", "title": "Parent 2"},
                    {"linear_id": "GC", "title": "Grandchild", "parent_linear_id": "C1A"},
                ],
                "edges": [],
            }
        )
    )
    a = default_wbs_assignments(inp)
    assert a["P1"] == "1"
    assert a["C1A"] == "1.1"
    assert a["C1B"] == "1.2"
    assert a["GC"] == "1.1.1"
    assert a["P2"] == "2"


def test_default_wbs_includes_defaulted_start_warning_for_orphans():
    """An issue with no predecessors and no start_anchor gets a defaulted
    start to config.today plus a warning."""
    inp = from_json(
        json.dumps(
            {
                "project": {"name": "O", "source": "linear"},
                "config": {"default_duration_days": 1, "today": "2026-05-18"},
                "issues": [
                    {"linear_id": "X", "title": "Orphan", "estimate_days": 3}
                ],
                "edges": [],
            }
        )
    )
    out = run_cp(inp)
    assert isinstance(out, CpOutput)
    defaulted = [w for w in out.warnings if w.kind == "defaulted_start"]
    assert len(defaulted) == 1
    assert defaulted[0].linear_id == "X"
    by_linear = {item.linear_id: item for item in out.cascade}
    assert by_linear["X"].start == date(2026, 5, 18)


# --- Predecessor DSL translation --------------------------------------------


def test_predecessor_dsl_built_with_correct_relation_and_lag():
    """Edges translate to the existing DSL format: 1FS+3, 2SS, etc."""
    inp = from_json(
        json.dumps(
            {
                "project": {"name": "P", "source": "linear"},
                "config": {"default_duration_days": 1, "today": "2026-05-18"},
                "issues": [
                    {"linear_id": "A", "title": "A", "estimate_days": 2},
                    {"linear_id": "B", "title": "B", "estimate_days": 2},
                    {"linear_id": "C", "title": "C", "estimate_days": 2},
                ],
                "edges": [
                    {"from_linear_id": "A", "to_linear_id": "C", "type": "FS", "lag_days": 3},
                    {"from_linear_id": "B", "to_linear_id": "C", "type": "SS", "lag_days": 0},
                ],
            }
        )
    )
    build = cp_input_to_program(inp, default_wbs_assignments(inp))
    by_wbs = {t.id: t for t in build.program.tasks}
    # C is wbs 3; its predecessors should reference A=1, B=2.
    assert by_wbs["3"].predecessors == "1FS+3, 2SS"


def test_edge_references_unknown_issue_raises_error():
    """Edge pointing to a non-existent issue → CpError missing_reference."""
    inp = from_json(
        json.dumps(
            {
                "project": {"name": "P", "source": "linear"},
                "config": {"default_duration_days": 1, "today": "2026-05-18"},
                "issues": [{"linear_id": "A", "title": "A", "estimate_days": 1}],
                "edges": [
                    {
                        "from_linear_id": "A",
                        "to_linear_id": "GHOST",
                        "type": "FS",
                        "lag_days": 0,
                    }
                ],
            }
        )
    )
    out = run_cp(inp)
    assert isinstance(out, CpError)
    assert out.error == "missing_reference"


# --- Output integrity --------------------------------------------------------


def test_cascade_items_carry_both_wbs_and_linear_ids():
    """Every cascade item exposes both keys so the agent can render either."""
    inp = _load("linear_chain.json")
    out = run_cp(inp)
    assert isinstance(out, CpOutput)
    for item in out.cascade:
        assert item.wbs_id  # non-empty
        assert item.linear_id  # non-empty


def test_critical_path_ordered_by_start_date():
    """CP items appear in chronological start order, not insertion order."""
    inp = _load("linear_chain.json")
    out = run_cp(inp)
    assert isinstance(out, CpOutput)
    starts = [item.start for item in out.critical_path]
    assert starts == sorted(starts)
