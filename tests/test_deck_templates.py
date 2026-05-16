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
    MAX_SCOPE_IN_ID,
    MAX_TABLE_ROWS_PER_SLIDE,
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


def test_create_table_slide_at_threshold_stays_single_slide():
    """Exactly MAX rows → still one slide, no title suffix, no slide-id suffix."""
    rows = [[str(i), "x", "y"] for i in range(MAX_TABLE_ROWS_PER_SLIDE)]
    reqs = _create_table_slide("tab-1", "Big Table", ["A", "B", "C"], rows)
    create_slides = [r for r in reqs if "createSlide" in r]
    assert len(create_slides) == 1
    assert create_slides[0]["createSlide"]["objectId"] == "tab-1"
    title_text = next(
        r["insertText"]["text"] for r in reqs
        if "insertText" in r and r["insertText"].get("objectId") == "tab-1-title"
    )
    assert "(" not in title_text  # no pagination suffix


def test_create_table_slide_overflow_paginates_with_suffix():
    """MAX+1 rows → 2 slides; title gets '(1/2)' / '(2/2)'; ids get '-1' / '-2'."""
    rows = [[str(i), "x", "y"] for i in range(MAX_TABLE_ROWS_PER_SLIDE + 1)]
    reqs = _create_table_slide("tab-1", "Overflowing", ["A", "B", "C"], rows)
    create_slides = [r["createSlide"] for r in reqs if "createSlide" in r]
    assert len(create_slides) == 2
    assert [s["objectId"] for s in create_slides] == ["tab-1-1", "tab-1-2"]
    titles = [
        r["insertText"]["text"] for r in reqs
        if "insertText" in r and r["insertText"].get("objectId", "").endswith("-title")
    ]
    assert titles == ["Overflowing (1/2)", "Overflowing (2/2)"]


def test_create_table_slide_paginated_distributes_data_rows_correctly():
    """Last page gets the remainder; first page gets exactly MAX."""
    n = MAX_TABLE_ROWS_PER_SLIDE + 3
    rows = [[str(i), "x", "y"] for i in range(n)]
    reqs = _create_table_slide("tab-1", "Distributed", ["A", "B", "C"], rows)
    create_tables = [r["createTable"] for r in reqs if "createTable" in r]
    # rows count includes the header (+1)
    assert create_tables[0]["rows"] == MAX_TABLE_ROWS_PER_SLIDE + 1
    assert create_tables[1]["rows"] == 3 + 1  # remainder + header


def test_create_table_slide_paginated_object_ids_unique():
    """Across all paginated slides, every objectId stays unique."""
    rows = [[str(i), "x", "y"] for i in range(MAX_TABLE_ROWS_PER_SLIDE * 3)]
    reqs = _create_table_slide("tab-1", "Big", ["A", "B", "C"], rows)
    ids = _all_object_ids(reqs)
    create_kinds = ("createSlide", "createShape", "createTable")
    created = [
        r[k]["objectId"] for r in reqs for k in create_kinds if k in r
    ]
    assert len(created) == len(set(created))
    assert all(i.startswith("tab-1") for i in ids)


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
    assert divider_id.startswith("div-T-TPM90-")


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


def test_tactical_back_to_back_calls_get_distinct_divider_ids():
    """Re-runs on the same day must produce different objectIds (issue #2).

    Slides batchUpdate rejects any slide objectId that already exists in the
    file, so two same-day appends would 400 if the divider id were purely
    deterministic. The per-call nonce prevents that.
    """
    a, _ = tactical_section_requests(
        "TPM90", TODAY, "alice",
        this_week_tasks=[], blockers_data=[],
        cp_due_tasks=[], recent_tasks=[],
        gantt_image_url="https://example/x",
    )
    b, _ = tactical_section_requests(
        "TPM90", TODAY, "alice",
        this_week_tasks=[], blockers_data=[],
        cp_due_tasks=[], recent_tasks=[],
        gantt_image_url="https://example/x",
    )
    a_ids = _all_object_ids(a)
    b_ids = _all_object_ids(b)
    assert a_ids.isdisjoint(b_ids)


def test_all_section_object_ids_fit_slides_50_char_cap():
    """Slides API rejects objectIds > 50 chars (issue #5).

    Worst case is the longest derived suffix on the longest scope name,
    paginated. Force pagination + a 14-char scope and check every id.
    """
    long_scope = "X" * MAX_SCOPE_IN_ID  # at the cap
    big_milestones = [
        MilestoneSlipRow(program=long_scope, wbs=str(i), name="M",
                         baseline_end=date(2026, 6, 1),
                         current_end=date(2026, 6, 5), slip=4)
        for i in range(40)  # forces 3 paginated milestone slides
    ]
    reqs, _ = strategic_section_requests(
        long_scope, TODAY, "alice",
        portfolio_rows=[_portfolio_row(program=long_scope)],
        milestone_rows=big_milestones,
        cp_rows=[_cp_row(program=long_scope)],
        risk_rows=[_risk_row(program=long_scope)],
        forward_rows=[_forward_row(program=long_scope)],
    )
    over = [oid for oid in _all_object_ids(reqs) if len(oid) > 50]
    assert not over, f"objectIds over 50 chars: {[(o, len(o)) for o in over]}"


def test_strategic_back_to_back_calls_get_distinct_divider_ids():
    a, _ = strategic_section_requests(
        "Portfolio", TODAY, "alice",
        portfolio_rows=[], milestone_rows=[],
        cp_rows=[], risk_rows=[], forward_rows=[],
    )
    b, _ = strategic_section_requests(
        "Portfolio", TODAY, "alice",
        portfolio_rows=[], milestone_rows=[],
        cp_rows=[], risk_rows=[], forward_rows=[],
    )
    assert _all_object_ids(a).isdisjoint(_all_object_ids(b))


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
