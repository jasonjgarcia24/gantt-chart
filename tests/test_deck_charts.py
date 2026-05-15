"""Smoke tests for gantt_lib.deck.charts — gantt PNG rendering.

Pixel-level visual regression is overkill for Phase 2; we verify the function
runs to completion, returns valid PNG bytes, and doesn't crash on edge cases
(empty input, milestone-only data, missing-date tasks). Visual quality is
reviewed manually at the final acceptance gate.
"""
from __future__ import annotations

from datetime import date, timedelta

from gantt_lib.deck.charts import render_gantt_zoom_png
from gantt_lib.model import Task


TODAY = date(2026, 5, 14)
PNG_HEADER = b"\x89PNG\r\n\x1a\n"


def _t(id, name="x", *, duration=1, start=None, end=None, milestone=False):
    return Task(
        id=id, level=1, name=name, duration=duration,
        start=start, end=end, milestone=milestone,
    )


def test_render_returns_png_bytes_with_valid_header():
    tasks = [
        _t("1", duration=3, start=TODAY, end=TODAY + timedelta(days=2)),
        _t("2", duration=5,
           start=TODAY + timedelta(days=3), end=TODAY + timedelta(days=7)),
    ]
    png = render_gantt_zoom_png(tasks, TODAY)
    assert png.startswith(PNG_HEADER)
    assert len(png) > 1000  # not a degenerate empty image


def test_render_empty_task_list_produces_placeholder_png():
    """Empty input should still produce a valid PNG (with a placeholder), not crash."""
    png = render_gantt_zoom_png([], TODAY)
    assert png.startswith(PNG_HEADER)


def test_render_with_critical_path_highlighting():
    tasks = [
        _t("1", duration=3, start=TODAY, end=TODAY + timedelta(days=2)),
        _t("2", duration=5,
           start=TODAY + timedelta(days=3), end=TODAY + timedelta(days=7)),
    ]
    png = render_gantt_zoom_png(tasks, TODAY, critical_ids={"1"})
    assert png.startswith(PNG_HEADER)


def test_render_handles_milestones_as_diamonds():
    tasks = [
        _t("1", duration=3, start=TODAY, end=TODAY + timedelta(days=2)),
        _t("M", duration=0,
           start=TODAY + timedelta(days=5), end=TODAY + timedelta(days=5),
           milestone=True),
    ]
    png = render_gantt_zoom_png(tasks, TODAY)
    assert png.startswith(PNG_HEADER)


def test_render_skips_tasks_with_missing_dates():
    """A task without start/end shouldn't crash the renderer — it's silently skipped."""
    tasks = [
        _t("1", duration=3, start=TODAY, end=TODAY + timedelta(days=2)),
        _t("2", duration=1),  # no dates
    ]
    png = render_gantt_zoom_png(tasks, TODAY)
    assert png.startswith(PNG_HEADER)


def test_render_with_title():
    tasks = [_t("1", duration=2, start=TODAY, end=TODAY + timedelta(days=1))]
    png = render_gantt_zoom_png(tasks, TODAY, title="TPM90 — 30-Day Window")
    assert png.startswith(PNG_HEADER)
