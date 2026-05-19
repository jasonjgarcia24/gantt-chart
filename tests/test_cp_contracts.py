"""Tests for gantt_lib.cp.contracts — JSON serde for the project graph.

Verifies the wire contract:
- Required fields are required; missing ones raise ContractValidationError
  with a JSONPath-ish locator
- Unknown keys at any level are silently ignored (forward-compat)
- Date fields round-trip through ISO-8601 ↔ datetime.date
- Edge types are validated against the FS/SS/FF/SF enum
- to_json(from_json(s)) is semantically stable (re-parsing the output
  produces an equal CpInput dataclass)
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from gantt_lib.cp.contracts import (
    ContractValidationError,
    CpError,
    CpInputIssue,
    CpOutput,
    CpOutputItem,
    CpOutputWarning,
    from_json,
    to_json,
)


# --- from_json: happy paths --------------------------------------------------


def test_from_json_minimal():
    s = json.dumps(
        {
            "project": {"name": "P1", "source": "linear"},
            "config": {"default_duration_days": 1, "today": "2026-05-16"},
            "issues": [],
            "edges": [],
        }
    )
    inp = from_json(s)
    assert inp.project.name == "P1"
    assert inp.project.source == "linear"
    assert inp.project.source_ref is None
    assert inp.config.default_duration_days == 1
    assert inp.config.today == date(2026, 5, 16)
    assert inp.config.estimate_to_days == {}
    assert inp.issues == []
    assert inp.edges == []


def test_from_json_full():
    s = json.dumps(
        {
            "project": {
                "name": "TPM-Eyepiece",
                "source": "linear",
                "source_ref": "https://linear.app/x/project/abc",
            },
            "config": {
                "default_duration_days": 1,
                "today": "2026-05-16",
                "estimate_to_days": {"kind": "points_to_days", "ratio": 1.0},
            },
            "issues": [
                {
                    "linear_id": "TPM-42",
                    "title": "Spec optics",
                    "state": "In Progress",
                    "estimate_days": 5,
                    "percent": 30,
                    "assignee": "alex@example.com",
                    "start_anchor": "2026-05-17",
                    "is_milestone": False,
                    "parent_linear_id": None,
                },
                {
                    "linear_id": "TPM-47",
                    "title": "Eyepiece fab",
                    "estimate_days": 8,
                    "is_milestone": True,
                },
            ],
            "edges": [
                {
                    "from_linear_id": "TPM-42",
                    "to_linear_id": "TPM-47",
                    "type": "FS",
                    "lag_days": 3,
                }
            ],
        }
    )
    inp = from_json(s)
    assert inp.project.source_ref == "https://linear.app/x/project/abc"
    assert inp.config.estimate_to_days == {"kind": "points_to_days", "ratio": 1.0}
    assert len(inp.issues) == 2
    assert inp.issues[0].linear_id == "TPM-42"
    assert inp.issues[0].percent == 30
    assert inp.issues[0].start_anchor == date(2026, 5, 17)
    assert inp.issues[0].end_anchor is None
    assert inp.issues[1].is_milestone is True
    assert inp.issues[1].state == ""
    assert len(inp.edges) == 1
    assert inp.edges[0].type == "FS"
    assert inp.edges[0].lag_days == 3


# --- from_json: validation failures ------------------------------------------


def test_from_json_missing_top_level_object():
    with pytest.raises(ContractValidationError, match="must be an object"):
        from_json(json.dumps([1, 2, 3]))


def test_from_json_missing_project():
    s = json.dumps(
        {"config": {"default_duration_days": 1, "today": "2026-05-16"}}
    )
    with pytest.raises(ContractValidationError, match=r"\$\.project"):
        from_json(s)


def test_from_json_missing_project_source():
    s = json.dumps(
        {
            "project": {"name": "P1"},
            "config": {"default_duration_days": 1, "today": "2026-05-16"},
        }
    )
    with pytest.raises(ContractValidationError, match=r"\$\.project\.source"):
        from_json(s)


def test_from_json_missing_config():
    s = json.dumps({"project": {"name": "P1", "source": "linear"}})
    with pytest.raises(ContractValidationError, match=r"\$\.config"):
        from_json(s)


def test_from_json_missing_issue_linear_id():
    s = json.dumps(
        {
            "project": {"name": "P1", "source": "linear"},
            "config": {"default_duration_days": 1, "today": "2026-05-16"},
            "issues": [{"title": "no id"}],
            "edges": [],
        }
    )
    with pytest.raises(
        ContractValidationError, match=r"\$\.issues\[0\]\.linear_id"
    ):
        from_json(s)


def test_from_json_invalid_edge_type():
    s = json.dumps(
        {
            "project": {"name": "P1", "source": "linear"},
            "config": {"default_duration_days": 1, "today": "2026-05-16"},
            "issues": [
                {"linear_id": "A", "title": "A"},
                {"linear_id": "B", "title": "B"},
            ],
            "edges": [
                {
                    "from_linear_id": "A",
                    "to_linear_id": "B",
                    "type": "ZZ",
                    "lag_days": 0,
                }
            ],
        }
    )
    with pytest.raises(ContractValidationError, match="invalid edge type"):
        from_json(s)


def test_from_json_issues_not_array():
    s = json.dumps(
        {
            "project": {"name": "P1", "source": "linear"},
            "config": {"default_duration_days": 1, "today": "2026-05-16"},
            "issues": "not a list",
            "edges": [],
        }
    )
    with pytest.raises(ContractValidationError, match=r"\$\.issues must be an array"):
        from_json(s)


# --- from_json: forward-compat (unknown keys silently ignored) ---------------


def test_from_json_ignores_unknown_keys():
    s = json.dumps(
        {
            "project": {
                "name": "P1",
                "source": "linear",
                "future_field": "ignored",
            },
            "config": {
                "default_duration_days": 1,
                "today": "2026-05-16",
                "future_config": 42,
            },
            "issues": [
                {
                    "linear_id": "A",
                    "title": "A",
                    "made_up_field": True,
                    "another_future_field": [1, 2, 3],
                }
            ],
            "edges": [],
            "top_level_extra": "also ignored",
        }
    )
    inp = from_json(s)
    assert inp.project.name == "P1"
    assert inp.issues[0].linear_id == "A"


def test_from_json_omits_optional_fields():
    """All optional fields default cleanly when omitted."""
    s = json.dumps(
        {
            "project": {"name": "P1", "source": "linear"},
            "config": {"default_duration_days": 1, "today": "2026-05-16"},
            "issues": [{"linear_id": "X", "title": "X"}],
        }
    )
    inp = from_json(s)
    assert inp.edges == []
    assert inp.issues[0].percent == 0
    assert inp.issues[0].estimate_days is None
    assert inp.issues[0].parent_linear_id is None
    assert inp.issues[0].labels == []
    assert inp.config.linear_team_label_map == {}


def test_from_json_parses_linear_team_label_map_and_issue_labels():
    """Team-sync inputs: config carries workbook-team→Linear-label map;
    each issue carries its current label names."""
    s = json.dumps(
        {
            "project": {"name": "TPM90", "source": "linear"},
            "config": {
                "default_duration_days": 1,
                "today": "2026-05-18",
                "linear_team_label_map": {
                    "Engineering": "SW",
                    "Manufacturing": "MFG",
                },
            },
            "issues": [
                {"linear_id": "ENG-1", "title": "Build it", "labels": ["SW", "Bug"]},
                {"linear_id": "ENG-2", "title": "Make it", "labels": ["MFG"]},
            ],
        }
    )
    inp = from_json(s)
    assert inp.config.linear_team_label_map == {
        "Engineering": "SW",
        "Manufacturing": "MFG",
    }
    assert inp.issues[0].labels == ["SW", "Bug"]
    assert inp.issues[1].labels == ["MFG"]


# --- to_json: output shapes --------------------------------------------------


def test_to_json_cp_output_with_dates():
    out = CpOutput(
        project={"name": "P1"},
        computed_at="2026-05-16T17:00:00Z",
        critical_path=[
            CpOutputItem(
                linear_id="A",
                wbs_id="1",
                title="A",
                start=date(2026, 5, 17),
                end=date(2026, 5, 21),
                slack_days=0,
                on_critical_path=True,
            )
        ],
        cascade=[
            CpOutputItem(
                linear_id="A",
                wbs_id="1",
                title="A",
                start=date(2026, 5, 17),
                end=date(2026, 5, 21),
                slack_days=0,
                on_critical_path=True,
            )
        ],
        warnings=[],
    )
    s = to_json(out)
    d = json.loads(s)
    assert d["ok"] is True
    assert d["cascade"][0]["start"] == "2026-05-17"
    assert d["cascade"][0]["end"] == "2026-05-21"
    assert d["critical_path"][0]["wbs_id"] == "1"


def test_to_json_cp_output_with_none_dates():
    """Dates that are None serialize as JSON null."""
    out = CpOutput(
        project={"name": "P1"},
        computed_at="2026-05-16T17:00:00Z",
        critical_path=[],
        cascade=[
            CpOutputItem(
                linear_id="A",
                wbs_id="1",
                title="A",
                start=None,
                end=None,
                slack_days=0,
                on_critical_path=False,
            )
        ],
        warnings=[],
    )
    d = json.loads(to_json(out))
    assert d["cascade"][0]["start"] is None
    assert d["cascade"][0]["end"] is None


def test_to_json_cp_output_with_warnings():
    out = CpOutput(
        project={"name": "P1"},
        computed_at="2026-05-16T17:00:00Z",
        critical_path=[],
        cascade=[],
        warnings=[
            CpOutputWarning(
                linear_id="X",
                kind="missing_estimate",
                message="no estimate; using default 1d",
            )
        ],
    )
    d = json.loads(to_json(out))
    assert d["warnings"][0]["kind"] == "missing_estimate"


def test_to_json_cp_error():
    err = CpError(
        error="cycle_detected",
        detail="A → B → A",
        trace=["A", "B", "A"],
    )
    d = json.loads(to_json(err))
    assert d["ok"] is False
    assert d["error"] == "cycle_detected"
    assert d["trace"] == ["A", "B", "A"]


# --- Round-trip property -----------------------------------------------------


def test_roundtrip_cp_input():
    """to_json(from_json(s)) → re-parse → equal CpInput.

    The wire bytes won't be byte-identical (key ordering, defaulted-field
    expansion), but the *semantic* round-trip is what matters: parsing the
    re-serialized form yields the same dataclass.
    """
    s = json.dumps(
        {
            "project": {"name": "P1", "source": "linear"},
            "config": {
                "default_duration_days": 2,
                "today": "2026-05-16",
                "estimate_to_days": {"kind": "points_to_days", "ratio": 1.0},
            },
            "issues": [
                {
                    "linear_id": "A",
                    "title": "Task A",
                    "estimate_days": 3,
                    "percent": 50,
                    "start_anchor": "2026-05-18",
                    "is_milestone": False,
                },
                {
                    "linear_id": "B",
                    "title": "Task B",
                    "estimate_days": 4,
                    "parent_linear_id": "A",
                },
            ],
            "edges": [
                {
                    "from_linear_id": "A",
                    "to_linear_id": "B",
                    "type": "SS",
                    "lag_days": 1,
                }
            ],
        }
    )
    inp1 = from_json(s)
    s2 = to_json(inp1)
    inp2 = from_json(s2)
    assert inp1 == inp2


# --- Sanity ------------------------------------------------------------------


def test_cp_input_issue_dataclass_defaults():
    """Direct dataclass construction works with just the required fields."""
    iss = CpInputIssue(linear_id="X", title="X")
    assert iss.state == ""
    assert iss.percent == 0
    assert iss.is_milestone is False
