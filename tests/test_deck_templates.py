"""Tests for gantt_lib.deck.templates.

Verifies request-list shape, object-ID uniqueness within a section, and
empty-state behavior. Doesn't validate against the live Slides API — that
happens at the final manual acceptance gate.
"""
from __future__ import annotations

from datetime import date

import pytest

from gantt_lib.deck.data import (
    CPRow,
    ForwardMilestone,
    MilestoneSlipRow,
    PortfolioRow,
    RiskRow,
)
from gantt_lib.deck.templates import (
    _create_bullets_slide,
    _create_divider_slide,
    _create_image_slide,
    _create_table_slide,
    strategic_section_requests,
    tactical_section_requests,
)
from gantt_lib.model import Status, Task


TODAY = date(2026, 5, 14)


def _t(id, name="x", *, status=Status.NOT_STARTED, **kw):
    return Task(id=id, level=1, name=name, status=status, **kw)


# ---------- helpers ----------

def _request_kinds(reqs: list[dict]) -> list[str]:
    """Each Slides API request is a single-key dict; return the keys in order."""
    return [next(iter(r.keys())) for r in reqs]


def _all_object_ids(reqs: list[dict]) -> set[str]:
    """Collect every objectId mentioned across all requests in this section."""
    out: set[str] = set()
    for r in reqs:
        for body in r.values():
            if isinstance(body, dict) and "objectId" in body:
                out.add(body["objectId"])
    return out


# ---------- atomic helpers ----------

def test_create_divider_slide_has_expected_request_kinds():
    reqs = _create_divider_slide(
        "div-1", "Tactical", "TPM90", TODAY, "alice",
    )
    kinds = _request_kinds(reqs)
    # createSlide, updatePageProperties, then 2× (createShape + insertText + updateTextStyle)
    assert kinds[0] == "createSlide"
    assert kinds[1] == "updatePageProperties"
    assert kinds.count("createShape") == 2
    assert kinds.count("insertText") == 2
    assert kinds.count("updateTextStyle") == 2


def test_create_table_slide_renders_header_plus_data_rows():
    reqs = _create_table_slide(
        "tab-1", "Test Table",
        ["A", "B", "C"],
        [["1", "2", "3"], ["4", "5", "6"]],
    )
    kinds = _request_kinds(reqs)
    assert "createSlide" in kinds
    assert "createTable" in kinds
    # Header (3 cells × 2 reqs each = 6) + data (2 rows × 3 cols × 2 reqs = 12) = 18
    cell_inserts = sum(1 for r in reqs if "insertText" in r and "cellLocation" in r["insertText"])
    assert cell_inserts == 9  # 3 header + 6 data


def test_create_table_slide_empty_rows_renders_no_items_placeholder():
    reqs = _create_table_slide(
        "tab-1", "Empty Test",
        ["A", "B"],
        [],
    )
    # Should still render a table with 1 header + 1 data row
    create_table_req = next(r["createTable"] for r in reqs if "createTable" in r)
    assert create_table_req["rows"] == 2  # 1 header + 1 placeholder


def test_create_image_slide_has_create_image_request():
    reqs = _create_image_slide(
        "img-1", "Gantt Zoom", "https://drive.google.com/uc?id=abc",
    )
    kinds = _request_kinds(reqs)
    assert "createImage" in kinds
    img_req = next(r["createImage"] for r in reqs if "createImage" in r)
    assert img_req["url"] == "https://drive.google.com/uc?id=abc"


def test_create_bullets_slide_uses_paragraph_bullets():
    reqs = _create_bullets_slide(
        "bul-1", "Top Risks", ["Risk 1", "Risk 2"],
    )
    kinds = _request_kinds(reqs)
    assert "createParagraphBullets" in kinds


def test_create_bullets_slide_empty_renders_no_items_fallback():
    reqs = _create_bullets_slide("bul-1", "Empty", [])
    insert = next(r["insertText"] for r in reqs if "insertText" in r and r["insertText"]["objectId"].endswith("-bullets"))
    assert insert["text"] == "No items"


# ---------- tactical orchestrator ----------

def test_tactical_section_starts_with_divider():
    reqs, divider_id = tactical_section_requests(
        "TPM90", TODAY, "alice",
        this_week_tasks=[],
        blockers_data=[],
        cp_due_tasks=[],
        recent_tasks=[],
        gantt_image_url="https://drive.google.com/uc?id=test",
    )
    # First createSlide is the divider
    first_create = next(
        r["createSlide"] for r in reqs if "createSlide" in r
    )
    assert first_create["objectId"] == divider_id
    assert "divider-tactical-TPM90" in divider_id


def test_tactical_section_has_six_create_slide_requests():
    """1 divider + 5 content slides = 6 createSlide requests total."""
    reqs, _ = tactical_section_requests(
        "TPM90", TODAY, "alice",
        this_week_tasks=[],
        blockers_data=[],
        cp_due_tasks=[],
        recent_tasks=[],
        gantt_image_url="https://drive.google.com/uc?id=test",
    )
    create_slide_count = sum(1 for r in reqs if "createSlide" in r)
    assert create_slide_count == 6


def test_tactical_section_object_ids_are_unique():
    """No two createShape/createTable/createImage/createSlide use the same objectId."""
    reqs, _ = tactical_section_requests(
        "TPM90", TODAY, "alice",
        this_week_tasks=[_t("1", "Task A", start=TODAY, end=TODAY)],
        blockers_data=[(_t("2", "Task B", status=Status.BLOCKED), ["1"])],
        cp_due_tasks=[_t("3", "Task C", end=TODAY, owner="bob")],
        recent_tasks=[_t("4", "Task D", end=TODAY, status=Status.DONE)],
        gantt_image_url="https://drive.google.com/uc?id=test",
    )
    create_kinds = ("createSlide", "createShape", "createTable", "createImage")
    created_ids = []
    for r in reqs:
        for kind in create_kinds:
            if kind in r:
                created_ids.append(r[kind]["objectId"])
    assert len(created_ids) == len(set(created_ids))


def test_tactical_section_includes_image_slide():
    reqs, _ = tactical_section_requests(
        "TPM90", TODAY, "alice",
        this_week_tasks=[],
        blockers_data=[],
        cp_due_tasks=[],
        recent_tasks=[],
        gantt_image_url="https://drive.google.com/uc?id=test",
    )
    image_reqs = [r for r in reqs if "createImage" in r]
    assert len(image_reqs) == 1
    assert image_reqs[0]["createImage"]["url"] == "https://drive.google.com/uc?id=test"


# ---------- strategic orchestrator ----------

def _portfolio_row(program="A", **kw):
    defaults = dict(
        program=program, status="In Progress", percent_complete=50,
        current_cp_days=30, last_baseline_date=date(2026, 4, 1),
        last_baseline_actor="alice",
    )
    defaults.update(kw)
    return PortfolioRow(**defaults)


def _milestone_row(**kw):
    defaults = dict(
        program="A", wbs="1", name="Beta",
        baseline_end=date(2026, 6, 1), current_end=date(2026, 6, 5),
        slip=4,
    )
    defaults.update(kw)
    return MilestoneSlipRow(**defaults)


def _cp_row(**kw):
    defaults = dict(
        program="A", baseline_cp_days=20, current_cp_days=25,
        delta=5, num_tasks_on_cp=4,
    )
    defaults.update(kw)
    return CPRow(**defaults)


def _risk_row(**kw):
    defaults = dict(
        program="A", wbs="1", name="Eyepiece fab", status=Status.BLOCKED,
        slip_days=5, on_critical_path=True, impact_score=10.0,
    )
    defaults.update(kw)
    return RiskRow(**defaults)


def _forward_row(**kw):
    defaults = dict(
        program="A", wbs="M1", name="Beta launch",
        end=date(2026, 6, 1), days_from_today=18,
    )
    defaults.update(kw)
    return ForwardMilestone(**defaults)


def test_strategic_section_has_six_create_slide_requests():
    reqs, _ = strategic_section_requests(
        "Portfolio", TODAY, "alice",
        portfolio_rows=[],
        milestone_rows=[],
        cp_rows=[],
        risk_rows=[],
        forward_rows=[],
    )
    assert sum(1 for r in reqs if "createSlide" in r) == 6


def test_strategic_section_divider_uses_strategic_color():
    """Strategic divider sets a light-blue background; tactical sets light grey."""
    reqs, _ = strategic_section_requests(
        "Portfolio", TODAY, "alice",
        portfolio_rows=[],
        milestone_rows=[],
        cp_rows=[],
        risk_rows=[],
        forward_rows=[],
    )
    bg_reqs = [r for r in reqs if "updatePageProperties" in r]
    assert len(bg_reqs) == 1
    bg = bg_reqs[0]["updatePageProperties"]["pageProperties"]["pageBackgroundFill"]["solidFill"]["color"]["rgbColor"]
    # Strategic = light blue, so blue ≈ 1.0 and red < 1
    assert bg["blue"] > 0.9
    assert bg["red"] < 1.0


def test_strategic_section_top_risks_uses_bullets_slide():
    reqs, _ = strategic_section_requests(
        "Portfolio", TODAY, "alice",
        portfolio_rows=[],
        milestone_rows=[],
        cp_rows=[],
        risk_rows=[_risk_row(), _risk_row(wbs="2")],
        forward_rows=[],
    )
    bullet_reqs = [r for r in reqs if "createParagraphBullets" in r]
    assert len(bullet_reqs) == 1


def test_strategic_section_object_ids_are_unique():
    reqs, _ = strategic_section_requests(
        "Portfolio", TODAY, "alice",
        portfolio_rows=[_portfolio_row(), _portfolio_row(program="B")],
        milestone_rows=[_milestone_row()],
        cp_rows=[_cp_row()],
        risk_rows=[_risk_row()],
        forward_rows=[_forward_row()],
    )
    create_kinds = ("createSlide", "createShape", "createTable", "createImage")
    created_ids = []
    for r in reqs:
        for kind in create_kinds:
            if kind in r:
                created_ids.append(r[kind]["objectId"])
    assert len(created_ids) == len(set(created_ids))


def test_strategic_cp_table_flags_positive_delta_with_warning():
    """CP rows with delta > 0 should append ' ⚠' to the delta column for visual flag."""
    reqs, _ = strategic_section_requests(
        "Portfolio", TODAY, "alice",
        portfolio_rows=[],
        milestone_rows=[],
        cp_rows=[_cp_row(delta=5)],
        risk_rows=[],
        forward_rows=[],
    )
    # Find the CP table cells; one of them should have the ⚠ marker
    warning_cells = [
        r for r in reqs
        if "insertText" in r and "⚠" in r["insertText"].get("text", "")
    ]
    assert len(warning_cells) >= 1
