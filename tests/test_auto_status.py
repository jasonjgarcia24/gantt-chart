"""Tests for gantt_lib.auto_status — derive task status from pct + dates + preds."""
from __future__ import annotations

from datetime import date

from gantt_lib.auto_status import compute_status
from gantt_lib.model import Status, Task

TODAY = date(2026, 5, 13)
PAST = date(2026, 5, 1)
FUTURE = date(2026, 6, 1)


def _t(id, pct=0, start=None, predecessors="", status=""):
    return Task(id=id, level=1, name=f"t{id}",
                percent_complete=pct, start=start, predecessors=predecessors,
                status=status)


# ---------- the 3 explicit rules ----------

def test_pct_zero_yields_not_started():
    t = _t("1", pct=0)
    assert compute_status(t, {"1": t}, TODAY) == Status.NOT_STARTED


def test_pct_one_hundred_yields_done():
    t = _t("1", pct=100, predecessors="2FS")
    pred = _t("2", pct=50)  # incomplete pred
    # Done overrides everything else.
    assert compute_status(t, {"1": t, "2": pred}, TODAY) == Status.DONE


def test_blocked_when_started_and_predecessor_incomplete():
    pred = _t("1", pct=50)
    t = _t("2", pct=30, start=PAST, predecessors="1FS")
    assert compute_status(t, {"1": pred, "2": t}, TODAY) == Status.BLOCKED


# ---------- the In Progress default (implicit fourth case) ----------

def test_in_progress_when_started_and_predecessors_complete():
    pred = _t("1", pct=100)
    t = _t("2", pct=30, start=PAST, predecessors="1FS")
    assert compute_status(t, {"1": pred, "2": t}, TODAY) == Status.IN_PROGRESS


def test_in_progress_when_no_predecessors_and_started():
    t = _t("1", pct=30, start=PAST)
    assert compute_status(t, {"1": t}, TODAY) == Status.IN_PROGRESS


def test_in_progress_when_pct_above_zero_but_start_in_future():
    # Started early — no blocker check (start hasn't passed). Default In Progress.
    t = _t("1", pct=10, start=FUTURE)
    assert compute_status(t, {"1": t}, TODAY) == Status.IN_PROGRESS


# ---------- precedence + edge cases ----------

def test_pct_zero_overrides_blocker_check():
    # Even with incomplete predecessor and start passed, pct=0 → Not Started
    # (per Jason's explicit rule, not Blocked).
    pred = _t("1", pct=0)
    t = _t("2", pct=0, start=PAST, predecessors="1FS")
    assert compute_status(t, {"1": pred, "2": t}, TODAY) == Status.NOT_STARTED


def test_blocker_check_when_start_today_counts_as_passed():
    pred = _t("1", pct=50)
    t = _t("2", pct=20, start=TODAY, predecessors="1FS")
    assert compute_status(t, {"1": pred, "2": t}, TODAY) == Status.BLOCKED


def test_multiple_predecessors_blocks_if_any_incomplete():
    p1 = _t("1", pct=100)
    p2 = _t("2", pct=70)
    t = _t("3", pct=10, start=PAST, predecessors="1FS, 2FS")
    assert compute_status(t, {"1": p1, "2": p2, "3": t}, TODAY) == Status.BLOCKED


def test_multiple_predecessors_in_progress_if_all_complete():
    p1 = _t("1", pct=100)
    p2 = _t("2", pct=100)
    t = _t("3", pct=10, start=PAST, predecessors="1FS, 2FS")
    assert compute_status(t, {"1": p1, "2": p2, "3": t}, TODAY) == Status.IN_PROGRESS


def test_missing_predecessor_id_is_ignored_for_blocker_check():
    # If a predecessor id can't be resolved, don't crash — treat as not blocked.
    t = _t("1", pct=30, start=PAST, predecessors="9.9FS")
    assert compute_status(t, {"1": t}, TODAY) == Status.IN_PROGRESS


def test_milestone_done_at_full_pct():
    t = _t("M", pct=100)
    assert compute_status(t, {"M": t}, TODAY) == Status.DONE


# ---------- Cancelled preservation ----------


def test_cancelled_status_preserved_at_zero_percent():
    """A task pulled from Linear in canceled-type state lands in the
    workbook with status=Cancelled. Without rule-0 preservation,
    auto_status would rewrite it to Not Started (because pct=0), and
    the next Linear sync would push that back as un-archive."""
    t = _t("1", pct=0, status=Status.CANCELLED)
    assert compute_status(t, {"1": t}, TODAY) == Status.CANCELLED


def test_cancelled_status_preserved_even_with_predecessors_and_started_date():
    """Cancelled wins over the in-progress / blocked path entirely —
    the task is in a terminal state, not actively being worked."""
    pred = _t("1", pct=50)
    t = _t("2", pct=30, start=PAST, predecessors="1FS", status=Status.CANCELLED)
    assert compute_status(t, {"1": pred, "2": t}, TODAY) == Status.CANCELLED


def test_cancelled_status_included_in_enum():
    """Sanity check: Cancelled is in the canonical Status set."""
    assert Status.CANCELLED == "Cancelled"
    assert Status.CANCELLED in Status.all()
