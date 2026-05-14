"""Tests for the pure-Python helpers in gantt_lib.sheets.

Live I/O (gspread reads/writes) isn't covered here — the network paths are
exercised end-to-end by `gantt program new`, `gantt task add`, etc.
"""
from __future__ import annotations

from gantt_lib.model import Task
from gantt_lib.sheets import (
    _indented_row,
    compute_row_groups,
    find_child_insertion_row,
    indent_prefix,
)


def _t(id, level=1, name="task"):
    return Task(id=id, level=level, name=name)


# ---------- _indented_row ----------

def test_indented_row_l1_no_indent():
    t = _t("1", level=1, name="Define OKRs")
    row = _indented_row(t)
    assert row[2] == "Define OKRs"


def test_indented_row_l2_one_dash_indent():
    t = _t("1.1", level=2, name="Draft OKR doc")
    row = _indented_row(t)
    assert row[2] == "-- Draft OKR doc"


def test_indented_row_l3_four_dash_indent():
    t = _t("1.1.1", level=3, name="Outline structure")
    row = _indented_row(t)
    assert row[2] == "---- Outline structure"


def test_indent_prefix_per_level():
    assert indent_prefix(1) == ""
    assert indent_prefix(2) == "-- "
    assert indent_prefix(3) == "---- "
    assert indent_prefix(4) == "------ "
    assert indent_prefix(0) == ""  # defensive


def test_indented_row_passes_through_other_cols():
    t = Task(id="1", level=1, name="x", owner="Jason", team="PM", duration=5)
    row = _indented_row(t)
    assert row[0] == "1"
    assert row[3] == "Jason"
    assert row[4] == "PM"
    assert row[7] == "5"


# ---------- compute_row_groups ----------

def test_no_groups_when_all_top_level():
    pairs = [(_t("1"), 4), (_t("2"), 5), (_t("3"), 6)]
    assert compute_row_groups(pairs) == []


def test_single_group_under_one_anchor():
    # Task 1 (L1) at row 4 with two L2 children at rows 5, 6.
    # Group covers rows 5-6 (1-based) → 4-6 exclusive (0-based).
    pairs = [
        (_t("1", level=1), 4),
        (_t("1.1", level=2), 5),
        (_t("1.2", level=2), 6),
        (_t("2", level=1), 7),  # next L1, not a child
    ]
    assert compute_row_groups(pairs) == [(4, 6)]


def test_nested_groups_for_three_levels():
    # 1 (L1) at row 4
    # 1.1 (L2) at row 5
    # 1.1.1 (L3) at row 6
    # 1.2 (L2) at row 7
    # 2 (L1) at row 8
    pairs = [
        (_t("1", level=1), 4),
        (_t("1.1", level=2), 5),
        (_t("1.1.1", level=3), 6),
        (_t("1.2", level=2), 7),
        (_t("2", level=1), 8),
    ]
    groups = compute_row_groups(pairs)
    # Two groups: 1's descendants (rows 5-7), and 1.1's descendants (row 6).
    # Ordered by emission (anchor scan order): outer first, then nested.
    assert (4, 7) in groups  # rows 5-7 (1-based) = 4-7 exclusive (0-based)
    assert (5, 6) in groups  # row 6 only (0-based exclusive)
    assert len(groups) == 2


def test_no_group_for_anchor_with_no_descendants():
    # L1 followed immediately by another L1 — no children to group.
    pairs = [(_t("1", level=1), 4), (_t("2", level=1), 5)]
    assert compute_row_groups(pairs) == []


def test_two_separate_groups_under_two_anchors():
    pairs = [
        (_t("1", level=1), 4),
        (_t("1.1", level=2), 5),
        (_t("2", level=1), 6),
        (_t("2.1", level=2), 7),
        (_t("2.2", level=2), 8),
    ]
    groups = compute_row_groups(pairs)
    assert (4, 5) in groups   # 1's child at row 5 (0-based: 4-5 exclusive)
    assert (6, 8) in groups   # 2's children at rows 7-8 (0-based: 6-8 exclusive)
    assert len(groups) == 2


# ---------- find_child_insertion_row ----------
# Helper: pairs use 3-tuples (Task, row, raw_row); raw_row unused here.

def _p(id, row, level=1):
    return (_t(id, level=level), row, [])


def test_insertion_row_under_parent_with_no_children():
    pairs = [_p("1", 5), _p("2", 6)]
    # New child of 1 inserts directly after parent.
    assert find_child_insertion_row(pairs, "1") == 6


def test_insertion_row_after_last_existing_child():
    pairs = [
        _p("1", 5, level=1),
        _p("1.1", 6, level=2),
        _p("1.2", 7, level=2),
        _p("2", 8, level=1),
    ]
    # New child of 1 inserts after last child (1.2 at row 7).
    assert find_child_insertion_row(pairs, "1") == 8


def test_insertion_row_skips_grandchildren():
    pairs = [
        _p("1", 5, level=1),
        _p("1.1", 6, level=2),
        _p("1.1.1", 7, level=3),
        _p("1.2", 8, level=2),
        _p("2", 9, level=1),
    ]
    # New child of 1 lands after the whole subtree (rows 6-8).
    assert find_child_insertion_row(pairs, "1") == 9
    # New child of 1.1 lands after grandchild 1.1.1 only.
    assert find_child_insertion_row(pairs, "1.1") == 8


def test_insertion_row_for_deep_parent():
    pairs = [
        _p("1", 5, level=1),
        _p("1.1", 6, level=2),
        _p("1.1.1", 7, level=3),
    ]
    assert find_child_insertion_row(pairs, "1.1.1") == 8


def test_insertion_row_returns_none_for_missing_parent():
    pairs = [_p("1", 5)]
    assert find_child_insertion_row(pairs, "99") is None
