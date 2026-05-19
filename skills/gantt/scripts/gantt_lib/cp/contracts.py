"""JSON contracts for the cp engine.

The agent normalizes a Linear (or other future source) project graph into
a `CpInput` JSON document, pipes it to the CLI on stdin, and gets back a
`CpOutput` (cascade + critical path) or `CpError` JSON document on stdout.

Shapes are defined here as dataclasses with explicit serde so the wire
format is stable and forward-compatible:
- Unknown top-level / per-item keys are silently ignored on read
  (callers can ship new optional fields without breaking us)
- Missing required fields raise `ContractValidationError` with a
  JSONPath-ish locator so the agent can surface a useful message
- Date fields are ISO-8601 strings on the wire, `datetime.date` on the
  dataclass
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Optional


_VALID_EDGE_TYPES = frozenset({"FS", "SS", "FF", "SF"})


class ContractValidationError(ValueError):
    """Raised when an input JSON document is missing a required field or
    has an out-of-range value (e.g., an unknown edge type)."""


# ----- Input dataclasses -----------------------------------------------------


@dataclass
class CpInputProject:
    name: str
    source: str  # e.g., "linear"
    source_ref: Optional[str] = None


@dataclass
class CpInputConfig:
    default_duration_days: int
    today: date
    estimate_to_days: dict = field(default_factory=dict)
    # Phase-2 sync fields. Populated by the agent from list_teams /
    # list_projects / list_issue_statuses. Defaults are blank so
    # Phase-1-only callers (pull-only) don't need to set them.
    linear_team: str = ""        # team name or id — required for create
    linear_project: str = ""     # project name or id — required for create
    linear_archive_state: str = ""  # state name to apply on archive (first canceled-type)
    # workbook-team-name → Linear-label-name. Labels are how workbook
    # teams round-trip into Linear (Linear has no per-issue team field
    # at the granularity workbook needs). Empty map = team sync disabled.
    linear_team_label_map: dict = field(default_factory=dict)


@dataclass
class CpInputIssue:
    linear_id: str
    title: str
    state: str = ""
    estimate_days: Optional[float] = None
    percent: int = 0
    assignee: str = ""
    start_anchor: Optional[date] = None
    end_anchor: Optional[date] = None
    is_milestone: bool = False
    parent_linear_id: Optional[str] = None
    # Used by the pull path to wrap the Sheet's name cell as a HYPERLINK
    # formula. Ignored by the cp/adapter (cascade math doesn't care). Empty
    # string when the agent didn't supply one.
    linear_url: str = ""
    # Label names attached to the issue in Linear. Used by the team-sync
    # path: workbook Team is derived by intersecting this with
    # `CpInputConfig.linear_team_label_map.values()`. Order preserved so
    # the snapshot survives label-set normalization.
    labels: list = field(default_factory=list)


@dataclass
class CpInputEdge:
    from_linear_id: str
    to_linear_id: str
    type: str
    lag_days: int = 0


@dataclass
class CpInput:
    project: CpInputProject
    config: CpInputConfig
    issues: list[CpInputIssue]
    edges: list[CpInputEdge]


# ----- Output dataclasses ----------------------------------------------------


@dataclass
class CpOutputItem:
    linear_id: str
    wbs_id: str
    title: str
    start: Optional[date]
    end: Optional[date]
    slack_days: int
    on_critical_path: bool


@dataclass
class CpOutputWarning:
    linear_id: str
    kind: str
    message: str


@dataclass
class CpOutput:
    project: dict
    computed_at: str
    critical_path: list[CpOutputItem]
    cascade: list[CpOutputItem]
    warnings: list[CpOutputWarning]
    ok: bool = True


@dataclass
class CpError:
    error: str
    detail: str
    trace: list[str] = field(default_factory=list)
    ok: bool = False


# ----- Serde -----------------------------------------------------------------


def _require(d: dict, key: str, path: str) -> Any:
    if not isinstance(d, dict) or key not in d:
        raise ContractValidationError(f"missing required field: {path}.{key}")
    return d[key]


def _opt_date(v: Any) -> Optional[date]:
    if v is None or v == "":
        return None
    if isinstance(v, date):
        return v
    return date.fromisoformat(v)


def _validate_edge_type(t: str, path: str) -> str:
    if t not in _VALID_EDGE_TYPES:
        raise ContractValidationError(
            f"invalid edge type {t!r} at {path} (must be one of FS/SS/FF/SF)"
        )
    return t


def from_json(s: str) -> CpInput:
    """Parse a CpInput JSON document. Raises ContractValidationError on
    missing required fields or invalid enums. Unknown keys are ignored."""
    raw = json.loads(s)
    if not isinstance(raw, dict):
        raise ContractValidationError("top-level JSON must be an object")

    project_raw = _require(raw, "project", "$")
    project = CpInputProject(
        name=_require(project_raw, "name", "$.project"),
        source=_require(project_raw, "source", "$.project"),
        source_ref=project_raw.get("source_ref"),
    )

    config_raw = _require(raw, "config", "$")
    config = CpInputConfig(
        default_duration_days=int(
            _require(config_raw, "default_duration_days", "$.config")
        ),
        today=_opt_date(_require(config_raw, "today", "$.config")),
        estimate_to_days=config_raw.get("estimate_to_days") or {},
        linear_team=config_raw.get("linear_team", "") or "",
        linear_project=config_raw.get("linear_project", "") or "",
        linear_archive_state=config_raw.get("linear_archive_state", "") or "",
        linear_team_label_map=dict(config_raw.get("linear_team_label_map") or {}),
    )

    issues_raw = raw.get("issues", []) or []
    if not isinstance(issues_raw, list):
        raise ContractValidationError("$.issues must be an array")
    issues: list[CpInputIssue] = []
    for i, iss in enumerate(issues_raw):
        if not isinstance(iss, dict):
            raise ContractValidationError(f"$.issues[{i}] must be an object")
        path = f"$.issues[{i}]"
        issues.append(
            CpInputIssue(
                linear_id=_require(iss, "linear_id", path),
                title=_require(iss, "title", path),
                state=iss.get("state", "") or "",
                estimate_days=iss.get("estimate_days"),
                percent=int(iss.get("percent") or 0),
                assignee=iss.get("assignee", "") or "",
                start_anchor=_opt_date(iss.get("start_anchor")),
                end_anchor=_opt_date(iss.get("end_anchor")),
                is_milestone=bool(iss.get("is_milestone", False)),
                parent_linear_id=iss.get("parent_linear_id"),
                linear_url=iss.get("linear_url", "") or "",
                labels=list(iss.get("labels") or []),
            )
        )

    edges_raw = raw.get("edges", []) or []
    if not isinstance(edges_raw, list):
        raise ContractValidationError("$.edges must be an array")
    edges: list[CpInputEdge] = []
    for i, e in enumerate(edges_raw):
        if not isinstance(e, dict):
            raise ContractValidationError(f"$.edges[{i}] must be an object")
        path = f"$.edges[{i}]"
        edges.append(
            CpInputEdge(
                from_linear_id=_require(e, "from_linear_id", path),
                to_linear_id=_require(e, "to_linear_id", path),
                type=_validate_edge_type(_require(e, "type", path), path),
                lag_days=int(e.get("lag_days") or 0),
            )
        )

    return CpInput(project=project, config=config, issues=issues, edges=edges)


class _ContractJSONEncoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if isinstance(o, date):
            return o.isoformat()
        return super().default(o)


def to_json(o: Any) -> str:
    """Serialize any dataclass (CpInput / CpOutput / CpError / nested) to
    a JSON string. Dates render as ISO-8601 strings."""
    if hasattr(o, "__dataclass_fields__"):
        d = asdict(o)
    else:
        d = o
    return json.dumps(d, indent=2, cls=_ContractJSONEncoder)
